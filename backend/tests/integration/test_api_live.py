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
    # Destination page really contains the uploaded roster (independent of PlaceCrew).
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


def test_invitation_link_round_trip_over_http(server):
    officer = login("officer@placementos.demo")
    bluepeak = next(d for d in officer.get("/api/drives").json() if d["title"].startswith("Bluepeak"))
    assert bluepeak["criteria"]["required_consent_scope"] == "DRIVE_REGISTRATION"
    sent = officer.post(f"/api/drives/{bluepeak['id']}/invitations").json()["sent"]
    assert sent
    mail = officer.get("/api/emails?category=DRIVE_INVITATION").json()["items"][0]
    body = officer.get(f"/api/emails/{mail['id']}").json()["body_text"]
    token = body.split("/respond/")[1].split()[0]
    anon = httpx.Client(base_url=server)
    view = anon.get(f"/api/public/requests/{token}").json()
    assert view["kind"] == "DRIVE_INVITATION" and view["status"] == "SENT"
    assert anon.post(f"/api/public/requests/{token}/invitation", json={"accept": True}).json()["status"] == "ACCEPTED"
    assert anon.get("/api/public/requests/not-a-real-token").status_code == 404
    assert anon.get("/api/emails").status_code == 401


def test_company_inbox_and_crew_over_http(server):
    """Stage 1 over HTTP: the drive upload officer loads sample company emails, the agents process them."""
    coordinator, approver, viewer = login("drives@placementos.demo"), login("approver@placementos.demo"), login("viewer@placementos.demo")
    assert viewer.post("/api/inbox/demo").status_code == 403
    assert coordinator.post("/api/inbox/demo").json()["added"] == 3
    result = coordinator.post("/api/inbox/check").json()
    assert result["mailbox"]["configured"] is False  # no Gmail in tests: nothing fetched, but new emails are processed
    statuses = sorted(p["status"] for p in result["processed"])
    assert statuses == ["COMPLETED", "COMPLETED", "NEEDS_REVIEW"], result
    inbox = coordinator.get("/api/inbox").json()
    zentrix = next(e for e in inbox["items"] if "Zentrix" in e["subject"])
    assert zentrix["status"] == "NEEDS_REVIEW" and {i["code"] for i in zentrix["issues"]} >= {"NEW_COMPANY", "SUSPICIOUS"}
    answered = coordinator.post(f"/api/agent/runs/{zentrix['run']['id']}/answer", json={"value": "confirm"})
    assert answered.status_code == 200 and answered.json()["status"] == "COMPLETED"
    drive = coordinator.get(f"/api/drives/{answered.json()['drive_id']}").json()
    assert drive["consent_form"]["audience"] == "ALL_STUDENTS" and drive["consent_form"]["sent"] == 34
    assert drive["registration_deadline"] and drive["destination"]["is_local_mock"] is True

    crew = viewer.get("/api/agents").json()
    assert len(crew["agents"]) == 21 and crew["ai"]["provider"] == "none"
    assert any(a["recent"] for a in crew["agents"] if a["key"] == "email_reader")
    assert approver.get("/api/integrations/google").json()["connected"] is False
    assert approver.post("/api/integrations/google/connect").status_code == 403  # only coordinator/admin connect
    assert coordinator.post("/api/integrations/google/connect").status_code == 412  # not configured in tests
    # Approver review + follow-up endpoints on a fresh proposal
    officer = login("officer@placementos.demo")
    run = officer.post("/api/agent/runs", json={"objective": f"Prepare the eligible roster for drive #{drive['id']}"}).json()
    assert run["status"] == "AWAITING_APPROVAL", run
    review = approver.get(f"/api/approvals/{run['state']['approval_id']}/review").json()
    assert review["verdict"] in ("READY", "READY_WITH_NOTES") and review["followup_candidates"] > 0
    assert viewer.get(f"/api/approvals/{run['state']['approval_id']}/review").status_code == 403
    students = officer.get(f"/api/students?drive_id={drive['id']}&drive_status=consent_pending&page_size=200").json()
    assert students["total"] == 34 and students["items"][0]["drive"]["consent"] is None


def test_ai_check_is_admin_only_and_says_why(server):
    assert login("officer@placementos.demo").post("/api/integrations/ai-check").status_code == 403
    r = login("admin@placementos.demo").post("/api/integrations/ai-check")
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"]  # the test server has no Gemini key


def test_ai_speed_is_admin_only(server):
    assert login("approver@placementos.demo").post("/api/integrations/ai-speed").status_code == 403
    r = login("admin@placementos.demo").post("/api/integrations/ai-speed")
    assert r.status_code == 200 and r.json() == {"results": [], "error": "no Gemini key is configured"}
