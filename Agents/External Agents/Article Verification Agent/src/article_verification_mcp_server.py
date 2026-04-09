#!/usr/bin/env python3
"""
Article Verification MCP Server v2.1

Two tools:
1. check_article -- pre-filters non-product inputs (Nettoartikel, Brutto, etc.)
2. search_article -- performs web search via SearXNG (primary) with DuckDuckGo
   fallback. Returns results with titles/URLs/snippets so the LLM can extract
   manufacturer info.

Protocol: MCP JSON-RPC 2.0 over stdio
"""

import json
import logging
import os
import re
import sys
from urllib.parse import quote_plus
from urllib.request import urlopen, Request
from urllib.error import URLError

# -- logging (never stdout -- MCP uses it) ------------------------------------
LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
LOG_FILE = os.environ.get("MCP_LOG_FILE", "article_verification_mcp.log")
log = logging.getLogger("article-verification-mcp")
log.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_fh)
log.propagate = False


# -- configuration ------------------------------------------------------------
SEARXNG_URL = os.environ.get(
    "SEARXNG_URL",
    "http://searxng.sam-ent-k8s.svc.cluster.local:8080",
)
SEARCH_MAX_RESULTS = int(os.environ.get("SEARCH_MAX_RESULTS", "10"))


# -- pre-filter patterns (detect non-product inputs) --------------------------
_UNSPECIFIC_PATTERNS = [
    (re.compile(r"\bNettoartikel\b", re.IGNORECASE), "Net-price contract label (Nettoartikel)"),
    (re.compile(r"\bNettoangebotspreise?\b", re.IGNORECASE), "Net-price offer label (Nettoangebotspreise)"),
    (re.compile(r"\bBRUTTOARTIKEL\b", re.IGNORECASE), "Gross-price label (Bruttoartikel)"),
    (re.compile(r"\bNETTOARTIKEL\b"), "Net-price label (NETTOARTIKEL)"),
    (re.compile(r"\bNLAG\b", re.IGNORECASE), "NLAG pricing label"),
]

_CATEGORY_LABEL_PATTERN = re.compile(
    r"^[A-Za-z\u00C4\u00D6\u00DC\u00E4\u00F6\u00FC\u00DF\s\-]+(Nettoartikel|Brutto|NLAG|SPECIFIC\.\s*i?NETTOARTIKEL)",
    re.IGNORECASE,
)

_HAS_ARTICLE_NUMBER = re.compile(r"\d{4,}")


def _prefilter(text: str) -> dict | None:
    """Return an 'unspecific' result if input is obviously not a product."""
    stripped = text.strip()

    for pat, reason in _UNSPECIFIC_PATTERNS:
        if pat.search(stripped):
            return {
                "action": "skip",
                "is_specific": False,
                "unspecific_reason": reason,
                "original_input": stripped,
            }

    if _CATEGORY_LABEL_PATTERN.search(stripped):
        return {
            "action": "skip",
            "is_specific": False,
            "unspecific_reason": "Pricing category label, not a specific product",
            "original_input": stripped,
        }

    if re.search(r"\bBrutto\b", stripped, re.IGNORECASE):
        if not _HAS_ARTICLE_NUMBER.search(stripped):
            return {
                "action": "skip",
                "is_specific": False,
                "unspecific_reason": "Gross-price category label (Brutto), no specific article number",
                "original_input": stripped,
            }

    return None


def _extract_article_code(desc: str) -> str | None:
    """Extract the most likely article/order code."""
    m = re.search(r"\b([A-Z0-9]{2,}[\-\.\/][A-Z0-9\-\.\/]{2,})\b", desc)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{5,})\b", desc)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d+\.\d+[\w\.]*)\b", desc)
    if m:
        return m.group(1)
    return None


def _build_search_query(desc: str) -> str:
    """Build the optimal search query.

    Use the article code (or full description) as-is -- broad queries
    return more shop/distributor hits that clearly show manufacturer
    and product details.
    """
    code = _extract_article_code(desc)
    if code:
        context = desc.replace(code, "").strip(" ,-./")
        if context:
            return f"{code} {context}"
        return code
    return desc


# -- SearXNG JSON search (primary) -------------------------------------------
def _searxng_search(query: str, max_results: int = 10) -> list[dict]:
    """Search via SearXNG JSON API. Returns list of {title, url, snippet}."""
    try:
        url = f"{SEARXNG_URL}/search?q={quote_plus(query)}&format=json"
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))

        results = []
        for r in data.get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            })

        log.info("SearXNG search '%s': %d results", query, len(results))
        return results

    except (URLError, OSError) as e:
        log.warning("SearXNG search failed for '%s': %s", query, e)
        return []


# -- DuckDuckGo HTML search (fallback) ---------------------------------------
_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).strip()


