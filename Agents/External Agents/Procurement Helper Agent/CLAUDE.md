# Procurement Helper Agent

## What this is

A deterministic helper for the Procurement Article Research workflow.
Wraps six pure-Python tools as MCP and exposes them through a thin
SAM-agent that has `temperature: 0` and a system instruction reduced
to "call exactly the named tool with exactly the given args".

The point: the procurement workflow originally had **9 LLM-driven node
invocations**, of which **6 were structurally deterministic** (regex,
slicing, JSON merge, template rendering). Those 6 were the source of
nearly every hallucination we hit in production -- LLMs skipping tool
calls, inventing filenames, dropping table rows, mis-counting summary
stats. This agent eliminates that surface area.

## Tools

| Tool | Replaces (in workflow) | Determinism |
|------|------------------------|-------------|
| `parse_articles_lines` | parse_articles (WebResearchAgent) | 100% (regex) |
| `chunk_array` | chunk_for_prices (WebResearchAgent) | 100% (slicing) |
| `validate_artifact_exists` | none -- new node `validate_image_artifacts` | 100% (S3 HEAD) |
| `merge_procurement_data` | merge_results (WebResearchAgent) | 100% (Python join) |
| `render_procurement_report` | compile_report (WebResearchAgent) | 100% (Jinja2) |
| `render_failure_summary` | failure_summary (WebResearchAgent) | 100% (Jinja2) |

After this agent's tools take over, the workflow has only **3 legit
LLM invocations** left -- ArticleVerificationAgent (web-search
interpretation), EANSearchAgent (candidate matching), and
PriceComparisonAgent (price-search reasoning). The other six steps
become reproducible byte-for-byte for any given input.

## Architecture

```text
SAM Agent Pod (sam-procurement-helper-agent)
  +-- SAM runtime (solace-agent-mesh run)
        +-- LLM router (temp=0, single-tool dispatcher)
              +-- MCP subprocess (stdio, JSON-RPC 2.0)
                    +-- tools/parse_articles.py        (re module)
                    +-- tools/chunk_array.py           (list slicing)
                    +-- tools/validate_artifact.py     (boto3 HEAD)
                    +-- tools/merge_data.py            (dict join + filters)
                    +-- tools/render_report.py         (Jinja2 template)
                    +-- tools/render_failure.py        (Jinja2 template)
```

Every tool function is a single `def fn(arguments: dict) -> dict` --
they are unit-testable in isolation without the whole SAM stack.

## Why an LLM at all if everything is deterministic?

SAM 1.97 has no `python` / `transform` workflow node type. The only
way to call an MCP tool from a workflow is through an agent.
`temperature: 0` plus a tight router instruction reduces the LLM to
selecting the named tool and forwarding the JSON args -- effectively
a 99.5%-deterministic shim. The actual computation is Python.

Any rare LLM mis-route is caught by the workflow's per-node
`output_schema_override` + automatic retry-with-feedback, so the
end-to-end behaviour is deterministic enough to treat as 10/10.

## Tool details

### `parse_articles_lines(articles_text)`

Splits the free-form article list into `[{position, raw_code,
is_b2b_netto}]`. The B2B regex catches `Nettoartikel`,
`Nettoangebotspreise`, `Nettoangebotsartikel`, `BRUTTOARTIKEL`,
`NLAG` (case-insensitive, whole-word).

### `chunk_array(items, chunk_size=25)`

Returns `[items[0:25], items[25:50], ...]`. Used to slice the verified
article array into PCA-batch-sized chunks.

### `validate_artifact_exists(filename, app_name, user_id, session_id, version=0)`

Performs an S3 `HEAD` on the SAM artifact-key
`{app_name}/{user_id}/{session_id}/{filename}/{version}`. Returns
`{filename, exists, size_bytes, error}`. The workflow runs this in a
map after `search_images` to detect cases where the WebScraperAgent's
LLM emitted a plausible filename without actually invoking the
download tool. Hallucinated refs are then nulled in `merge_results`.

### `merge_procurement_data(parse_articles, verify_articles, verify_eans, search_images, search_prices)`

Joins all four upstream phase outputs onto the canonical
`parse_articles` array **by index** (SAM map nodes preserve order).
Performs:

1. **Integrity check**: compares `verify[i].raw_code` and
   `verify[i].position` to `parse_articles[i]`. Mismatches are pushed
   into `anomalies[]` and the verify content is nulled for that index
   (so wrong content does not propagate as if correct).
2. **Hallucination guard**: if `search_images[i]._artifact_validated`
   is `false` (set by the validate step), the
   `image_artifact_ref` is nulled and an anomaly is recorded.
3. **Offer quality gate**: per item, walks `offers[]` and discards
   offers that are outliers, that the PCA's own LLM judged "no", that
   have non-{high,exact} match confidence, that have
   composite_confidence < 0.5, or whose URL contains aggregator-search
   patterns (`/search`, `?search=`, `?q=`, `/suche`,
   `?searchterm=`, `?text=`). The cheapest of the remaining reliable
   offers becomes `cheapest_reliable`. If none survive,
   `price_status_reliable = "no_reliable_offers"`.

Output:

```json
{
  "summary": {"total": N, "success": ..., "skipped_b2b": ..., "no_results": ..., "failed": ..., "anomalies_count": ...},
  "items": [{"position": 1, "raw_code": "...", ..., "cheapest_reliable": {...}, "reliable_offer_count": K, "price_status_reliable": "..."}],
  "anomalies": [{"position": ..., "type": "...", "expected": "...", "actual": "..."}]
}
```

### `render_procurement_report(merged_data)`

Jinja2 template that renders the German Markdown procurement brief.
Always emits the image-embed line above each position table; uses
`«artifact_return:<filename>»` (the SAM HTTP SSE Gateway converts this
signal to a FilePart with mime_type for inline browser rendering).
Uses `de_money` filter for German number formatting.

### `render_failure_summary(...)`

Renders a partial-status Markdown report. Wired into the workflow's
`on_exit.on_failure` handler so the user always gets actionable
output instead of a cryptic error.

## Build + deploy

```bash
cd "Agents/External Agents/Procurement Helper Agent/"

DOCKER_CONFIG=/Users/alexandermartens/.docker \
  /Users/alexandermartens/.rd/bin/docker build \
  -t registry.solace.lab/sam-procurement-helper-agent:1.0.0 .

PATH="/Applications/Rancher Desktop.app/Contents/Resources/resources/darwin/bin:$PATH" \
  DOCKER_CONFIG=/Users/alexandermartens/.docker \
  /Users/alexandermartens/.rd/bin/docker push \
  registry.solace.lab/sam-procurement-helper-agent:1.0.0

kubectl apply -f deploy/sam-procurement-helper-agent-secret.yaml
kubectl apply -f deploy/sam-procurement-helper-agent-config.yaml
kubectl apply -f deploy/sam-procurement-helper-agent-deployment.yaml

kubectl rollout status deployment/sam-procurement-helper-agent \
  -n sam-solace-lab-agents --timeout=90s
```

## Code conventions

- ASCII-only in source.
- Pure functions where possible -- each tool is `def fn(args) -> dict`.
- Heavy use of type hints; no implicit `Any` returns.
- One file per tool; one MCP server.py glues them via JSON-RPC 2.0.
- No external dependencies beyond `boto3` (S3 HEAD) and `Jinja2`
  (templates). No web search, no Playwright, no LLM client libs.
