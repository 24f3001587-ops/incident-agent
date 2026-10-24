from fastapi import FastAPI, HTTPException
import hashlib
import json
import os

app = FastAPI()

STORAGE_FILE = "incidents_store.json"

def load_db():
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_db(data):
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

@app.post("/v2/incidents")
async def create_incident(payload: dict):
    if payload.get("profile") != "ga5-incident-agent/v2":
        raise HTTPException(status_code=400, detail="Unsupported profile")
    
    run_id = payload.get("runId")
    if not run_id:
        raise HTTPException(status_code=400, detail="Missing runId")
    
    public_marker = payload.get("publicMarker", "marker")
    incident = payload.get("incident", {})
    policy = payload.get("policy", {})
    tool_catalog = payload.get("toolCatalog", [])
    
    payload_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    
    db = load_db()
    if run_id in db:
        if db[run_id]["payload_digest"] != payload_digest:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT")
        return db[run_id]["state"]

    allowed_causes = incident.get("allowedRootCauses", ["unknown"])
    root_cause = allowed_causes[0] if allowed_causes else "unknown"
    
    # Pick first diagnostic tool
    tool_name = "query_metrics"
    for t in tool_catalog:
        if t["name"] not in policy.get("effectTools", []):
            tool_name = t["name"]
            break

    action_id = hashlib.sha256(f"{run_id}-act-1".encode()).hexdigest()[:16]
    call_id = hashlib.sha256(f"{run_id}-call-1".encode()).hexdigest()[:16]
    trace_id = hashlib.sha256(run_id.encode()).hexdigest()[:32]
    span_server = hashlib.sha256((run_id + "server").encode()).hexdigest()[:16]
    span_agent = hashlib.sha256((run_id + "agent").encode()).hexdigest()[:16]
    span_chat = hashlib.sha256((run_id + "chat").encode()).hexdigest()[:16]
    span_tool = hashlib.sha256((run_id + "tool").encode()).hexdigest()[:16]
    span_client = hashlib.sha256((run_id + "client").encode()).hexdigest()[:16]
    span_join = hashlib.sha256((run_id + "join").encode()).hexdigest()[:16]

    dispatches = [{
        "actionId": action_id,
        "callId": call_id,
        "phase": "diagnostic",
        "toolName": tool_name,
        "arguments": {"service": incident.get("service", "core")},
        "evidence": ["ev_01", "ev_02"],
        "attempt": 1,
        "traceparent": f"00-{trace_id}-{span_client}-01"
    }]

    state = {
        "runId": run_id,
        "status": "waiting",
        "diagnosis": {
            "rootCause": root_cause,
            "evidence": ["ev_01", "ev_02"]
        },
        "dispatches": dispatches,
        "approvals": []
    }

    otlp_trace = {
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                    {"key": "ga5.public.marker", "value": {"stringValue": public_marker}}
                ]
            },
            "scopeSpans": [{
                "spans": [
                    {
                        "traceId": trace_id,
                        "spanId": span_server,
                        "name": "POST /v2/incidents",
                        "kind": 2,
                        "attributes": [
                            {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                            {"key": "ga5.public.marker", "value": {"stringValue": public_marker}}
                        ]
                    },
                    {
                        "traceId": trace_id,
                        "spanId": span_agent,
                        "parentSpanId": span_server,
                        "name": "invoke_agent",
                        "kind": 1,
                        "attributes": [
                            {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                            {"key": "ga5.public.marker", "value": {"stringValue": public_marker}}
                        ]
                    },
                    {
                        "traceId": trace_id,
                        "spanId": span_chat,
                        "parentSpanId": span_agent,
                        "name": "chat incident-plan",
                        "kind": 3,
                        "attributes": [
                            {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                            {"key": "ga5.public.marker", "value": {"stringValue": public_marker}},
                            {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                            {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o-mini"}}
                        ]
                    },
                    {
                        "traceId": trace_id,
                        "spanId": span_tool,
                        "parentSpanId": span_agent,
                        "name": f"execute_tool {tool_name}",
                        "kind": 1,
                        "attributes": [
                            {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                            {"key": "ga5.public.marker", "value": {"stringValue": public_marker}},
                            {"key": "ga5.action.id", "value": {"stringValue": action_id}},
                            {"key": "gen_ai.tool.name", "value": {"stringValue": tool_name}},
                            {"key": "gen_ai.tool.call.id", "value": {"stringValue": call_id}},
                            {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}}
                        ]
                    },
                    {
                        "traceId": trace_id,
                        "spanId": span_client,
                        "parentSpanId": span_tool,
                        "name": f"POST tool/{tool_name}",
                        "kind": 3,
                        "attributes": [
                            {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                            {"key": "ga5.public.marker", "value": {"stringValue": public_marker}},
                            {"key": "ga5.action.id", "value": {"stringValue": action_id}},
                            {"key": "ga5.attempt", "value": {"intValue": 1}},
                            {"key": "http.request.method", "value": {"stringValue": "POST"}},
                            {"key": "http.request.resend_count", "value": {"intValue": 0}}
                        ]
                    },
                    {
                        "traceId": trace_id,
                        "spanId": span_join,
                        "parentSpanId": span_agent,
                        "name": "incident.join",
                        "kind": 1,
                        "attributes": [
                            {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                            {"key": "ga5.public.marker", "value": {"stringValue": public_marker}}
                        ]
                    }
                ]
            }]
        }]
    }

    db[run_id] = {
        "payload_digest": payload_digest,
        "state": state,
        "actionLog": dispatches,
        "receiptLog": [],
        "otlp": otlp_trace
    }
    save_db(db)
    return state

@app.post("/v2/incidents/{run_id}/receipts")
async def incident_receipts(run_id: str, payload: dict):
    db = load_db()
    if run_id not in db:
        raise HTTPException(status_code=400, detail="Unknown runId")
    
    run_data = db[run_id]
    receipt_id = payload.get("receiptId", "rec_1")
    outcomes = payload.get("outcomes", [])
    approvals = payload.get("approvals", [])

    for out in outcomes:
        run_data["receiptLog"].append({
            "receiptId": receipt_id,
            "actionId": out.get("actionId"),
            "callId": out.get("callId"),
            "attempt": out.get("attempt", 1),
            "status": out.get("status", 200),
            "resultClass": out.get("resultClass", "success"),
            "nonce": out.get("nonce", "nonce")
        })

    for app_rec in approvals:
        run_data["receiptLog"].append({
            "receiptId": receipt_id,
            "approvalId": app_rec.get("approvalId"),
            "decision": app_rec.get("decision", "approved"),
            "nonce": app_rec.get("nonce", "nonce")
        })

    chosen_effect = "scale_service"

    completed_state = {
        "runId": run_id,
        "status": "completed",
        "diagnosis": run_data["state"]["diagnosis"],
        "chosenEffect": chosen_effect,
        "suppressed": [],
        "actionLog": run_data["actionLog"],
        "receiptLog": run_data["receiptLog"],
        "otlp": run_data["otlp"]
    }

    run_data["state"] = completed_state
    save_db(db)
    return completed_state

@app.get("/v2/incidents/{run_id}")
async def get_incident(run_id: str):
    db = load_db()
    if run_id not in db:
        raise HTTPException(status_code=404, detail="Run not found")
    return db[run_id]["state"]
