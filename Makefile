# ============================================================================
# Solace Agent Mesh Artifacts -- convenience targets
# ============================================================================

KUBECTL ?= /Users/alexandermartens/.rd/bin/kubectl

.PHONY: help secrets apply-secrets clean-secrets verify-secrets

help:
	@echo "Targets:"
	@echo "  secrets         Render all *-secret.yaml files from templates + .env"
	@echo "  apply-secrets   kubectl apply all rendered secret YAMLs"
	@echo "  verify-secrets  Render, then check every rendered file parses as YAML"
	@echo "  clean-secrets   Delete all rendered *-secret.yaml files (templates stay)"
	@echo ""
	@echo "Quick start:"
	@echo "  cp .env.example .env && \$$EDITOR .env"
	@echo "  make secrets"
	@echo "  make apply-secrets"

secrets:
	@./scripts/render-secrets.sh

verify-secrets: secrets
	@echo "Validating YAML syntax..."
	@python3 -c 'import yaml, glob, sys; \
bad=[f for f in glob.glob("**/*-secret.yaml",recursive=True)+glob.glob("**/*-secret.*.yaml",recursive=True) \
     if not f.endswith(".template") \
     and not (yaml.safe_load(open(f)) or True)]; \
sys.exit(1 if bad else 0)' && \
	  echo "All rendered secrets parse as valid YAML."

apply-secrets: secrets
	@echo "Applying all rendered secret YAMLs..."
	@find . -type f \( -name "*-secret.yaml" -o -name "*-secret.*.yaml" \) \
	  -not -path "./.git/*" -not -name "*.template" -print0 | \
	  xargs -0 -I{} $(KUBECTL) apply -f "{}"

clean-secrets:
	@echo "Deleting rendered *-secret.yaml files (templates preserved)..."
	@find . -type f -name "*-secret.yaml" -not -path "./.git/*" -not -name "*.template" -delete
	@find . -type f -name "*-secret.*.yaml" -not -path "./.git/*" -not -name "*.template" -delete
