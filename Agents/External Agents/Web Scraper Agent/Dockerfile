FROM localhost:5000/solace-agent-mesh-enterprise:latest

USER root
WORKDIR /app

# System dependencies for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libxshmfence1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

WORKDIR /opt/web-scraper-mcp
COPY pyproject.toml .
COPY src/ src/

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/web-scraper-mcp/browsers

RUN uv venv && \
    . .venv/bin/activate && \
    uv pip install -e . && \
    playwright install chromium --with-deps

# Verify installation
RUN . .venv/bin/activate && python -c "from web_scraper_mcp.server import TOOLS; print(f'{len(TOOLS)} tools registered')"

# Create non-root user for runtime
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app /opt/web-scraper-mcp /opt/web-scraper-mcp/browsers

WORKDIR /app
EXPOSE 8000
USER appuser
CMD ["sam", "run"]
