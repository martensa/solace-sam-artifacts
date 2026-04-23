# ============================================================================
# Price Comparison Agent -- build / push / rollout
# ============================================================================
#
# Usage:
#   make build VERSION=1.0.0-alpha1
#   make push VERSION=1.0.0-alpha1      # also pushes :latest pointing at same digest
#   make release VERSION=1.0.0-alpha1   # build + push + rollout-restart
#   make rollout                        # just kick the pod, pulls :latest
#
# Defaults:
#   VERSION reads from pyproject.toml (project.version).
#   REGISTRY = registry.solace.lab
#   IMAGE    = sam-price-comparison-agent
#
# Notes:
#   - Docker credential helper (docker-credential-osxkeychain) must be on
#     PATH for `docker push`. The helper lives in Rancher Desktop's bundle.
#   - Deployment references :latest; bumping VERSION tags both :VERSION and
#     :latest from the same image digest.
# ============================================================================

DOCKER  ?= /Users/alexandermartens/.rd/bin/docker
KUBECTL ?= /Users/alexandermartens/.rd/bin/kubectl
REGISTRY ?= registry.solace.lab
IMAGE   ?= sam-price-comparison-agent

# Read VERSION from pyproject.toml unless overridden on the command line
VERSION ?= $(shell awk -F'"' '/^version = /{print $$2}' pyproject.toml)

NAMESPACE ?= sam-solace-lab-agents
DEPLOYMENT ?= sam-price-comparison-agent

# Credential-helper needs to be on PATH for docker push
CRED_HELPER_PATH := /Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin
DOCKER_CONFIG := /Users/alexandermartens/.docker

.PHONY: help version build push release rollout test logs verify-version

help:
	@echo "Targets:"
	@echo "  make version                        Show current VERSION"
	@echo "  make test                           Run pytest suite"
	@echo "  make build [VERSION=x.y.z]          Build image tagged :VERSION and :latest"
	@echo "  make push  [VERSION=x.y.z]          Push :VERSION and :latest"
	@echo "  make release [VERSION=x.y.z]        build + push + rollout-restart"
	@echo "  make rollout                        rollout-restart to pull :latest"
	@echo "  make logs                           tail the running pod"
	@echo "  make verify-version                 Confirm the running pod reports VERSION"
	@echo ""
	@echo "Registry cleanup (from repo root):"
	@echo "  ./scripts/registry-cleanup.sh $(IMAGE) 1.0.0 latest    # keep only final"

version:
	@echo "VERSION = $(VERSION)"
	@echo "IMAGE   = $(REGISTRY)/$(IMAGE):$(VERSION)"

test:
	@if [[ -d .venv ]]; then \
	  . .venv/bin/activate && pytest tests/ -v; \
	else \
	  echo "No .venv found. Run: uv venv && uv pip install -e .[dev] && playwright install chromium"; \
	  exit 1; \
	fi

build:
	@if [[ -z "$(VERSION)" ]]; then echo "ERROR: VERSION empty"; exit 1; fi
	DOCKER_CONFIG=$(DOCKER_CONFIG) $(DOCKER) build \
	  -t $(REGISTRY)/$(IMAGE):$(VERSION) \
	  -t $(REGISTRY)/$(IMAGE):latest .

push: build
	@PATH="$(CRED_HELPER_PATH):$$PATH" DOCKER_CONFIG=$(DOCKER_CONFIG) $(DOCKER) push $(REGISTRY)/$(IMAGE):$(VERSION)
	@PATH="$(CRED_HELPER_PATH):$$PATH" DOCKER_CONFIG=$(DOCKER_CONFIG) $(DOCKER) push $(REGISTRY)/$(IMAGE):latest

release: push rollout

rollout:
	$(KUBECTL) rollout restart deployment/$(DEPLOYMENT) -n $(NAMESPACE)
	$(KUBECTL) rollout status  deployment/$(DEPLOYMENT) -n $(NAMESPACE) --timeout=180s

logs:
	@POD=$$($(KUBECTL) get pods -n $(NAMESPACE) -l app=sam-external-agents \
	    -o jsonpath='{.items[?(@.spec.containers[0].name=="$(DEPLOYMENT)")].metadata.name}'); \
	  echo "Pod: $$POD"; \
	  $(KUBECTL) logs -n $(NAMESPACE) "$$POD" --tail=50

verify-version:
	@POD=$$($(KUBECTL) get pods -n $(NAMESPACE) -l app=sam-external-agents \
	    -o jsonpath='{.items[?(@.spec.containers[0].name=="$(DEPLOYMENT)")].metadata.name}'); \
	  echo "Pod: $$POD"; \
	  $(KUBECTL) logs -n $(NAMESPACE) "$$POD" --tail=200 \
	    | grep -E "MCP Server v[0-9]" | tail -1
