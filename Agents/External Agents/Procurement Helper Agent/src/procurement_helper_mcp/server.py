"""ProcurementHelperMCP -- JSON-RPC 2.0 MCP server exposing six
deterministic tools that replace LLM-driven steps in the procurement
workflow.

Tools:
  - parse_articles_lines     -> tools/parse_articles.py
  - chunk_array              -> tools/chunk_array.py
  - validate_artifact_exists -> tools/validate_artifact.py
  - merge_procurement_data   -> tools/merge_data.py
  - render_procurement_report -> tools/render_report.py
  - render_failure_summary   -> tools/render_failure.py

Protocol: JSON-RPC 2.0 over stdio with newline-delimited JSON framing,
matching the pattern used by the other procurement-stack MCP servers
(WebScraper, EAN, Article Verification).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Callable

from .tools.build_report import build_procurement_report
from .tools.chunk_array import chunk_array
from .tools.merge_data import merge_procurement_data
from .tools.parse_articles import parse_articles
from .tools.render_failure import render_failure_summary
from .tools.render_report import render_procurement_report
from .tools.validate_artifact import validate_artifact_exists

# ---------------- logging ----------------

LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
LOG_FILE = os.environ.get("MCP_LOG_FILE", "procurement_helper_mcp.log")

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger("procurement-helper-mcp.server")

# ---------------- tool registry ----------------

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "parse_articles_lines": parse_articles,
    "chunk_array": chunk_array,
    "validate_artifact_exists": validate_artifact_exists,
    "merge_procurement_data": merge_procurement_data,
    "render_procurement_report": render_procurement_report,
    "build_procurement_report": build_procurement_report,
    "render_failure_summary": render_failure_summary,
}

TOOL_DEFS = [
    {
        "name": "parse_articles_lines",
        "description": (
            "Parse a free-form multi-line article list into a JSON array. "
            "Each non-empty line becomes one item with auto-numbered position "
            "and an is_b2b_netto flag detected via case-insensitive regex on "
            "Nettoartikel / Nettoangebotspreise / Nettoangebotsartikel / "
            "BRUTTOARTIKEL / NLAG. Pure regex; deterministic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "articles_text": {
                    "type": "string",
                    "description": "Multi-line article list, one item per line.",
                }
            },
            "required": ["articles_text"],
        },
    },
    {
        "name": "chunk_array",
        "description": (
            "Split a JSON array into fixed-size sub-arrays. Used to chunk "
            "items for the PriceComparisonAgent's 25-item batch limit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "description": "Array to chunk."},
                "chunk_size": {
                    "type": "integer",
                    "description": "Maximum items per chunk (default 25).",
                    "minimum": 1,
                    "default": 25,
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "validate_artifact_exists",
        "description": (
            "Verify that an artifact filename emitted by an upstream LLM "
            "agent ACTUALLY exists in S3. Catches hallucinated filenames "
            "by performing an HTTP HEAD on the storage layer. The LLM "
            "cannot fool S3."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "app_name": {"type": "string"},
                "user_id": {"type": "string"},
                "session_id": {"type": "string"},
                "version": {"type": "integer", "default": 0},
            },
            "required": ["filename", "app_name", "user_id", "session_id"],
        },
    },
    {
        "name": "merge_procurement_data",
        "description": (
            "Join the four upstream phase outputs (verify, ean, image, "
            "price) onto the canonical parse_articles array BY INDEX, "
            "apply offer-quality gates, compute summary counts, surface "
            "anomalies. Pure Python; replaces the LLM merge_results node."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "parse_articles": {"type": "array"},
                "verify_articles": {"type": "array"},
                "verify_eans": {"type": "array"},
                "search_images": {"type": "array"},
                "search_prices": {"type": "array"},
            },
            "required": ["parse_articles"],
        },
    },
    {
        "name": "render_procurement_report",
        "description": (
            "Render a German Markdown procurement brief from the merged "
            "procurement object. Jinja2 template; deterministic structure: "
            "every position gets the exact same layout, image embed line "
            "is never omitted, totals are correct."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "merged_data": {
                    "type": "object",
                    "description": (
                        "Output of merge_procurement_data: object with "
                        "summary, items, anomalies."
                    ),
                }
            },
            "required": ["merged_data"],
        },
    },
    {
        "name": "build_procurement_report",
        "description": (
            "Single deterministic call combining merge_procurement_data "
            "(canonical join + heuristic image-hallucination guard + "
            "offer-quality gate) and render_procurement_report (Jinja2 "
            "Markdown). Use this from the workflow as the SINGLE "
            "post-pipeline aggregation step. Eliminates the separate "
            "validate-image map node and the two-step merge -> render "
            "pattern; collapses all deterministic post-processing into "
            "one HelperAgent invocation. Returns markdown + structured "
            "summary/items/anomalies for output_mapping."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "parse_articles": {"type": "array"},
                "verify_articles": {"type": "array"},
                "verify_eans": {"type": "array"},
                "search_images": {"type": "array"},
                "search_prices": {"type": "array"},
            },
            "required": ["parse_articles"],
        },
    },
    {
        "name": "render_failure_summary",
        "description": (
            "Render a partial-status German Markdown report when the "
            "workflow aborts mid-run. Jinja2 template; honest about which "
            "phases ran and which did not."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "parse_articles": {"type": "array"},
                "verify_articles": {"type": "array"},
                "verify_eans": {"type": "array"},
                "search_images": {"type": "array"},
                "search_prices": {"type": "array"},
                "failure_node": {"type": "string"},
                "failure_message": {"type": "string"},
            },
        },
    },
]

# ---------------- JSON-RPC plumbing ----------------


def _rpc_response(id_: Any, result: Any | None = None, error: dict | None = None) -> dict:
    if error is not None:
        return {"jsonrpc": "2.0", "id": id_, "error": error}
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(code: int, message: str) -> dict:
    return {"code": code, "message": message}


def _format_tool_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a Python tool dict in the MCP `content` envelope (one
    JSON-encoded text part). Keeping the response compact and JSON
    ensures upstream agents parse it deterministically.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        ]
    }


async def _handle_message(msg: dict) -> dict | None:
    method = msg.get("method")
    params = msg.get("params") or {}
    msg_id = msg.get("id")

    if method == "initialize":
        return _rpc_response(
            msg_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "procurement-helper-mcp", "version": "1.0.0"},
            },
        )

    if method == "notifications/initialized":
        return None  # one-way notification

    if method == "tools/list":
        return _rpc_response(msg_id, {"tools": TOOL_DEFS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _rpc_response(
                msg_id, error=_err(-32601, f"Unknown tool: {name}")
            )
        try:
            logger.info("Tool call: %s", name)
            result = handler(arguments)
            return _rpc_response(msg_id, _format_tool_response(result))
        except Exception as e:
            logger.exception("Tool '%s' raised", name)
            return _rpc_response(
                msg_id,
                error=_err(-32000, f"Tool '{name}' failed: {type(e).__name__}: {e}"),
            )

    return _rpc_response(msg_id, error=_err(-32601, f"Method not found: {method}"))


async def _run_stdio() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    writer_transport, _ = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, _, None, loop)

    logger.info(
        "ProcurementHelperMCP stdio server starting (tools: %s)",
        sorted(TOOL_HANDLERS.keys()),
    )

    while True:
        line = await reader.readline()
        if not line:
            logger.info("stdin closed; shutting down")
            return
        try:
            msg = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON on stdin: %s", e)
            continue

        response = await _handle_message(msg)
        if response is None:
            continue
        out = json.dumps(response, ensure_ascii=False) + "\n"
        writer.write(out.encode("utf-8"))
        await writer.drain()


def main() -> None:
    try:
        asyncio.run(_run_stdio())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
