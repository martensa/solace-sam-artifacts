FROM localhost:5000/solace-agent-mesh-enterprise:latest

USER root
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

# MCP server + OpenAPI specs
WORKDIR /opt/solace-broker-mcp-server
COPY solace_monitoring_mcp_server.py .
COPY semp-v2-swagger-monitor.json .
COPY semp-v2-swagger-config.json .
COPY requirements-mcp-server.txt requirements.txt
RUN uv venv && uv pip install -r requirements.txt
RUN chmod +x solace_monitoring_mcp_server.py

WORKDIR /app
EXPOSE 8000
CMD ["sam", "run"]
