#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Testlauf 3 driver -- Price Comparison Agent v1.0.0 (post Phase A-G).
#
# Usage:
#   1. Get a Bearer token from your active SAM browser session:
#        - Open https://sam.solace.lab in a logged-in tab
#        - DevTools (F12) -> Network -> any `/api/v1/...` request
#        - Right-click -> Copy as cURL -> grep "Authorization: Bearer "
#   2. Export it:
#        export SAM_BEARER_TOKEN="eyJ..."
#   3. Make sure port-forward is running:
#        kubectl port-forward svc/agent-mesh 18081:80 -n sam-solace-lab &
#   4. Run:
#        ./run.sh                    # all 25 positions
#        ./run.sh 1 5                # only positions 1-5
#
# Output: results/<pos>.json  (full task payload)
#         results/summary.md   (markdown overview, pos | category | n_offers | min/max | flag)
# ----------------------------------------------------------------------------
set -euo pipefail

GATEWAY="${SAM_GATEWAY:-http://localhost:18081}"
TOKEN="${SAM_BEARER_TOKEN:-}"
AGENT="${SAM_AGENT_NAME:-PriceComparisonAgent}"
POSITIONS_FILE="$(dirname "$0")/positions.tsv"
OUTDIR="$(dirname "$0")/results"
POLL_TIMEOUT=180
POLL_INTERVAL=3

if [[ -z "$TOKEN" ]]; then
    echo "ERROR: SAM_BEARER_TOKEN env var is required." >&2
    echo "       export SAM_BEARER_TOKEN=\"<your-bearer-token>\"" >&2
    exit 2
fi

mkdir -p "$OUTDIR"

START_POS="${1:-1}"
END_POS="${2:-25}"

echo "Testlauf 3 -- positions ${START_POS}..${END_POS}"
echo "Gateway:  $GATEWAY"
echo "Agent:    $AGENT"
echo "Outdir:   $OUTDIR"
echo

while IFS=$'\t' read -r pos qty query; do
    [[ "$pos" == "pos" ]] && continue   # skip header
    if (( pos < START_POS || pos > END_POS )); then continue; fi

    echo "── Pos $pos (qty=$qty): $query"
    msg_id="testlauf3-pos${pos}-$(date +%s)"
    payload=$(jq -n --arg id "$msg_id" --arg q "$query" --arg agent "$AGENT" '
      {
        id: $id,
        params: {
          message: {
            role: "user",
            parts: [{kind: "text", text: ("Suche Preise fuer: " + $q)}],
            messageId: $id,
            metadata: {agent_name: $agent}
          }
        }
      }')
    send_resp=$(curl -sS -X POST "$GATEWAY/api/v1/message:send" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -d "$payload")
    task_id=$(echo "$send_resp" | jq -r '.result.task_id // .task_id // .id // empty')
    if [[ -z "$task_id" ]]; then
        echo "  ERROR: no task_id. Response: $send_resp"
        continue
    fi
    echo "  task_id=$task_id"

    elapsed=0
    while (( elapsed < POLL_TIMEOUT )); do
        sleep "$POLL_INTERVAL"
        elapsed=$((elapsed + POLL_INTERVAL))
        task_resp=$(curl -sS -H "Authorization: Bearer $TOKEN" \
            "$GATEWAY/api/v1/tasks/$task_id")
        state=$(echo "$task_resp" | jq -r '
            .invocation_flow[-1].payload.result.status.state //
            .status //
            ""' 2>/dev/null || echo "")
        case "$state" in
            completed|COMPLETED|done)
                echo "  done in ${elapsed}s"
                echo "$task_resp" > "$OUTDIR/pos${pos}.json"
                break
                ;;
            failed|FAILED|error)
                echo "  FAILED in ${elapsed}s"
                echo "$task_resp" > "$OUTDIR/pos${pos}.json"
                break
                ;;
            *)
                printf "  [%ds] state=%s\r" "$elapsed" "$state"
                ;;
        esac
    done
    if (( elapsed >= POLL_TIMEOUT )); then
        echo "  TIMEOUT after ${POLL_TIMEOUT}s"
        echo "$task_resp" > "$OUTDIR/pos${pos}.json"
    fi
done < "$POSITIONS_FILE"

echo
echo "Building summary -> $OUTDIR/summary.md"
{
    echo "# Testlauf 3 -- summary"
    echo
    echo "| Pos | Query | n_offers | min EUR | max EUR | category | next_actions? |"
    echo "|-----|-------|----------|---------|---------|----------|---------------|"
    for f in "$OUTDIR"/pos*.json; do
        pos=$(basename "$f" .json | sed 's/pos//')
        # Extract the agent's final text response and try to parse
        text=$(jq -r '
            [.invocation_flow[]?
              | select(.direction == "response")
              | .payload.result.status.message.parts[]?.text]
            | last // ""' "$f" 2>/dev/null || echo "")
        # The agent renders a markdown report; we fish out hints
        n_offers=$(echo "$text" | grep -cE "^\| *[0-9]+ *\|" || true)
        min_eur=$(echo "$text" | grep -oE "[0-9]+[.,][0-9]{2} *EUR" | head -1)
        max_eur=$(echo "$text" | grep -oE "[0-9]+[.,][0-9]{2} *EUR" | tail -1)
        has_next=$(echo "$text" | grep -ciE "naechste schritte|next_actions" || true)
        query=$(awk -F'\t' -v p="$pos" 'NR>1 && $1==p {print $3}' "$POSITIONS_FILE")
        echo "| $pos | ${query:0:50} | $n_offers | $min_eur | $max_eur |  | $has_next |"
    done
} > "$OUTDIR/summary.md"
echo "Done."
