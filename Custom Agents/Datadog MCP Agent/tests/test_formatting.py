"""Tests for formatting utilities."""

from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_monitor, format_host,
    format_security_signal, format_container, format_team,
    count_by, counts_str,
)


def test_tool_response_basic() -> None:
    result = tool_response(summary="Found 5 items.", data=[1, 2, 3, 4, 5])
    assert result["summary"] == "Found 5 items."
    assert result["data"] == [1, 2, 3, 4, 5]
    assert "page" not in result


def test_tool_response_paginated() -> None:
    result = tool_response(summary="Page 1", data=[1], total_count=100, page=1, has_more=True)
    assert result["page"] == 1
    assert result["has_more"] is True
    assert result["total_count"] == 100


def test_error_response() -> None:
    result = error_response("Something went wrong")
    assert result["error"] is True
    assert "Something went wrong" in result["summary"]


def test_format_monitor() -> None:
    raw = {"id": 1, "name": "Test", "type": "metric alert", "query": "avg:cpu{*} > 90",
           "overall_state": "OK", "message": "Alert!", "tags": ["env:prod"],
           "created": "2024-01-01", "modified": "2024-02-01", "priority": 3}
    result = format_monitor(raw)
    assert result["id"] == 1
    assert result["status"] == "OK"


def test_format_host() -> None:
    raw = {"name": "web-01", "id": 123, "is_muted": False, "up": True, "apps": ["agent"],
           "tags_by_source": {"datadog": ["env:prod"]}, "last_reported_time": 1700000000,
           "meta": {"platform": "linux", "agent_version": "7.50"}}
    result = format_host(raw)
    assert result["status"] == "up"
    assert result["meta"]["platform"] == "linux"


def test_format_security_signal() -> None:
    raw = {"id": "sig-1", "attributes": {"title": "Brute force", "status": "high",
           "severity": "critical", "timestamp": "2024-03-01", "source": "cloudtrail",
           "tags": ["env:prod"], "attributes": {"workflow": {"rule": {"name": "bf_rule"}}}}}
    result = format_security_signal(raw)
    assert result["title"] == "Brute force"
    assert result["severity"] == "critical"


def test_format_container() -> None:
    raw = {"id": "c-1", "attributes": {"name": "nginx", "image_name": "nginx",
           "image_tag": "latest", "state": "running", "host": "web-01",
           "started": "2024-01-01", "tags": ["env:prod"]}}
    result = format_container(raw)
    assert result["name"] == "nginx"
    assert result["state"] == "running"


def test_format_team() -> None:
    raw = {"id": "t-1", "attributes": {"name": "Platform", "handle": "platform",
           "summary": "Platform team", "description": "Core platform", "user_count": 5, "link_count": 3}}
    result = format_team(raw)
    assert result["name"] == "Platform"
    assert result["member_count"] == 5


def test_count_by() -> None:
    items = [{"status": "ok"}, {"status": "error"}, {"status": "ok"}, {"status": "error"}, {"status": "error"}]
    result = count_by(items, "status")
    assert result == {"ok": 2, "error": 3}


def test_counts_str() -> None:
    assert counts_str({"ok": 2, "error": 3}) == "error: 3, ok: 2"
