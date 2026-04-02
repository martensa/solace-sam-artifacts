FROM localhost:5000/solace-agent-mesh-enterprise:latest

USER root
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

# EAN Search MCP server
WORKDIR /opt/ean-search-mcp-server
COPY src/ean_search_mcp_server.py .
COPY requirements.txt .
RUN uv venv && uv pip install -r requirements.txt
RUN chmod +x ean_search_mcp_server.py

WORKDIR /app
EXPOSE 8000
CMD ["sam", "run"]
