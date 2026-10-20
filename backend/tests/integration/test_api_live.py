"""End-to-end over HTTP against a real uvicorn server (API + mounted LOCAL MOCK portal)."""
import os
import threading
import time

import httpx
import pytest
import uvicorn

PORT = int(os.environ["API_TEST_PORT"])
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server():
    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    from app.main import app
    from app.services.seed import seed

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            if httpx.get(f"{BASE}/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield BASE
    srv.should_exit = True
    t.join(timeout=5)


def login(email):
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/api/auth/login", json={"email": email, "password": "Demo@2026"})
    assert r.status_code == 200, r.text
    return c


def test_auth_required_and_roles(server):
    assert httpx.get(f"{server}/api/students").status_code == 401
    viewer = login("viewer@placementos.demo")
    assert viewer.get("/api/students").status_code == 200
    assert viewer.post("/api/agent/runs", json={"objective": "Prepare roster"}).status_code == 403
    assert httpx.post(f"{server}/api/auth/login", json={"email": "officer@placementos.demo", "password": "wrong"}).status_code == 401


def test_full_workflow_over_http(server):
    officer, approver = login("officer@placementos.demo"), login("approver@placementos.demo")
    run = officer.post("/api/agent/runs", json={"objective": "Prepare the eligible roster for the upcoming drive, "
                                                              "identify unresolved records, and show me the proposal for approval."}).json()
    assert run["status"] == "AWAITING_APPROVAL", run
    approval = officer.get(f"/api/approvals/{run['state']['approval_id']}").json()
    assert approval["is_current"] is True
    assert officer.post(f"/api/approvals/{approval['id']}/approve", json={"proposal_hash": approval["proposal_hash"]}).status_code == 403
    r = approver.post(f"/api/approvals/{approval['id']}/approve", json={"proposal_hash": approval["proposal_hash"], "note": "ok"})
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["status"] == "VERIFIED", run
    upload_id = run["state"]["upload_id"]
    receipt = officer.get(f"/api/uploads/{upload_id}/receipt").json()
    assert receipt["verification"]["status"] == "VERIFIED"
    assert receipt["verification"]["observed_count"] == receipt["counts"]["eligible_uploaded"]
    sheet_id = run["state"]["attendance_sheet_id"]
    csv_resp = officer.get(f"/api/attendance/{sheet_id}/download?format=csv")
    assert csv_resp.status_code == 200 and csv_resp.text.count("\n") == receipt["counts"]["eligible_uploaded"] + 1
    assert officer.get(f"/api/attendance/{sheet_id}/download?format=xlsx").content[:2] == b"PK"
    # Destination page really contains the uploaded roster (independent of PlacementOS).
    page = httpx.get(f"{server}/mock-portal/forms/{receipt['destination']['form_ref']}/roster").text
    assert all(f"data-roll='{r}'" in page for r in receipt["uploaded_roll_numbers"])
    events = officer.get(f"/api/agent/runs/{run['id']}/events").json()
    assert {"execute_approved_upload", "verify_uploaded_roster", "generate_attendance_sheet"} <= {e["tool_name"] for e in events}
    assert officer.get("/api/audit/verify").json()["valid"] is True
    dash = officer.get("/api/dashboard").json()
    assert dash["metrics"]["total_students"] == 34


def test_test_lab_endpoint(server):
    officer = login("officer@placementos.demo")
    results = officer.post("/api/tests/run", json={"test_ids": ["TC1"]}).json()
    assert results[0]["passed"] is True
    assert officer.get("/api/tests").json()["tests"][0]["latest"]["test_id"] == "TC1"
