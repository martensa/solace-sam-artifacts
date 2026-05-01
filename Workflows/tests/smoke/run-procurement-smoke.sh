#!/usr/bin/env bash
# Smoke test for the Procurement Article Research workflow.
#
# Two modes:
#
#   1. WIRING CHECK (default, no auth needed)
#      Validates the workflow is deployed, registered with the broker,
#      and reachable through the gateway. Does NOT actually run the
#      pipeline. Exits in <30s.
#
#   2. END-TO-END (set SAM_AUTH_TOKEN)
#      Submits a 3-item fixture, polls for completion, asserts on the
#      result. Requires a valid SAM access token (the gateway runs with
#      OAUTH2_ENABLED=true). Exits in 5-10 min.
#
# Usage:
#   make test-procurement-workflow
#   SAM_AUTH_TOKEN=<bearer-token> make test-procurement-workflow
#
# Obtaining a SAM_AUTH_TOKEN:
#   - Log in to https://sam.solace.lab in your browser
#   - DevTools -> Application -> Cookies -> copy the value of
#     `sam_access_token` (or whatever the session cookie is called)
#   - export SAM_AUTH_TOKEN=<value>
#
# Env overrides:
#   GATEWAY_PORT      local port for kubectl port-forward (default 8081)
#   POLL_INTERVAL_S   poll interval in seconds        (default 10)
#   POLL_MAX_ATTEMPTS max poll attempts               (default 30 = 5 min)
#   KUBECTL           path to kubectl                 (default in PATH)
#
# Exit codes:
#   0 = wiring check OR end-to-end passed
#   1 = environment error (kubectl, port-forward, fixture)
#   2 = workflow submission failed
#   3 = workflow timed out
#   4 = assertions failed on the result
#   5 = wiring check failed (workflow not reachable / not registered)

set -euo pipefail

GATEWAY_PORT="${GATEWAY_PORT:-8081}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-10}"
POLL_MAX_ATTEMPTS="${POLL_MAX_ATTEMPTS:-30}"
KUBECTL="${KUBECTL:-/Users/alexandermartens/.rd/bin/kubectl}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE="${SCRIPT_DIR}/articles.txt"

if [[ ! -f "$FIXTURE" ]]; then
    echo "ERROR: fixture not found at $FIXTURE" >&2
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required (brew install jq)" >&2
    exit 1
fi

cleanup() {
    if [[ -n "${PF_PID:-}" ]] && kill -0 "$PF_PID" 2>/dev/null; then
        kill "$PF_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "[smoke] Wiring check: workflow pod"
WF_POD="$($KUBECTL get pods -n sam-solace-lab-workflows \
    -l app=sam-procurement-workflow \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -z "$WF_POD" ]]; then
    echo "FAIL: no procurement workflow pod found in sam-solace-lab-workflows" >&2
    exit 5
fi
echo "[smoke]   pod: $WF_POD"

WF_READY="$($KUBECTL logs -n sam-solace-lab-workflows "$WF_POD" --tail=200 2>&1 \
    | grep -c "Workflow ready: ProcurementArticleResearch" || true)"
if [[ "$WF_READY" -lt 1 ]]; then
    echo "FAIL: workflow pod did not log 'Workflow ready'" >&2
    exit 5
fi
echo "[smoke]   workflow registered with broker"

echo "[smoke] Wiring check: agent pods (5 expected)"
for AGENT in sam-article-verification-agent sam-ean-search-agent \
             sam-price-comparison-agent sam-web-research-agent \
             sam-web-scraper-agent; do
    POD="$($KUBECTL get pods -n sam-solace-lab-agents \
        -l app=$AGENT \
        -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)"
    if [[ "$POD" != "Running" ]]; then
        echo "FAIL: agent $AGENT not Running (state=$POD)" >&2
        exit 5
    fi
    echo "[smoke]   $AGENT: Running"
done

echo "[smoke] Starting port-forward to gateway on :$GATEWAY_PORT"
"$KUBECTL" port-forward svc/agent-mesh "$GATEWAY_PORT":80 -n sam-solace-lab \
    >/tmp/sam-pf.log 2>&1 &
PF_PID=$!
sleep 3

if ! kill -0 "$PF_PID" 2>/dev/null; then
    echo "FAIL: port-forward died -- see /tmp/sam-pf.log" >&2
    cat /tmp/sam-pf.log >&2
    exit 5
fi

echo "[smoke] Wiring check: gateway responds (expect 401 without token)"
HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "http://localhost:$GATEWAY_PORT/api/v1/message:send" \
    -H "Content-Type: application/json" -d '{}' || true)"
if [[ "$HTTP_CODE" != "401" && "$HTTP_CODE" != "200" && "$HTTP_CODE" != "400" ]]; then
    echo "FAIL: unexpected gateway HTTP status: $HTTP_CODE" >&2
    exit 5
fi
echo "[smoke]   gateway HTTP $HTTP_CODE (auth-protected, as expected)"

