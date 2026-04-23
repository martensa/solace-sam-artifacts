#!/usr/bin/env bash
# =============================================================================
# render-secrets.sh -- Render all *-secret.yaml.template files using envsubst.
#
# Reads variables from the root-level .env file and substitutes ${VAR}
# placeholders in every *-secret.yaml.template into the corresponding
# *-secret.yaml output file.
#
# The shared secret template is rendered twice (once per target namespace).
# Every output file is gitignored -- only .template files are committed.
#
# Safety:
#   - Fails if .env is missing.
#   - Fails if any ${VAR} remains unsubstituted after rendering.
#   - Rendered files land in the same directory as their template.
# =============================================================================
set -euo pipefail

# Resolve repo root (parent of this script directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  cat <<'EOF' >&2
ERROR: .env not found at repo root.
Copy .env.example to .env and fill in real values:
  cp .env.example .env
  $EDITOR .env
EOF
  exit 1
fi

# Export every VAR=VALUE from .env into the environment for envsubst
set -a
# shellcheck disable=SC1091
source .env
set +a

# ensure envsubst is available
if ! command -v envsubst >/dev/null 2>&1; then
  echo "ERROR: envsubst not found. Install gettext (macOS: brew install gettext)." >&2
  exit 1
fi

render_template() {
  local template="$1"
  local output="$2"
  local ns_override="${3:-}"

  # Build a whitelist of variables we substitute so envsubst never touches
  # anything else (e.g. shell-like $foo inside comments). We enumerate every
  # variable currently exported from .env plus K8S_NAMESPACE for shared.
  local vars
  vars="$(awk -F= '/^[A-Z][A-Z0-9_]*=/ {printf "${%s} ", $1}' .env) \${K8S_NAMESPACE}"

  if [[ -n "$ns_override" ]]; then
    K8S_NAMESPACE="$ns_override" envsubst "$vars" < "$template" > "$output"
  else
    envsubst "$vars" < "$template" > "$output"
  fi

  # Safety: fail if any ${VAR} survived (typo or missing var in .env)
  if grep -E '\$\{[A-Z][A-Z0-9_]*\}' "$output" >/dev/null; then
    echo "ERROR: unsubstituted variables in $output:" >&2
    grep -nE '\$\{[A-Z][A-Z0-9_]*\}' "$output" | head -10 >&2
    rm -f "$output"
    exit 1
  fi
}

# -----------------------------------------------------------------------------
# Shared secret: one template, two renderings (one per namespace).
# Add namespaces here if new ones appear.
# -----------------------------------------------------------------------------
SHARED_TEMPLATE="deploy/shared/sam-shared-secret.yaml.template"
SHARED_NAMESPACES=("sam-solace-lab-agents" "sam-solace-lab-workflows")

if [[ -f "$SHARED_TEMPLATE" ]]; then
  for ns in "${SHARED_NAMESPACES[@]}"; do
    out="deploy/shared/sam-shared-secret.${ns}.yaml"
    render_template "$SHARED_TEMPLATE" "$out" "$ns"
    echo "  [shared ]  $out"
  done
fi

# -----------------------------------------------------------------------------
# Per-agent templates: any other *-secret.yaml.template in the repo.
# -----------------------------------------------------------------------------
rendered=0
while IFS= read -r -d '' template; do
  # skip the shared template (already handled)
  if [[ "$template" == "./$SHARED_TEMPLATE" ]] || [[ "$template" == "$SHARED_TEMPLATE" ]]; then
    continue
  fi
  output="${template%.template}"
  render_template "$template" "$output"
  echo "  [agent  ]  $output"
  rendered=$((rendered + 1))
done < <(find . -type f -name "*-secret.yaml.template" -not -path "./.git/*" -print0)

echo ""
echo "Rendered shared (${#SHARED_NAMESPACES[@]} namespaces) + $rendered agent secret file(s)."
