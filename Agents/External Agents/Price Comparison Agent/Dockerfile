FROM localhost:5000/solace-agent-mesh-enterprise:latest

LABEL maintainer="Procurement Engineering" \
      version="1.0.0" \
      description="Price Comparison Agent for Solace Agent Mesh"

USER root
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && pip install --upgrade uv

# Install Python dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy source code and package definition, install, then remove source
# to avoid duplicate tool registration (site-packages + src/)
COPY src/ ./src/
COPY pyproject.toml .
RUN pip install . --no-deps && rm -rf src/ pyproject.toml

WORKDIR /app
CMD ["sam", "run"]
