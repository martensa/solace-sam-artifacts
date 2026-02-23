#!/usr/bin/env python3
"""
Solace SEMP v2 MCP Server — dynamic OpenAPI-driven tool generation.

Loads a single SEMP OpenAPI/Swagger spec file and exposes every operation
as an MCP tool with correctly typed JSON-Schema parameters.  Supports both
Swagger 2.0 (config spec) and OpenAPI 3.0 (monitor spec).

Env: OPENAPI_SPEC — path to the spec JSON file.

Key features:
  - Reads param types from schema sub-object (OAS 3.0 fix)
  - Preserves array/integer/enum types in tool input schemas
  - Comma-joins array query params (SEMP style=form, explode=false)
  - Server-side response trimming for monitor list endpoints
  - Hard character cap to prevent LLM context overflow
  - Compact JSON output (no indent) to minimise token usage
  - Cross-LLM compatible (Gemini, Claude, GPT)
  - Token-optimised tool descriptions with method prefix
  - Server-side tool filtering via env vars (method, tag, path, tool name)

Protocol: MCP JSON-RPC 2.0 over stdio
"""

import hashlib, json, logging, os, sys, urllib.parse
import requests

# ── logging (never stdout — MCP uses it) ──────────────────────────────────
LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
LOG_FILE  = os.environ.get("MCP_LOG_FILE", "solace_mcp.log")
log = logging.getLogger("solace-mcp")
log.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_fh = logging.FileHandler(LOG_FILE)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_fh)
log.propagate = False

# ── broker config ─────────────────────────────────────────────────────────
BASE_URL     = os.environ.get("SOLACE_SEMPV2_BASE_URL", "http://localhost:8080")
AUTH_METHOD  = os.environ.get("SOLACE_SEMPV2_AUTH_METHOD", "basic")
USERNAME     = os.environ.get("SOLACE_SEMPV2_USERNAME", "admin")
PASSWORD     = os.environ.get("SOLACE_SEMPV2_PASSWORD", "admin")
BEARER_TOKEN = os.environ.get("SOLACE_SEMPV2_BEARER_TOKEN", "")
MAX_CHARS    = int(os.environ.get("MCP_MAX_RESPONSE_CHARS", "25000"))

# ── tool filtering (server-side, reduces tool count for GPT etc.) ─────────
def _csv(env_key):
    """Parse comma-separated env var into a list of stripped, non-empty strings."""
    val = os.environ.get(env_key, "")
    return [s.strip() for s in val.split(",") if s.strip()] if val else []

INCLUDE_METHODS = [m.upper() for m in _csv("MCP_API_INCLUDE_METHODS")]
EXCLUDE_METHODS = [m.upper() for m in _csv("MCP_API_EXCLUDE_METHODS")]
INCLUDE_TAGS    = _csv("MCP_API_INCLUDE_TAGS")
EXCLUDE_TAGS    = _csv("MCP_API_EXCLUDE_TAGS")
INCLUDE_PATHS   = _csv("MCP_API_INCLUDE_PATHS")
EXCLUDE_PATHS   = _csv("MCP_API_EXCLUDE_PATHS")
INCLUDE_TOOLS   = _csv("MCP_API_INCLUDE_TOOLS")
EXCLUDE_TOOLS   = _csv("MCP_API_EXCLUDE_TOOLS")

