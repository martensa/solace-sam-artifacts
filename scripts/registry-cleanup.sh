#!/usr/bin/env bash
# =============================================================================
# registry-cleanup.sh -- Delete all tags from a registry repository except
# the ones passed as "keep" arguments.
#
# Use cases:
#   - Clean up old dev/alpha tags after cutting a final release
#   - Enforce "only :<version> and :latest in registry" policy
#
# Auth:
#   - Uses the same docker-credential-osxkeychain entry that `docker push`
#     uses. No credentials in the script.
#
# Usage:
#   ./scripts/registry-cleanup.sh <repo> <keep_tag> [<keep_tag> ...]
#
# Examples:
#   # Delete everything except 1.0.0 and latest
#   ./scripts/registry-cleanup.sh sam-price-comparison-agent 1.0.0 latest
#
#   # Dry-run (list what would be deleted, do nothing)
#   DRY_RUN=1 ./scripts/registry-cleanup.sh sam-price-comparison-agent 1.0.0 latest
#
# NOTE: The Docker registry removes the tag immediately but the underlying
# blob layers remain on disk until `registry garbage-collect` runs on the
# registry server (see solace-lab-infrastructure/registry/README).
# =============================================================================
set -euo pipefail

REGISTRY_HOST="${REGISTRY_HOST:-registry.solace.lab}"
CREDENTIAL_HELPER="${CREDENTIAL_HELPER:-/Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin/docker-credential-osxkeychain}"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <repo> <keep_tag> [<keep_tag> ...]" >&2
  echo "Example: $0 sam-price-comparison-agent 1.0.0 latest" >&2
  exit 2
fi

REPO="$1"; shift
KEEP_TAGS=("$@")

# Pull credentials via the keychain helper
if [[ ! -x "$CREDENTIAL_HELPER" ]]; then
  echo "ERROR: credential helper not found at $CREDENTIAL_HELPER" >&2
  exit 1
fi
CREDS_JSON="$(echo "$REGISTRY_HOST" | "$CREDENTIAL_HELPER" get)"
REG_USER="$(echo "$CREDS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['Username'])")"
REG_PASS="$(echo "$CREDS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['Secret'])")"

API="https://${REGISTRY_HOST}/v2/${REPO}"

echo "Registry cleanup: ${REGISTRY_HOST}/${REPO}"
echo "Keep tags:        ${KEEP_TAGS[*]}"
[[ "${DRY_RUN:-0}" == "1" ]] && echo "DRY_RUN:          yes (no deletes will be sent)"
echo ""

# 1) List all tags
tags_json="$(curl -sk -u "$REG_USER:$REG_PASS" "${API}/tags/list")"
all_tags_py='
import json, sys
data = json.load(sys.stdin)
print("\n".join(data.get("tags") or []))
'
# Portable (macOS bash 3.2 has no mapfile)
ALL_TAGS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && ALL_TAGS+=("$line")
done < <(echo "$tags_json" | python3 -c "$all_tags_py")

if [[ ${#ALL_TAGS[@]} -eq 0 ]]; then
  echo "No tags found in $REPO. Nothing to do."
  exit 0
fi

# 2) Determine delete list
DELETE_TAGS=()
for t in "${ALL_TAGS[@]}"; do
  keep=0
  for k in "${KEEP_TAGS[@]}"; do
    [[ "$t" == "$k" ]] && keep=1 && break
  done
  [[ $keep -eq 0 ]] && DELETE_TAGS+=("$t")
done

if [[ ${#DELETE_TAGS[@]} -eq 0 ]]; then
  echo "Registry already clean: only kept tags present."
  exit 0
fi

echo "Will delete ${#DELETE_TAGS[@]} tag(s):"
for t in "${DELETE_TAGS[@]}"; do
  echo "  - $t"
done
echo ""

# 3) Resolve each tag to its digest, de-dup by digest, delete
# (portable: bash 3.2 has no associative arrays -- use a newline-separated string)
SEEN_DIGESTS=""
failed=0
for tag in "${DELETE_TAGS[@]}"; do
  # Get digest via HEAD
  digest="$(curl -skI -u "$REG_USER:$REG_PASS" \
    -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    -H "Accept: application/vnd.oci.image.manifest.v1+json" \
    -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
    -H "Accept: application/vnd.oci.image.index.v1+json" \
    "${API}/manifests/${tag}" \
    | grep -i '^docker-content-digest:' \
    | awk '{print $2}' | tr -d '\r\n' || true)"

  if [[ -z "$digest" ]]; then
    echo "  [skip] $tag -- could not resolve digest (already gone?)"
    continue
  fi

  # De-dup: a single digest can carry many tags; delete the manifest once.
  if echo "$SEEN_DIGESTS" | grep -Fxq "$digest"; then
    echo "  [dedup] $tag -> $digest (already deleted)"
    continue
  fi
  SEEN_DIGESTS="${SEEN_DIGESTS}${digest}"$'\n'

  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "  [dry-run] would DELETE $tag ($digest)"
    continue
  fi

  http_status="$(curl -sk -u "$REG_USER:$REG_PASS" -X DELETE \
    "${API}/manifests/${digest}" \
    -o /dev/null -w '%{http_code}')"
  case "$http_status" in
    202|204)
      echo "  [ok]    $tag deleted ($digest)"
      ;;
    404)
      echo "  [gone]  $tag (already absent)"
      ;;
    *)
      echo "  [FAIL]  $tag HTTP $http_status" >&2
      failed=$((failed + 1))
      ;;
  esac
done

echo ""
echo "Remaining tags:"
curl -sk -u "$REG_USER:$REG_PASS" "${API}/tags/list" \
  | python3 -c "import json,sys; print('\n'.join(sorted((json.load(sys.stdin).get('tags') or []))))" \
  | sed 's/^/  /'

if [[ $failed -gt 0 ]]; then
  echo ""
  echo "ERROR: $failed delete(s) failed." >&2
  exit 1
fi