if [[ -z "${SAM_AUTH_TOKEN:-}" ]]; then
    echo
    echo "[smoke] WIRING CHECK PASS"
    echo
    echo "  Workflow + 5 agents are deployed, registered, and reachable."
    echo "  End-to-end test skipped (no SAM_AUTH_TOKEN set)."
    echo
    echo "  To run the full E2E test:"
    echo "    1. Browser: log in to https://sam.solace.lab"
    echo "    2. DevTools -> Application -> Cookies -> copy sam_access_token"
    echo "    3. SAM_AUTH_TOKEN=<value> make test-procurement-workflow"
    echo
    echo "  OR submit the fixture interactively via the SAM Web UI"
    echo "  (paste the contents of $FIXTURE)."
    exit 0
fi

# ============================================================================
# End-to-end test path (SAM_AUTH_TOKEN provided)
# ============================================================================

ARTICLES_RAW="$(cat "$FIXTURE")"
TASK_ID="smoke-$(date +%s)-$RANDOM"
MSG_ID="msg-$TASK_ID"

# Build the JSON payload via jq so all newlines / quotes are escaped properly.
PAYLOAD="$(jq -n \
    --arg task "$TASK_ID" \
    --arg msg "$MSG_ID" \
    --arg articles "$ARTICLES_RAW" \
    '{
        id: $task,
        params: {
            message: {
                role: "user",
                parts: [
                    {kind: "text",
                     text: ("Recherchiere die folgenden Artikel und erstelle einen Beschaffungsbericht.\n\nArtikel:\n" + $articles)}
                ],
                messageId: $msg,
                metadata: {agent_name: "ProcurementArticleResearch"}
            }
        }
    }')"

echo "[smoke] Submitting workflow request (task $TASK_ID)"
SUBMIT_RESP="$(curl -fsS -X POST "http://localhost:$GATEWAY_PORT/api/v1/message:send" \
    -H "Authorization: Bearer $SAM_AUTH_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")" || {
    echo "ERROR: submit failed (token expired? rerun with fresh SAM_AUTH_TOKEN)" >&2
    exit 2
}

REAL_TASK_ID="$(echo "$SUBMIT_RESP" | jq -r '.result.id // .id // empty')"
if [[ -z "$REAL_TASK_ID" ]]; then
    echo "ERROR: could not extract task id from submit response:" >&2
    echo "$SUBMIT_RESP" >&2
    exit 2
fi
echo "[smoke] Workflow accepted, real task id: $REAL_TASK_ID"

echo "[smoke] Polling task (every ${POLL_INTERVAL_S}s, up to ${POLL_MAX_ATTEMPTS} attempts)"
RESULT=""
for ((i=1; i<=POLL_MAX_ATTEMPTS; i++)); do
    sleep "$POLL_INTERVAL_S"
    RESP="$(curl -fsS "http://localhost:$GATEWAY_PORT/api/v1/tasks/$REAL_TASK_ID" \
        -H "Authorization: Bearer $SAM_AUTH_TOKEN" || true)"
    STATE="$(echo "$RESP" | jq -r '.status.state // empty')"
    echo "[smoke]   attempt $i/$POLL_MAX_ATTEMPTS state=$STATE"
    case "$STATE" in
        completed)
            RESULT="$RESP"
            break
            ;;
        failed|cancelled)
            echo "ERROR: workflow ended in state '$STATE'" >&2
            echo "$RESP" | jq . >&2
            exit 4
            ;;
    esac
done

if [[ -z "$RESULT" ]]; then
    echo "ERROR: workflow did not complete within $((POLL_INTERVAL_S * POLL_MAX_ATTEMPTS))s" >&2
    exit 3
fi

echo "[smoke] Workflow completed. Running assertions..."

REPORT_TEXT="$(echo "$RESULT" | jq -r '
    .status.message.parts[]?
    | select(.kind == "text")
    | .text' | head -c 500)"
if [[ -z "$REPORT_TEXT" ]]; then
    echo "FAIL: no report text found in response" >&2
    echo "$RESULT" | jq . >&2
    exit 4
fi

PASS=true
for KEYWORD in "Bosch" "Sony" "Leitz"; do
    if ! echo "$RESULT" | jq -r '
        .status.message.parts[]? | select(.kind == "text") | .text
    ' | grep -qi "$KEYWORD"; then
        echo "FAIL: keyword '$KEYWORD' not found in report" >&2
        PASS=false
    fi
done

if ! echo "$RESULT" | jq -r '
    .status.message.parts[]? | select(.kind == "text") | .text
' | grep -qiE "Nettoartikel|skipped_b2b_netto|B2B"; then
    echo "FAIL: B2B Nettoartikel not flagged in report" >&2
    PASS=false
fi

if $PASS; then
    echo "[smoke] PASS -- E2E run completed and all assertions met"
    exit 0
else
    echo "[smoke] FAIL -- one or more assertions failed" >&2
    exit 4
fi