def _ddg_search(query: str, max_results: int = 8) -> list[dict]:
    """Search DuckDuckGo HTML endpoint. Returns list of {title, url, snippet}."""
    try:
        data = f"q={quote_plus(query)}&kl=de-de".encode()
        req = Request(
            _DDG_URL,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        titles_urls = _RESULT_RE.findall(html)
        snippets = _SNIPPET_RE.findall(html)

        results = []
        for i, (url, title_html) in enumerate(titles_urls[:max_results]):
            snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
            title = _strip_tags(title_html)
            if "uddg=" in url:
                from urllib.parse import unquote, parse_qs, urlparse
                parsed = parse_qs(urlparse(url).query)
                url = unquote(parsed.get("uddg", [url])[0])
            results.append({"title": title, "url": url, "snippet": snippet})

        log.info("DDG search '%s': %d results", query, len(results))
        return results

    except (URLError, OSError) as e:
        log.error("DDG search failed for '%s': %s", query, e)
        return []


# -- unified search: SearXNG first, DDG fallback -----------------------------
def _web_search(query: str, max_results: int = 10) -> list[dict]:
    """Search using SearXNG (primary) with DuckDuckGo fallback."""
    results = _searxng_search(query, max_results)
    if results:
        return results

    log.info("Falling back to DuckDuckGo for '%s'", query)
    return _ddg_search(query, min(max_results, 8))


# -- MCP tool definitions -----------------------------------------------------
TOOLS = [
    {
        "name": "check_article",
        "description": (
            "Pre-check an article description. "
            "Detects non-product inputs (Nettoartikel, Bruttoartikel, pricing labels) "
            "and returns action='skip'. For real products, returns action='search' "
            "with an optimized search query and extracted article code."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_description": {
                    "type": "string",
                    "description": "The raw article description to check",
                },
            },
            "required": ["article_description"],
        },
    },
    {
        "name": "search_article",
        "description": (
            "Search the web for a B2B product article. Returns search results "
            "with titles, URLs and snippets from electrical distributors and shops. "
            "The titles typically contain the manufacturer name first, e.g. "
            "'Bega 50140.2K4 Wandleuchte' or 'OBO Bettermann ASM-C6A G'. "
            "Use this to identify the manufacturer and full product name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query (article code and/or description)",
                },
            },
            "required": ["query"],
        },
    },
]


def _handle_check_article(args: dict) -> dict:
    desc = args.get("article_description", "").strip()

    if not desc:
        return {"action": "skip", "is_specific": False,
                "unspecific_reason": "Empty input", "original_input": ""}

    prefilter_result = _prefilter(desc)
    if prefilter_result is not None:
        log.info("Pre-filtered as unspecific: %s", desc)
        return prefilter_result

    code = _extract_article_code(desc)
    query = _build_search_query(desc)

    log.info("Article OK, suggested query: %s", query)
    return {
        "action": "search",
        "is_specific": True,
        "original_input": desc,
        "article_code": code,
        "suggested_search_query": query,
    }


def _handle_search_article(args: dict) -> dict:
    query = args.get("query", "").strip()
    if not query:
        return {"error": "Empty query", "results": []}

    results = _web_search(query, max_results=SEARCH_MAX_RESULTS)

    if not results:
        log.warning("No results for query: %s", query)
        return {"query": query, "results": [], "result_count": 0}

    return {
        "query": query,
        "results": results,
        "result_count": len(results),
    }


HANDLERS = {
    "check_article": _handle_check_article,
    "search_article": _handle_search_article,
}


# -- MCP protocol -------------------------------------------------------------
def _init(rid, _p):
    return {"jsonrpc": "2.0", "id": rid, "result": {
        "protocolVersion": "2024-11-05",
        "serverInfo": {"name": "article-verification-mcp", "version": "2.1.0"},
        "capabilities": {"tools": {}},
    }}


def _list(rid, _p):
    return {"jsonrpc": "2.0", "id": rid, "result": {
        "tools": [{"name": t["name"], "description": t["description"],
                    "inputSchema": t["inputSchema"]} for t in TOOLS]
    }}


def _call(rid, p):
    name = p.get("name", "")
    args = p.get("arguments", {})
    handler = HANDLERS.get(name)
    if not handler:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
    try:
        result = handler(args)
        text = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text", "text": text}]}}
    except Exception as e:
        log.exception("Tool call failed: %s", name)
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32603, "message": str(e)}}


_DISPATCH = {"initialize": _init, "tools/list": _list, "tools/call": _call}


def main():
    log.info("Article Verification MCP server v2.1 started (SearXNG: %s)", SEARXNG_URL)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method", "")
        if method == "notifications/initialized" or rid is None:
            continue
        handler = _DISPATCH.get(method)
        resp = handler(rid, req.get("params", {})) if handler else {
            "jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}
        sys.stdout.write(json.dumps(resp, separators=(",", ":"), ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
