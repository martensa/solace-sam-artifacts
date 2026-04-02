FROM python:3.12-slim

USER root
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

# Install solace-agent-mesh using uv
RUN uv pip install --system solace-agent-mesh

RUN sam plugin add sam-geo-information --plugin sam-geo-information

# Expose default port (adjust if needed)
EXPOSE 8000

# Run the agent mesh
CMD ["sam", "run"]