def _should_register(method, tags, path_tpl, tool_name):
    """Server-side filter: returns True if this tool should be registered.
    Uses same env-var names as SolaceLabs reference implementation.
    Exclusion takes precedence if both include and exclude match."""
    m = method.upper()
    # HTTP method filter
    if INCLUDE_METHODS and m not in INCLUDE_METHODS:
        log.debug("Filter out %s %s: method not in include list", m, tool_name)
        return False
    if EXCLUDE_METHODS and m in EXCLUDE_METHODS:
        log.debug("Filter out %s %s: method in exclude list", m, tool_name)
        return False
    # tool name filter
    if INCLUDE_TOOLS and tool_name not in INCLUDE_TOOLS:
        log.debug("Filter out %s: tool not in include list", tool_name)
        return False
    if EXCLUDE_TOOLS and tool_name in EXCLUDE_TOOLS:
        log.debug("Filter out %s: tool in exclude list", tool_name)
        return False
    # tag filter
    if INCLUDE_TAGS and not any(t in INCLUDE_TAGS for t in tags):
        log.debug("Filter out %s: no matching tags in include list", tool_name)
        return False
    if EXCLUDE_TAGS and any(t in EXCLUDE_TAGS for t in tags):
        log.debug("Filter out %s: tag in exclude list", tool_name)
        return False
    # path filter (substring match)
    if INCLUDE_PATHS and not any(p in path_tpl for p in INCLUDE_PATHS):
        log.debug("Filter out %s: path not matching include list", tool_name)
        return False
    if EXCLUDE_PATHS and any(p in path_tpl for p in EXCLUDE_PATHS):
        log.debug("Filter out %s: path matching exclude list", tool_name)
        return False
    return True

# ── response trimming (list endpoints — reduces token usage) ──────────────
_KEEP = {
    "queue": {"queueName","msgVpnName","accessType","durable","ingressEnabled",
        "egressEnabled","spooledMsgCount","msgSpoolUsage","msgSpoolPeakUsage",
        "maxMsgSpoolUsage","maxMsgSize","maxBindCount","bindCount","consumerCount",
        "averageRxMsgRate","averageTxMsgRate","lowPriorityMsgCongestionState",
        "deadMsgQueue","deletedMsgCount"},
    "client": {"clientName","msgVpnName","clientAddress","clientUsername",
        "clientProfileName","aclProfileName","platform","uptime","slowSubscriber",
        "averageRxMsgRate","averageTxMsgRate","dataRxMsgCount","dataTxMsgCount"},
    "vpn": {"msgVpnName","enabled","state","failureReason","maxConnectionCount",
        "maxMsgSpoolUsage","msgSpoolUsage","msgSpoolMsgCount","connectionCount",
        "subscriptionCount","averageRxMsgRate","averageTxMsgRate","rxMsgRate",
        "txMsgRate","maxEndpointCount","maxSubscriptionCount",
        "dmrEnabled","replicationEnabled","replicationRole"},
    "flow": {"flowId","queueName","clientName","msgVpnName","activityState",
        "ackedMsgCount","unackedMsgCount","bindTime","windowSize"},
    "conn": {"clientName","clientAddress","msgVpnName","uptime","state","ssl"},
}
_TRIM_MAP = {}  # toolName -> category (populated during spec load)

def _trim(name, body):
    # always strip "links" — they're sub-resource URIs, never useful to the LLM
    if "links" in body:
        body = {k: v for k, v in body.items() if k != "links"}
    # field trimming only for list endpoints (data is an array)
    cat = _TRIM_MAP.get(name)
    if not cat:
        return body
    keep = _KEEP.get(cat)
    if not keep:
        return body
    data = body.get("data")
    if isinstance(data, list):
        body = dict(body)
        body["data"] = [
            {k: v for k, v in o.items() if k in keep} if isinstance(o, dict) else o
            for o in data
        ]
    # single-object GETs are NOT trimmed — the LLM may need any field
    return body

def _cap(text):
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS] + f"\n...TRUNCATED ({len(text)} chars). Use count or where params to narrow results."

# ══════════════════════════════════════════════════════════════════════════
# SPEC LOADING — supports both Swagger 2.0 and OpenAPI 3.0
# ══════════════════════════════════════════════════════════════════════════
def _resolve_ref(spec, ref):
    parts = ref.lstrip("#/").split("/")
    node = spec
    for p in parts:
        node = node.get(p, {})
    return node

