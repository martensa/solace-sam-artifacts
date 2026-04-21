"""Browser lifecycle management with stealth configuration and domain-based context caching.

Adapted from the Web Scraper Agent's browser_manager.py. Stripped down for price
comparison use case: no download_resource, no image handling. Focused on page
navigation and content extraction for price data.
"""

from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger("price-comparison-mcp.browser")

# Detect host platform for consistent Sec-Ch-Ua-Platform header
_PLATFORM_MAP = {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}
_HOST_PLATFORM = _PLATFORM_MAP.get(platform.system(), "macOS")


# -- Stealth injection script -------------------------------------------------

STEALTH_JS = """
() => {
    // -- 1. Remove automation indicators ----------------------------------
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    delete window.__playwright;
    delete window.__pw_manual;

    // -- 2. Chrome environment emulation ----------------------------------
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

    Object.defineProperty(navigator, 'languages', {
        get: () => ['de-DE', 'de', 'en-US', 'en'],
    });

    if (!window.chrome) { window.chrome = {}; }
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
            if (parameter === 37445) return 'Intel Inc.';
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
            const imageData = ctx.getImageData(0, 0, 1, 1);
            imageData.data[0] = imageData.data[0] ^ 1;
            ctx.putImageData(imageData, 0, 0);
        }
        return originalToDataURL.apply(this, arguments);
    };

    // -- 6. Connection type -----------------------------------------------
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

    // -- 7. Battery API ---------------------------------------------------
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
    const originalNow = performance.now.bind(performance);
    performance.now = () => {
        return Math.round(originalNow() * 10) / 10;
    };
}
"""


# -- Human-like behavior helpers -----------------------------------------------

async def simulate_human_mouse(page: Page) -> None:
    """Perform a few realistic mouse movements on the page."""
    viewport = page.viewport_size
    if not viewport:
        return

    w, h = viewport["width"], viewport["height"]
    num_moves = random.randint(2, 3)
    cx, cy = w // 2, h // 2

    for _ in range(num_moves):
        tx = random.randint(int(w * 0.1), int(w * 0.9))
        ty = random.randint(int(h * 0.1), int(h * 0.7))
        steps = random.randint(6, 12)
        for step in range(steps):
            t = step / steps
            t = t * t * (3 - 2 * t)
            x = int(cx + (tx - cx) * t + random.gauss(0, 2))
            y = int(cy + (ty - cy) * t + random.gauss(0, 2))
            x = max(0, min(x, w - 1))
            y = max(0, min(y, h - 1))
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.02))
        cx, cy = tx, ty
        await asyncio.sleep(random.uniform(0.05, 0.2))


async def simulate_human_scroll(page: Page) -> None:
    """Perform a small natural scroll gesture."""
    scroll_y = random.randint(100, 300)
    await page.mouse.wheel(0, scroll_y)
    await asyncio.sleep(random.uniform(0.2, 0.5))


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
    """Manages a single browser instance with domain-based context caching."""

    def __init__(self, config: BrowserConfig) -> None:
        self.config = config
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, _CachedContext] = {}
        self._lock = asyncio.Lock()
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._domain_lock_creation = asyncio.Lock()
        self._last_request_time: dict[str, float] = {}

    async def get_page(self, url: str) -> Page:
        """Return a new stealth-configured Page for *url*."""
        domain = urlparse(url).netloc
        await self._rate_limit(domain)
        ctx = await self._get_or_create_context(domain)
        page = await ctx.context.new_page()
        return page

    async def health_check(self) -> dict[str, object]:
        """Return health status of the browser subsystem."""
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
        """Close all contexts and the browser."""
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
        """Launch the browser if not already running."""
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
            await self._evict_idle()

            cached = self._contexts.get(domain)
            if cached is not None:
                cached.touch()
                return cached

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

            if self.config.stealth_enabled:
                await context.add_init_script(STEALTH_JS)
                try:
                    if self.config.browser_type == "chromium":
                        for page in context.pages:
                            cdp = await context.new_cdp_session(page)
                            await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
                                "source": f"({STEALTH_JS})()",
                                "worldName": "stealth",
                            })
                            await cdp.detach()
                except PlaywrightError:
                    pass

            cached = _CachedContext(context, domain)
            self._contexts[domain] = cached
            logger.info("Created context for %s (viewport=%s, ua=%s...)",
                        domain, viewport, user_agent[:40])
            return cached

    async def _evict_idle(self) -> None:
        """Close contexts that have been idle longer than the timeout."""
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
        async with self._domain_lock_creation:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = asyncio.Lock()
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
                jitter = max(0, random.gauss(0.3, 0.1))
                wait = (delay - elapsed) + jitter
                logger.debug("Rate limiting %s: waiting %.1fs", domain, wait)
                await asyncio.sleep(wait)
            self._last_request_time[domain] = time.monotonic()
