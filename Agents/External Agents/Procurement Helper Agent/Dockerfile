FROM registry.solace.lab/solace-agent-mesh-enterprise:1.97.2

USER root
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

# ----------------------------------------------------------------------
# Procurement Helper MCP server
# Deterministic Python tools that replace LLM-driven nodes in the
# procurement workflow (parse_articles, chunk_array, merge_results,
# render_report, render_failure_summary, validate_artifact_exists).
# ----------------------------------------------------------------------
WORKDIR /opt/procurement-helper-mcp

COPY pyproject.toml .
COPY src/ src/

RUN uv venv && \
    . .venv/bin/activate && \
    uv pip install -e .

# Smoke check: import the server module to catch syntax errors at build time.
RUN . .venv/bin/activate && \
    python -c "from procurement_helper_mcp.server import TOOL_HANDLERS; print(f'{len(TOOL_HANDLERS)} tools registered')"

# Run as non-root for defense in depth.
RUN groupadd -r appuser && \
    useradd -r -g appuser -d /app -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app /opt/procurement-helper-mcp

WORKDIR /app
USER appuser

CMD ["sam", "run"]
