"""Browser lifecycle management with stealth configuration and domain-based context caching.

Key design decisions for bypassing web security standards:

- **CORS bypass**: Downloads use ``context.request`` (Playwright API-level HTTP client)
  which operates outside the browser's same-origin policy. Falls back to in-page
  ``fetch()`` for resources already loaded by the page.
- **CSP bypass**: Stealth JS is injected via CDP ``Page.addScriptToEvaluateOnNewDocument``
  which executes *before* CSP is evaluated by the browser.
- **Mixed content**: ``ignore_https_errors=True`` + resources are downloaded via the
  API-level client which does not enforce mixed-content blocking.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import platform
import random
import time
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from .config import STEALTH_ARGS, USER_AGENTS, BrowserConfig

logger = logging.getLogger("web-scraper-mcp.browser")

# Detect host platform for consistent Sec-Ch-Ua-Platform header
_PLATFORM_MAP = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}
_HOST_PLATFORM = _PLATFORM_MAP.get(platform.system(), "macOS")


# -- Stealth injection script -------------------------------------------------
# Applied to every new context via add_init_script to run before any page JS.

STEALTH_JS = """
() => {
    // -- 1. Remove automation indicators ----------------------------------

    // Delete webdriver flag (primary detection vector)
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Remove Playwright-specific properties
    delete window.__playwright;
    delete window.__pw_manual;

    // -- 2. Chrome environment emulation ----------------------------------

    // Realistic plugins array (Chrome shows PDF/Native Client plugins)
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            plugins.length = 3;
            return plugins;
        },
    });

    // Consistent mimeTypes
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => {
            const mimes = [
                { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
                { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
            ];
            mimes.length = 2;
            return mimes;
        },
    });

    // Override languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['de-DE', 'de', 'en-US', 'en'],
    });

    // Chrome runtime object (missing = headless indicator)
    if (!window.chrome) {
        window.chrome = {};
    }
    window.chrome.runtime = window.chrome.runtime || {};
    window.chrome.loadTimes = window.chrome.loadTimes || function() {
        return {
            commitLoadTime: Date.now() / 1000,
            connectionInfo: 'h2',
            finishDocumentLoadTime: Date.now() / 1000 + 0.1,
            finishLoadTime: Date.now() / 1000 + 0.2,
            firstPaintAfterLoadTime: 0,
            firstPaintTime: Date.now() / 1000 + 0.05,
            navigationType: 'Other',
            npnNegotiatedProtocol: 'h2',
            requestTime: Date.now() / 1000 - 0.3,
            startLoadTime: Date.now() / 1000 - 0.2,
            wasAlternateProtocolAvailable: false,
            wasFetchedViaSpdy: true,
            wasNpnNegotiated: true,
        };
    };
    window.chrome.csi = window.chrome.csi || function() {
        return {
            onloadT: Date.now(),
            pageT: Date.now() - performance.timing.navigationStart,
            startE: performance.timing.navigationStart,
            tran: 15,
        };
    };

    // -- 3. Permissions API patch -----------------------------------------

    if (navigator.permissions && navigator.permissions.query) {
        const originalQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (parameters) => {
            if (parameters.name === 'notifications') {
                return Promise.resolve({ state: Notification.permission });
            }
            return originalQuery(parameters);
        };
    }

    // -- 4. WebGL fingerprint consistency ---------------------------------

    const patchWebGL = (proto) => {
        const original = proto.getParameter;
        proto.getParameter = function(parameter) {
            // UNMASKED_VENDOR_WEBGL
            if (parameter === 37445) return 'Intel Inc.';
            // UNMASKED_RENDERER_WEBGL
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return original.call(this, parameter);
        };
    };
    patchWebGL(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') {
        patchWebGL(WebGL2RenderingContext.prototype);
    }

    // -- 5. Canvas fingerprint noise --------------------------------------

    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        const ctx = this.getContext('2d');
        if (ctx && this.width > 16 && this.height > 16) {
            // Add imperceptible noise to canvas fingerprinting
            const imageData = ctx.getImageData(0, 0, 1, 1);
            imageData.data[0] = imageData.data[0] ^ 1;  // flip 1 bit
            ctx.putImageData(imageData, 0, 0);
        }
        return originalToDataURL.apply(this, arguments);
    };

    // -- 6. Connection type (headless often lacks this) -------------------

    if (!navigator.connection) {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false,
            }),
        });
    }

    // -- 7. Battery API (headless lacks this) -----------------------------

    if (!navigator.getBattery) {
        navigator.getBattery = () => Promise.resolve({
            charging: true,
            chargingTime: 0,
            dischargingTime: Infinity,
            level: 1.0,
            addEventListener: () => {},
        });
    }

    // -- 8. Timing consistency --------------------------------------------

    // Make performance.now() slightly less precise (fingerprint mitigation)
    const originalNow = performance.now.bind(performance);
    performance.now = () => {
        return Math.round(originalNow() * 10) / 10;  // Round to 0.1ms
    };
}
"""


# -- Human-like behavior helpers -----------------------------------------------

async def simulate_human_mouse(page: Page) -> None:
    """Perform a few realistic mouse movements on the page.

    Uses Bezier-like curves with jitter rather than linear paths,
    which is important for behavioral analysis evasion.
    """
    viewport = page.viewport_size
    if not viewport:
        return

    w, h = viewport["width"], viewport["height"]
    # Move mouse to 2-4 random points with natural curves
    num_moves = random.randint(2, 4)
    cx, cy = w // 2, h // 2  # start near center

    for _ in range(num_moves):
        # Target point (biased toward center/content area)
        tx = random.randint(int(w * 0.1), int(w * 0.9))
        ty = random.randint(int(h * 0.1), int(h * 0.7))

        # Move in small steps with jitter (Bezier approximation)
        steps = random.randint(8, 20)
        for step in range(steps):
            t = step / steps
            # Ease-in-out curve
            t = t * t * (3 - 2 * t)
            x = int(cx + (tx - cx) * t + random.gauss(0, 2))
            y = int(cy + (ty - cy) * t + random.gauss(0, 2))
            x = max(0, min(x, w - 1))
            y = max(0, min(y, h - 1))
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.025))

        cx, cy = tx, ty
        await asyncio.sleep(random.uniform(0.1, 0.4))


async def simulate_human_scroll(page: Page) -> None:
    """Perform a small natural scroll gesture."""
    scroll_y = random.randint(100, 400)
    await page.mouse.wheel(0, scroll_y)
    await asyncio.sleep(random.uniform(0.3, 0.8))


# -- Cached context wrapper ----------------------------------------------------

class _CachedContext:
    """Wrapper around a BrowserContext with last-used tracking."""

    __slots__ = ("context", "domain", "last_used")

    def __init__(self, context: BrowserContext, domain: str) -> None:
        self.context = context
        self.domain = domain
        self.last_used = time.monotonic()

    def touch(self) -> None:
        self.last_used = time.monotonic()


# -- Main browser manager -----------------------------------------------------

class BrowserManager:
    """Manages a single browser instance with domain-based context caching.

    - Reuses contexts per domain to keep cookies/sessions alive.
    - Evicts contexts after ``idle_timeout`` seconds of inactivity.
    - Caps the number of concurrent contexts at ``max_contexts``.
    - Applies stealth patches to every new context.
    - Provides human-like behavioral simulation helpers.
    """

    def __init__(self, config: BrowserConfig) -> None:
        self.config = config
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, _CachedContext] = {}
        self._lock = asyncio.Lock()
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._domain_lock_creation = asyncio.Lock()
        self._last_request_time: dict[str, float] = {}

    # -- Public API --------------------------------------------------------

    async def get_page(self, url: str) -> Page:
        """Return a new stealth-configured Page for *url*.

        The underlying BrowserContext is cached per domain so that cookies
        and sessions persist across calls to the same site.
        """
        domain = urlparse(url).netloc

        # Per-domain rate limiting
        await self._rate_limit(domain)

        ctx = await self._get_or_create_context(domain)
        page = await ctx.context.new_page()
        return page

    async def download_resource(self, page: Page, resource_url: str) -> DownloadResult | None:
        """Download any resource (image, PDF, video, document, etc.) bypassing CORS.

        This uses Playwright's API-level HTTP client (``context.request``) which
        operates **outside** the browser's same-origin policy, CSP, and mixed-content
        restrictions. It inherits cookies from the context for authenticated downloads.

        Falls back to in-page fetch() and then full page navigation as last resort.

        Returns a DownloadResult with bytes, mime_type, and filename, or None on failure.
        """
        # Method 1: Context-level API request (CORS-free, follows redirects, keeps cookies)
        try:
            # Set Referer to the page URL (many servers validate this)
            response = await page.context.request.get(
                resource_url,
                headers={
                    "Referer": page.url,
                    "Accept": "*/*",
                },
                max_redirects=10,
            )
            if response.ok:
                body = await response.body()
                content_type = response.headers.get("content-type", "")
                disposition = response.headers.get("content-disposition", "")
                filename = _extract_filename(resource_url, disposition)
                mime = _parse_content_type(content_type) or _guess_mime(resource_url)
                logger.debug("Downloaded %d bytes via context.request (CORS-free)", len(body))
                return DownloadResult(data=body, mime_type=mime, filename=filename)
            else:
                logger.debug("Context request returned %d for %s", response.status, resource_url[:80])
        except PlaywrightError as e:
            logger.debug("Context.request failed for %s: %s", resource_url[:80], e)

        # Method 2: Try to intercept from page's already-loaded resources via CDP.
        # We search the network log for the matching URL and use its requestId.
        try:
            cdp = await page.context.new_cdp_session(page)
            try:
                await cdp.send("Network.enable")
                # Try CDP-level interception first
                try:
                    log = await cdp.send("Network.getResponseBodyForInterception", {
                        "interceptionId": resource_url,
                    })
                except Exception:
                    log = None
                # Alternative: use page.evaluate to find cached response via fetch cache
                if log is None:
                    # Attempt to retrieve via fetch API within the page context
                    fetch_result = await page.evaluate("""
                        async (url) => {
                            try {
                                const resp = await fetch(url, {credentials: 'include'});
                                if (!resp.ok) return null;
                                const buf = await resp.arrayBuffer();
                                const bytes = new Uint8Array(buf);
                                let binary = '';
                                for (let i = 0; i < bytes.byteLength; i++) {
                                    binary += String.fromCharCode(bytes[i]);
                                }
                                return {
                                    body: btoa(binary),
                                    contentType: resp.headers.get('content-type') || '',
                                };
                            } catch { return null; }
                        }
                    """, resource_url)
                    if fetch_result and fetch_result.get("body"):
                        body = base64.b64decode(fetch_result["body"])
                        mime = _parse_content_type(fetch_result.get("contentType", "")) or _guess_mime(resource_url)
                        filename = _extract_filename(resource_url, "")
                        logger.debug("Retrieved %d bytes via in-page fetch (method 2)", len(body))
                        return DownloadResult(data=body, mime_type=mime, filename=filename)
            finally:
                await cdp.detach()
        except (PlaywrightError, Exception) as e:
            logger.debug("Method 2 (in-page fetch) failed for %s: %s", resource_url[:80], e)

        # Method 3: Navigate directly to the resource URL in a new page (last resort)
        # This works for resources that require full browser navigation (JS-rendered content)
        download_page = None
        try:
            download_page = await page.context.new_page()
            resp = await download_page.goto(resource_url, wait_until="load", timeout=self.config.navigation_timeout_ms)
            if resp and resp.ok:
                body = await resp.body()
                content_type = resp.headers.get("content-type", "")
                mime = _parse_content_type(content_type) or _guess_mime(resource_url)
                filename = _extract_filename(resource_url, "")
                logger.debug("Downloaded %d bytes via direct navigation", len(body))
                return DownloadResult(data=body, mime_type=mime, filename=filename)
        except PlaywrightError as e:
            logger.debug("Direct navigation download failed for %s: %s", resource_url[:80], e)
        finally:
            if download_page:
                try:
                    await download_page.close()
                except PlaywrightError:
                    pass

        return None

    async def health_check(self) -> dict[str, object]:
        """Return health status of the browser subsystem.

        Returns a dict with:
        - healthy (bool): True if browser can create pages.
        - browser_connected (bool): Whether browser process is alive.
        - active_contexts (int): Number of cached domain contexts.
        - error (str | None): Description if unhealthy.
        """
        status: dict[str, object] = {
            "healthy": False,
            "browser_connected": False,
            "active_contexts": len(self._contexts),
            "error": None,
        }
        try:
            async with self._lock:
                browser = await self._ensure_browser()
            status["browser_connected"] = browser.is_connected()
            if not browser.is_connected():
                status["error"] = "Browser process is not connected"
                return status

            # Smoke test: create and immediately close a page
            context = await browser.new_context()
            try:
                page = await context.new_page()
                await page.close()
            finally:
                await context.close()

            status["healthy"] = True
        except Exception as e:
            status["error"] = f"{type(e).__name__}: {e}"

        return status

    async def cleanup(self) -> None:
        """Close all contexts and the browser. Safe to call multiple times."""
        async with self._lock:
            for cached in list(self._contexts.values()):
                try:
                    await cached.context.close()
                except PlaywrightError:
                    pass
            self._contexts.clear()

            if self._browser:
                try:
                    await self._browser.close()
                except PlaywrightError:
                    pass
                self._browser = None

            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None

        logger.info("Browser manager cleaned up")

    # -- Internals ---------------------------------------------------------

    async def _ensure_browser(self) -> Browser:
        """Launch the browser if not already running. Must be called under self._lock."""
        if self._browser is None or not self._browser.is_connected():
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
            self._pw = await async_playwright().start()
            launcher = getattr(self._pw, self.config.browser_type)
            self._browser = await launcher.launch(
                headless=self.config.headless,
                args=STEALTH_ARGS,
            )
            logger.info("Browser launched: type=%s, headless=%s",
                        self.config.browser_type, self.config.headless)
        return self._browser

    async def _get_or_create_context(self, domain: str) -> _CachedContext:
        """Return a cached context or create a new one for *domain*."""
        async with self._lock:
            # Evict idle contexts
            await self._evict_idle()

            cached = self._contexts.get(domain)
            if cached is not None:
                cached.touch()
                return cached

            # Evict LRU if at capacity
            if len(self._contexts) >= self.config.max_contexts:
                lru_domain = min(self._contexts, key=lambda d: self._contexts[d].last_used)
                logger.info("Evicting context for %s (capacity=%d reached)", lru_domain, self.config.max_contexts)
                try:
                    await self._contexts[lru_domain].context.close()
                except PlaywrightError:
                    pass
                del self._contexts[lru_domain]

            browser = await self._ensure_browser()
            viewport = self.config.random_viewport()
            user_agent = random.choice(USER_AGENTS)

            context = await browser.new_context(
                viewport=viewport,
                user_agent=user_agent,
                locale=self.config.locale,
                timezone_id=self.config.timezone,
                java_script_enabled=True,
                ignore_https_errors=True,
                extra_http_headers={
                    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,image/apng,*/*;q=0.8"
                    ),
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Sec-Ch-Ua": '"Chromium";v="135", "Not-A.Brand";v="8", "Google Chrome";v="135"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": f'"{_HOST_PLATFORM}"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                },
            )

            # Register stealth init script on the context.
            # We use both add_init_script (Playwright-level) AND CDP-level injection.
            # CDP's Page.addScriptToEvaluateOnNewDocument executes BEFORE CSP is
            # evaluated, bypassing Content-Security-Policy restrictions.
            if self.config.stealth_enabled:
                await context.add_init_script(STEALTH_JS)
                # Also inject via CDP for CSP-hardened pages
                try:
                    # CDP session on context level -- applies to all pages
                    # Note: this requires Chromium; Firefox/WebKit don't support CDP
                    if self.config.browser_type == "chromium":
                        for page in context.pages:
                            cdp = await context.new_cdp_session(page)
                            await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
                                "source": f"({STEALTH_JS})()",
                                "worldName": "stealth",
                            })
                            await cdp.detach()
                except PlaywrightError:
                    pass  # Non-chromium or CDP not available -- init_script alone is fine

            cached = _CachedContext(context, domain)
            self._contexts[domain] = cached
            logger.info("Created context for %s (viewport=%s, ua=%s...)",
                        domain, viewport, user_agent[:40])
            return cached

    async def _evict_idle(self) -> None:
        """Close contexts that have been idle longer than the timeout.

        Must be called under self._lock. Closes synchronously to avoid
        race conditions where a new context could be created for the same
        domain while the old one is still closing.
        """
        now = time.monotonic()
        to_evict = [
            d for d, c in self._contexts.items()
            if now - c.last_used > self.config.context_idle_timeout
        ]
        for domain in to_evict:
            logger.info("Evicting idle context for %s (idle %.0fs)",
                        domain, now - self._contexts[domain].last_used)
            ctx = self._contexts.pop(domain)
            try:
                await ctx.context.close()
            except PlaywrightError:
                pass

    async def _rate_limit(self, domain: str) -> None:
        """Enforce per-domain rate limiting with jitter."""
        # Safely get or create domain lock (prevents race condition)
        async with self._domain_lock_creation:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = asyncio.Lock()
            # Periodic cleanup: remove locks for domains no longer cached
            if len(self._domain_locks) > self.config.max_contexts * 3:
                stale = [d for d in self._domain_locks if d not in self._contexts and d != domain]
                for d in stale:
                    del self._domain_locks[d]
                    self._last_request_time.pop(d, None)

        async with self._domain_locks[domain]:
            last = self._last_request_time.get(domain, 0.0)
            elapsed = time.monotonic() - last
            delay = self.config.per_domain_delay_seconds
            if elapsed < delay:
                # Add Gaussian jitter for less predictable timing
                jitter = max(0, random.gauss(0.5, 0.2))
                wait = (delay - elapsed) + jitter
                logger.debug("Rate limiting %s: waiting %.1fs", domain, wait)
                await asyncio.sleep(wait)
            self._last_request_time[domain] = time.monotonic()


# -- Download result and helpers -----------------------------------------------

class DownloadResult:
    """Container for a downloaded resource."""

    __slots__ = ("data", "mime_type", "filename")

    def __init__(self, data: bytes, mime_type: str, filename: str) -> None:
        self.data = data
        self.mime_type = mime_type
        self.filename = filename

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    @property
    def is_text(self) -> bool:
        return self.mime_type.startswith("text/") or self.mime_type in (
            "application/json", "application/xml", "application/javascript",
        )

    @property
    def size_str(self) -> str:
        size = len(self.data)
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"


def _parse_content_type(header: str) -> str | None:
    """Extract MIME type from Content-Type header, stripping charset etc."""
    if not header:
        return None
    return header.split(";")[0].strip().lower() or None


def _guess_mime(url: str) -> str:
    """Guess MIME type from URL path extension."""
    path = urlparse(url).path.lower().split("?")[0]
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def _extract_filename(url: str, content_disposition: str) -> str:
    """Extract filename from Content-Disposition header or URL path."""
    # Try Content-Disposition first
    if content_disposition:
        import re
        # RFC 6266: filename*=UTF-8''... or filename="..."
        match = re.search(r"filename\*?=['\"]?(?:UTF-8'')?([^;'\"]+)", content_disposition, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Fall back to URL path
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1] if "/" in path else path
    # Remove query parameters that leaked into the name
    name = name.split("?")[0]
    return name or "download"