# parameters to skip — they waste tokens or cause LLM hallucination errors
# opaquePassword: config-only, never needed by agents
# select: LLMs guess wrong field names causing 400 errors; server-side
#         trimming + links stripping + char cap handle payload size instead
_SKIP_PARAMS = {"opaquePassword", "select"}

# shorten verbose SEMP parameter descriptions to save tokens
_SHORT_PARAM_DESC = {
    "count":  "Max items per page (default 10, max 200).",
    "cursor": "Paging cursor from meta.paging.cursorQuery of previous response.",
    "where":  "Filter conditions, e.g. [\"queueName==my*\"].",
}

def _param_schema(param, spec):
    if "$ref" in param:
        param = _resolve_ref(spec, param["$ref"])
    name = param.get("name", "")
    if name in _SKIP_PARAMS:
        return "", {}, False, ""
    schema = param.get("schema", {})
    if "$ref" in schema:
        schema = _resolve_ref(spec, schema["$ref"])
    ptype = schema.get("type", param.get("type", "string"))
    desc = _SHORT_PARAM_DESC.get(name, param.get("description", ""))
    prop = {"type": ptype, "description": desc}
    if ptype == "array":
        items = schema.get("items", param.get("items", {"type": "string"}))
        prop["items"] = items
    for k in ("enum", "minimum", "maximum", "default"):
        v = schema.get(k, param.get(k))
        if v is not None:
            prop[k] = v
    return name, prop, param.get("required", False), param.get("in", "query")

def _detect_api_label(spec):
    """Auto-detect whether spec is monitor or config based on content."""
    base = spec.get("basePath", "")
    servers = spec.get("servers", [])
    server_url = servers[0].get("url", "") if servers else ""
    if "/monitor" in base or "/monitor" in server_url:
        return "monitor"
    if "/config" in base or "/config" in server_url:
        return "config"
    # fallback: if there are any non-GET methods, it's config
    for _path, methods in spec.get("paths", {}).items():
        for m in methods:
            if m in ("post", "put", "patch", "delete"):
                return "config"
    return "monitor"

