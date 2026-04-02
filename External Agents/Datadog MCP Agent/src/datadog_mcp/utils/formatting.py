"""Response formatting utilities for LLM-friendly output."""

from __future__ import annotations

from typing import Any


# -- Response builders-------------------------------------------------


def tool_response(
    *,
    summary: str,
    data: Any = None,
    total_count: int | None = None,
    page: int | None = None,
    has_more: bool = False,
) -> dict[str, Any]:
    """Build a standardised tool response dict.

    Every tool returns this shape so LLMs get a consistent interface:
    - summary: short human-readable description of the result
    - data: structured payload
    - total_count / page / has_more: pagination info (optional)
    """
    result: dict[str, Any] = {"summary": summary}
    if data is not None:
        result["data"] = data
    if total_count is not None:
        result["total_count"] = total_count
    if page is not None:
        result["page"] = page
        result["has_more"] = has_more
    return result


def error_response(message: str) -> dict[str, Any]:
    """Build an error response."""
    return {"summary": f"Error: {message}", "error": True}


# -- Helpers-----------------------------------------------------------


def truncate(text: str | None, max_len: int = 300) -> str:
    """Truncate text with ellipsis indicator."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def count_by(items: list[dict], key: str) -> dict[str, int]:
    """Count items grouped by a key field."""
    counts: dict[str, int] = {}
    for item in items:
        val = str(item.get(key) or "unknown")
        counts[val] = counts.get(val, 0) + 1
    return counts


def counts_str(counts: dict[str, int]) -> str:
    """Format counts dict as 'key: N, key: N' string."""
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))


def build_aggregate_body(
    query: str,
    from_time: str,
    to_time: str,
    group_by: list[str] | None,
    compute_metric: str,
    compute_type: str,
) -> dict[str, Any]:
    """Build a standard Datadog analytics aggregate request body."""
    compute: list[dict[str, str]] = [{"aggregation": compute_type}]
    if compute_metric != "count":
        compute[0]["metric"] = compute_metric

    body: dict[str, Any] = {
        "filter": {"query": query, "from": from_time, "to": to_time},
        "compute": compute,
    }
    if group_by:
        body["group_by"] = [
            {
                "facet": f,
                "limit": 25,
                "sort": {"aggregation": compute_type, "order": "desc"},
            }
            for f in group_by
        ]
    return body


# -- Formatters--------------------------------------------------------
# Each formatter extracts the most relevant fields from a raw API object,
# keeping payloads small and LLM-friendly.


def format_monitor(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "type": m.get("type"),
        "query": m.get("query"),
        "status": m.get("overall_state"),
        "message": truncate(m.get("message"), 300),
        "tags": m.get("tags", []),
        "created": m.get("created"),
        "modified": m.get("modified"),
        "priority": m.get("priority"),
    }


def format_host(h: dict) -> dict:
    return {
        "name": h.get("name"),
        "id": h.get("id"),
        "is_muted": h.get("is_muted"),
        "status": "up" if h.get("up") else "down",
        "apps": h.get("apps", []),
        "tags_by_source": h.get("tags_by_source", {}),
        "last_reported": h.get("last_reported_time"),
        "meta": {
            "platform": h.get("meta", {}).get("platform"),
            "agent_version": h.get("meta", {}).get("agent_version"),
        },
    }


def format_event(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "title": e.get("title"),
        "text": truncate(e.get("text"), 500),
        "date_happened": e.get("date_happened"),
        "source": e.get("source"),
        "priority": e.get("priority"),
        "alert_type": e.get("alert_type"),
        "tags": e.get("tags", []),
    }


def format_event_v2(e: dict) -> dict:
    attrs = e.get("attributes", {})
    return {
        "id": e.get("id"),
        "title": attrs.get("title") or attrs.get("evt", {}).get("name"),
        "message": truncate(attrs.get("message"), 500),
        "timestamp": attrs.get("timestamp"),
        "tags": attrs.get("tags", []),
        "source": attrs.get("evt", {}).get("source"),
        "status": attrs.get("status"),
    }


def format_incident(i: dict) -> dict:
    attrs = i.get("attributes", {})
    commander = attrs.get("commander") or {}
    commander_data = commander.get("data") or {}
    commander_attrs = commander_data.get("attributes") or {}
    return {
        "id": i.get("id"),
        "title": attrs.get("title"),
        "status": attrs.get("state"),
        "severity": attrs.get("severity"),
        "created": attrs.get("created"),
        "modified": attrs.get("modified"),
        "commander": commander_attrs.get("name"),
        "customer_impact": attrs.get("customer_impact_scope"),
        "detected": attrs.get("detected"),
        "resolved": attrs.get("resolved"),
    }


def format_slo(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "type": s.get("type"),
        "description": truncate(s.get("description"), 300),
        "tags": s.get("tags", []),
        "thresholds": s.get("thresholds", []),
        "overall_status": s.get("overall_status", []),
        "created_at": s.get("created_at"),
        "modified_at": s.get("modified_at"),
    }


def format_downtime(d: dict) -> dict:
    attrs = d.get("attributes", {})
    schedule = attrs.get("schedule") or {}
    current = schedule.get("current_downtime") or {}
    return {
        "id": d.get("id"),
        "display_name": attrs.get("display_name"),
        "status": attrs.get("status"),
        "scope": attrs.get("monitor_identifier"),
        "message": truncate(attrs.get("message"), 300),
        "schedule": {
            "start": current.get("start"),
            "end": current.get("end"),
        },
        "canceled": attrs.get("canceled"),
        "created": attrs.get("created"),
        "modified": attrs.get("modified"),
    }


def format_synthetics_test(t: dict) -> dict:
    return {
        "public_id": t.get("public_id"),
        "name": t.get("name"),
        "type": t.get("type"),
        "subtype": t.get("subtype"),
        "status": t.get("status"),
        "tags": t.get("tags", []),
        "locations": t.get("locations", []),
        "message": truncate(t.get("message"), 300),
        "monitor_id": t.get("monitor_id"),
        "created_at": t.get("created_at"),
        "modified_at": t.get("modified_at"),
    }


def format_service(s: dict) -> dict:
    attrs = s.get("attributes", {})
    schema = attrs.get("schema", {})
    info = schema.get("info", {})
    return {
        "name": schema.get("dd-service") or info.get("dd-service"),
        "description": truncate(
            schema.get("description") or info.get("description"), 300
        ),
        "team": schema.get("team") or info.get("team"),
        "contacts": schema.get("contacts", []),
        "links": schema.get("links", []),
        "tags": schema.get("tags", []),
    }


def format_dashboard(d: dict) -> dict:
    return {
        "id": d.get("id"),
        "title": d.get("title"),
        "description": truncate(d.get("description"), 300),
        "url": d.get("url"),
        "layout_type": d.get("layout_type"),
        "author_handle": d.get("author_handle"),
        "created_at": d.get("created_at"),
        "modified_at": d.get("modified_at"),
    }


def format_notebook(n: dict) -> dict:
    return {
        "id": n.get("id"),
        "name": n.get("name"),
        "author": (n.get("author") or {}).get("handle"),
        "status": n.get("status"),
        "created": n.get("created"),
        "modified": n.get("modified"),
        "cells_count": len(n.get("cells", [])),
    }


def format_team(t: dict) -> dict:
    attrs = t.get("attributes", {})
    return {
        "id": t.get("id"),
        "name": attrs.get("name"),
        "handle": attrs.get("handle"),
        "summary": truncate(attrs.get("summary"), 300),
        "description": truncate(attrs.get("description"), 300),
        "member_count": attrs.get("user_count"),
        "link_count": attrs.get("link_count"),
    }


def format_workflow(w: dict) -> dict:
    attrs = w.get("attributes", {})
    return {
        "id": w.get("id"),
        "name": attrs.get("name"),
        "description": truncate(attrs.get("description"), 300),
        "state": attrs.get("state"),
        "created_at": attrs.get("created_at"),
        "modified_at": attrs.get("modified_at"),
    }


def format_container(c: dict) -> dict:
    attrs = c.get("attributes", {})
    return {
        "id": c.get("id"),
        "name": attrs.get("name"),
        "image_name": attrs.get("image_name"),
        "image_tag": attrs.get("image_tag"),
        "state": attrs.get("state"),
        "host": attrs.get("host"),
        "started": attrs.get("started"),
        "tags": attrs.get("tags", []),
    }


def format_container_image(ci: dict) -> dict:
    attrs = ci.get("attributes", {})
    return {
        "id": ci.get("id"),
        "name": attrs.get("name"),
        "tags": attrs.get("tags", []),
        "image_id": attrs.get("image_id"),
        "repo_digest": attrs.get("repo_digest"),
        "short_image": attrs.get("short_image"),
        "os_name": attrs.get("os_name"),
        "os_version": attrs.get("os_version"),
        "vulnerability_count": attrs.get("vulnerability_count"),
    }


def format_security_signal(s: dict) -> dict:
    attrs = s.get("attributes", {})
    return {
        "id": s.get("id"),
        "title": truncate(attrs.get("title") or attrs.get("message"), 200),
        "status": attrs.get("status"),
        "severity": attrs.get("severity"),
        "timestamp": attrs.get("timestamp"),
        "source": attrs.get("source"),
        "tags": attrs.get("tags", []),
        "rule_name": (
            attrs.get("workflow", {}).get("rule", {}).get("name")
        ),
    }


def format_error_tracking_issue(i: dict) -> dict:
    attrs = i.get("attributes", {})
    return {
        "id": i.get("id"),
        "title": attrs.get("title"),
        "status": attrs.get("status"),
        "level": attrs.get("level"),
        "first_seen": attrs.get("first_seen"),
        "last_seen": attrs.get("last_seen"),
        "count": attrs.get("count"),
        "service": attrs.get("service"),
        "env": attrs.get("env"),
        "platform": attrs.get("platform"),
    }
