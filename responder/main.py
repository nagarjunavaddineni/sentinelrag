import base64
import json
import os

from fastapi import FastAPI, Request
from google.cloud import run_v2

TARGET = os.environ.get("TARGET_SERVICE", "")  # projects/P/locations/R/services/NAME
app = FastAPI(title="SentinelRAG responder")


def log(**fields):
    print(json.dumps(fields), flush=True)


def _short(name):
    return name.split("/")[-1]


def _is_healthy(rev):
    for c in rev.containers:
        for e in c.env:
            if e.name == "FAILURE_RATE" and e.value not in ("", "0"):
                return False
    return True


def rollback():
    services = run_v2.ServicesClient()
    svc = services.get_service(name=TARGET)
    current = _short(svc.latest_ready_revision)
    candidates = sorted(
        (r for r in run_v2.RevisionsClient().list_revisions(parent=TARGET)
         if _short(r.name) != current and _is_healthy(r)),
        key=lambda r: r.create_time, reverse=True,
    )
    if not candidates:
        log(severity="ERROR", message="rollback_skipped", reason="no healthy revision")
        return {"action": "none"}
    target = _short(candidates[0].name)
    svc.traffic = [run_v2.TrafficTarget(
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
        revision=target, percent=100,
    )]
    services.update_service(service=svc).result()
    log(severity="NOTICE", message="rollback_done", from_revision=current, to_revision=target)
    return {"action": "rollback", "to": target}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/")
async def on_alert(request: Request):
    envelope = await request.json()
    payload = json.loads(base64.b64decode(envelope["message"]["data"]))
    incident = payload.get("incident", {})
    log(severity="WARNING", message="alert_received",
        policy=incident.get("policy_name"), state=incident.get("state"))
    if incident.get("state") != "open":
        return {"action": "none"}
    return rollback()