def load_spec(path):
    """Load an OpenAPI spec and return (api_label, list of tool dicts)."""
    with open(path) as f:
        spec = json.load(f)

    api_label = _detect_api_label(spec)

    base_path = ""
    if "basePath" in spec:          # Swagger 2.0
        base_path = spec["basePath"]
    elif "servers" in spec:         # OAS 3.0
        base_path = spec["servers"][0].get("url", "").rstrip("/")
        if base_path.startswith("http"):
            base_path = urllib.parse.urlparse(base_path).path

    tools = []
    for path_tpl, methods in spec.get("paths", {}).items():
        for method_lower, details in methods.items():
            if method_lower in ("parameters", "summary", "description", "$ref"):
                continue
            op_id = details.get("operationId")
            if not op_id:
                continue
            method = method_lower.upper()
            tags = details.get("tags", [])
            if "deprecated" in tags:
                continue

            # compute tool name early for filtering
            if len(op_id) > 64:
                _h = hashlib.md5(op_id.encode()).hexdigest()[:4]
                tool_name = op_id[:59] + "_" + _h
            else:
                tool_name = op_id

            # server-side filter (env vars)
            if not _should_register(method, tags, path_tpl, tool_name):
                continue

            # ── description (truncate tables, keep first sentence, add method) ──
            desc = details.get("summary", "")
            raw_desc = details.get("description", "")
            # strip Attribute| markdown tables
            cut = raw_desc.find("Attribute|")
            if cut > 0:
                raw_desc = raw_desc[:cut].rstrip()
            # always use raw_desc if summary is empty (config spec)
            if not desc and raw_desc:
                # take first sentence(s) up to 180 chars
                first = raw_desc.split("\n\n")[0].strip()
                desc = first if len(first) <= 180 else first[:177] + "..."
            elif raw_desc and len(raw_desc) < 180:
                desc = (desc + " " + raw_desc).strip() if desc else raw_desc
            if len(desc) > 200:
                desc = desc[:197] + "..."
            # prefix with method so LLMs distinguish get/create/update/delete
            method_hint = {"GET": "[GET]", "POST": "[CREATE]", "PUT": "[REPLACE]",
                           "PATCH": "[UPDATE]", "DELETE": "[DELETE]"}.get(method, f"[{method}]")
            desc = f"{method_hint} {desc}" if desc else method_hint

            # ── parameters ────────────────────────────────────────
            properties = {}
            required = []
            path_params = []
            has_body = False
            for raw_p in details.get("parameters", []):
                pname, prop, req, loc = _param_schema(raw_p, spec)
                if not pname:
                    continue
                if loc == "body":
                    has_body = True
                    properties["body"] = {"type": "object", "description": "JSON body with resource attributes."}
                    required.append("body")
                elif loc in ("path", "query"):
                    properties[pname] = prop
                    if req:
                        required.append(pname)
                    if loc == "path":
                        path_params.append(pname)

            # OAS 3.0 requestBody
            rb = details.get("requestBody")
            if rb and not has_body:
                has_body = True
                properties["body"] = {"type": "object", "description": "JSON body with resource attributes."}
                if rb.get("required"):
                    required.append("body")

            input_schema = {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}

            tool = {
                "name": tool_name,
                "description": desc,
                "inputSchema": input_schema,
                "_path": path_tpl,
                "_base": base_path,
                "_method": method,
                "_path_params": path_params,
                "_api": api_label,
                "_has_body": has_body,
            }
            tools.append(tool)

            # set up trim map for list endpoints (both monitor and config)
            if method == "GET":
                _setup_trim_entry(tool_name, op_id)

    # log filter summary if any filters active
    any_filter = any([INCLUDE_METHODS, EXCLUDE_METHODS, INCLUDE_TAGS, EXCLUDE_TAGS,
                      INCLUDE_PATHS, EXCLUDE_PATHS, INCLUDE_TOOLS, EXCLUDE_TOOLS])
    if any_filter:
        log.info("Loaded %d tools from %s (detected: %s) [filtered by env vars]", len(tools), path, api_label)
    else:
        log.info("Loaded %d tools from %s (detected: %s) [no filters]", len(tools), path, api_label)
    return api_label, tools

def _setup_trim_entry(tool_name, op_id):
    """Register list GET endpoints for server-side field trimming.
    Single-object GETs are NOT registered — they return full data so the
    LLM can answer any question (TLS, auth, MQTT, replication, etc.)."""
    ln = op_id.lower()
    if op_id == "getMsgVpnClients":
        _TRIM_MAP[tool_name] = "client"
    elif op_id == "getMsgVpnQueues":
        _TRIM_MAP[tool_name] = "queue"
    elif op_id == "getMsgVpns":
        _TRIM_MAP[tool_name] = "vpn"
    elif "txflow" in ln and op_id.endswith("s"):
        _TRIM_MAP[tool_name] = "flow"
    elif "connection" in ln and op_id.endswith("s"):
        _TRIM_MAP[tool_name] = "conn"
    elif "QueueTxFlows" in op_id:
        _TRIM_MAP[tool_name] = "flow"
    elif "ClientTxFlows" in op_id:
        _TRIM_MAP[tool_name] = "flow"
    elif "queues" in ln and "subscription" not in ln and "msg" not in ln and "priority" not in ln and op_id.endswith("s"):
        _TRIM_MAP[tool_name] = "queue"

