FROM localhost:5000/solace-agent-mesh-enterprise:latest

USER root
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

# Datadog MCP Server — 73 tools across the SRE operational lifecycle
WORKDIR /opt/datadog-mcp-server
COPY pyproject.toml README.md ./
COPY src/ src/
RUN uv venv && uv pip install .

WORKDIR /app
EXPOSE 8000
CMD ["sam", "run"]
