from fastapi import FastAPI, HTTPException
import hashlib
import json
import os

app = FastAPI()

STORAGE_FILE = "incidents.json"

def load_data():
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(data):
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
    
    store = load_data()
    if run_id in store:
        if store[run_id]["payload_digest"] != payload_digest:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_CONFLICT")
        return store[run_id]["state"]

    allowed_causes = incident.get("allowedRootCauses", ["unknown"])
    root_cause = allowed_causes[0] if allowed_causes else "unknown"
    
    tool_name = "query_metrics"
    for t in tool_catalog:
        if t["name"] not in policy.get("effectTools", []):
            tool_name = t["name"]
            break

    action_id = hashlib.sha256(f"{run_id}-act-1".encode()).hexdigest()[:16]
    call_id = hashlib.sha256(f"{run_id}-call-1".encode()).hexdigest()[:16]
    trace_id = hashlib.sha256(run_id.encode()).hexdigest()[:32]
    span_id = hashlib.sha256((run_id + "span").encode()).hexdigest()[:16]

    dispatches = [{
        "actionId": action_id,
        "callId": call_id,
        "phase": "diagnostic",
        "toolName": tool_name,
        "arguments": {"service": incident.get("service", "core")},
        "evidence": ["ev_01", "ev_02"],
        "attempt": 1,
        "traceparent": f"00-{trace_id}-{span_id}-01"
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
                "spans": [{
                    "traceId": trace_id,
                    "spanId": span_id,
                    "name": "invoke_agent",
                    "kind": 1,
                    "attributes": [
                        {"key": "ga5.run.id", "value": {"stringValue": run_id}},
                        {"key": "ga5.public.marker", "value": {"stringValue": public_marker}}
                    ]
                }]
            }]
        }]
    }

    store[run_id] = {
        "payload_digest": payload_digest,
        "state": state,
        "actionLog": dispatches,
        "receiptLog": [],
        "otlp": otlp_trace
    }
    save_data(store)
    return state

@app.post("/v2/incidents/{run_id}/receipts")
async def incident_receipts(run_id: str, payload: dict):
    store = load_data()
    if run_id not in store:
        raise HTTPException(status_code=400, detail="Unknown runId")
    
    run_data = store[run_id]
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
    save_data(store)
    return completed_state

@app.get("/v2/incidents/{run_id}")
async def get_incident(run_id: str):
    store = load_data()
    if run_id not in store:
        raise HTTPException(status_code=404, detail="Run not found")
    return store[run_id]["state"]