# ══════════════════════════════════════════════════════════════════════════
# SEMP HTTP CLIENT
# ══════════════════════════════════════════════════════════════════════════
def _call(tool, args):
    method = tool["_method"]
    path = tool["_path"]
    for p in tool["_path_params"]:
        val = args.pop(p, "")
        path = path.replace("{" + p + "}", urllib.parse.quote(str(val), safe=""))
    url = BASE_URL.rstrip("/") + tool["_base"] + path

    body = args.pop("body", None) if tool["_has_body"] else None
    query = {}
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, list):
            query[k] = ",".join(str(x) for x in v)
        elif k == "count":
            try:
                query[k] = int(v)
            except (ValueError, TypeError):
                query[k] = v
        else:
            query[k] = v

    auth = None
    headers = {"Accept": "application/json"}
    if AUTH_METHOD == "basic":
        auth = (USERNAME, PASSWORD)
    elif AUTH_METHOD == "bearer":
        headers["Authorization"] = f"Bearer {BEARER_TOKEN}"
    if body is not None:
        headers["Content-Type"] = "application/json"

    log.info("%s %s q=%s body=%s", method, url, query or "-", "yes" if body else "-")
    r = requests.request(method, url, params=query or None, json=body,
                         headers=headers, auth=auth, timeout=30)
    r.raise_for_status()
    return r.json() if r.text else {"meta": {"responseCode": r.status_code}}

# ══════════════════════════════════════════════════════════════════════════
# MCP HANDLERS
# ══════════════════════════════════════════════════════════════════════════
ALL_TOOLS = []
_BY_NAME = {}
_API_LABEL = "unknown"

def _init(rid, _p):
    return {"jsonrpc": "2.0", "id": rid, "result": {
        "protocolVersion": "2024-11-05",
        "serverInfo": {"name": "solace-semp-mcp", "version": "5.0.0"},
        "capabilities": {"tools": {}}}}

def _list(rid, _p):
    return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in ALL_TOOLS]}}

def _call_tool(rid, p):
    name = p.get("name", "")
    args = dict(p.get("arguments", {}))
    t = _BY_NAME.get(name)
    if not t:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
    try:
        result = _call(t, args)
        result = _trim(name, result)
        text = _cap(json.dumps(result, separators=(",", ":")))
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}}
    except requests.HTTPError as e:
        detail = e.response.text[:1500] if e.response is not None else str(e)
        url = e.response.url if e.response is not None else "unknown"
        status = getattr(e.response, "status_code", None)
        log.error("%s: %s %s", name, e, detail)
        # detect SEMP parameter validation errors and give targeted hint
        if status == 400 and "not a valid" in detail:
            hint = ("Invalid parameter value. Check the where filter field names "
                    "or remove the filter and retry.")
        elif status == 404:
            hint = "Resource not found. Check msgVpnName, queueName, or other path params for typos."
        elif status in (401, 403):
            hint = "Authentication/authorization error. Check credentials and permissions."
        else:
            hint = "Check path params (msgVpnName, queueName, etc.) and auth."
        err = {"error": str(e), "status_code": status, "url": url, "detail": detail,
               "hint": hint}
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text",
            "text": json.dumps(err, separators=(",", ":"))}], "isError": True}}
    except Exception as e:
        log.exception(name)
        return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text",
            "text": json.dumps({"error": str(e)}, separators=(",", ":"))}], "isError": True}}

_H = {
    "initialize": _init,
    "tools/list": _list,
    "tools/call": _call_tool,
    "mcp.list_tools": _list,
    "mcp.call_tool": _call_tool,
}

# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
def main():
    global _API_LABEL
    spec_path = os.environ.get("OPENAPI_SPEC", "")
    if not spec_path:
        log.error("OPENAPI_SPEC env var not set. Provide the path to a single spec file.")
        sys.exit(1)

    _API_LABEL, tools = load_spec(spec_path)
    ALL_TOOLS.extend(tools)
    _BY_NAME.update({t["name"]: t for t in ALL_TOOLS})
    log.info("Solace SEMP MCP v5.0 ready: %d tools (%s), base=%s", len(ALL_TOOLS), _API_LABEL, BASE_URL)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        if rid is None:
            continue
        h = _H.get(req.get("method", ""))
        if h:
            r = h(rid, req.get("params", {}))
        else:
            r = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
        sys.stdout.write(json.dumps(r, separators=(",", ":")) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
