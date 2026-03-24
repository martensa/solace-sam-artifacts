"""Datadog Synthetics tools  - ALERT + RESPOND stages."""

from __future__ import annotations

from typing import Any

from datadog_mcp.client import DatadogClient
from datadog_mcp.utils.formatting import (
    tool_response, error_response, format_synthetics_test, count_by, counts_str,
)
from datadog_mcp.utils.pagination import paginate_list


def register(mcp, dd: DatadogClient) -> None:  # noqa: ANN001
    """Register synthetics tools on the MCP server."""

    @mcp.tool()
    async def list_synthetics_tests(
        test_type: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """List Datadog Synthetic monitoring tests.

        Args:
            test_type: Filter by type: "api", "browser", or "" for all.
            page: Page number (default 1).
            page_size: Results per page (default 25).

        Returns:
            Synthetic tests with status, type, and locations.
        """
        try:
            data = await dd.get("/api/v1/synthetics/tests")
        except Exception as e:
            return error_response(f"Failed to list synthetics tests: {e}")

        tests = data.get("tests", [])
        if test_type:
            tests = [t for t in tests if t.get("type") == test_type]

        formatted = [format_synthetics_test(t) for t in tests]
        page_items, total, has_more = paginate_list(formatted, page, page_size)
        status_str = counts_str(count_by(formatted, "status"))

        return tool_response(
            summary=f"Found {total} synthetic tests. Status: {status_str}.",
            data=page_items, total_count=total, page=page, has_more=has_more,
        )

    @mcp.tool()
    async def get_synthetics_test(public_id: str) -> dict[str, Any]:
        """Get detailed configuration of a specific synthetic test.

        Args:
            public_id: The public ID of the test (e.g. "abc-def-ghi").

        Returns:
            Full test config including assertions, request settings, and locations.
        """
        try:
            data = await dd.get(f"/api/v1/synthetics/tests/{public_id}")
        except Exception as e:
            return error_response(f"Failed to get test {public_id}: {e}")

        formatted = format_synthetics_test(data)
        formatted["config"] = data.get("config", {})
        formatted["options"] = data.get("options", {})

        return tool_response(
            summary=f"Synthetic test '{formatted['name']}' ({formatted['type']}): status={formatted['status']}.",
            data=formatted,
        )

    @mcp.tool()
    async def get_synthetics_results(
        public_id: str,
    ) -> dict[str, Any]:
        """Get recent results for a synthetic test.

        Args:
            public_id: The public ID of the test.

        Returns:
            Recent test results with pass/fail status and response times.
        """
        try:
            data = await dd.get(f"/api/v1/synthetics/tests/{public_id}/results")
        except Exception as e:
            return error_response(f"Failed to get results for test {public_id}: {e}")

        results = data.get("results", [])
        formatted = [{
            "result_id": r.get("result_id"),
            "status": r.get("status"),
            "check_time": r.get("check_time"),
            "probe_dc": r.get("dc_id"),
            "response_time": r.get("result", {}).get("timings", {}).get("total"),
        } for r in results[:50]]

        passed = sum(1 for r in formatted if r.get("status") == 0)
        return tool_response(
            summary=f"Test {public_id}: {len(formatted)} results  - {passed} passed, {len(formatted) - passed} failed.",
            data=formatted, total_count=len(formatted),
        )

    @mcp.tool()
    async def trigger_synthetics_test(
        public_ids: list[str],
    ) -> dict[str, Any]:
        """Trigger one or more synthetic tests to run immediately.

        Args:
            public_ids: Test public IDs to trigger (e.g. ["abc-def-ghi"]).

        Returns:
            Triggered test execution details.
        """
        body = {"tests": [{"public_id": pid} for pid in public_ids]}

        try:
            data = await dd.post("/api/v1/synthetics/tests/trigger", body=body)
        except Exception as e:
            return error_response(f"Failed to trigger tests: {e}")

        triggered = data.get("triggered_check_ids", [])
        return tool_response(
            summary=f"Triggered {len(triggered)} synthetic test(s): {', '.join(str(t) for t in triggered[:10])}.",
            data={"triggered_ids": triggered, "results": data.get("results", [])},
        )
