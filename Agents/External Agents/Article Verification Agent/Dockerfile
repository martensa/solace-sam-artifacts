FROM localhost:5000/solace-agent-mesh-enterprise:1.97.2

USER root
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

# Article Verification MCP server
WORKDIR /opt/article-verification-mcp
COPY src/article_verification_mcp_server.py .
COPY requirements.txt .
RUN uv venv && uv pip install -r requirements.txt
RUN chmod +x article_verification_mcp_server.py

WORKDIR /app
EXPOSE 8000
CMD ["sam", "run"]
