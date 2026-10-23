"""The evaluator walkthrough: every step runs on the real code in its own sandbox and passes its checks."""
from fastapi.testclient import TestClient

from app.main import app
from app.services import mailer


def test_walkthrough_runs_every_step_without_login_and_without_real_email(monkeypatch):
    monkeypatch.setattr(mailer, "_deliver_smtp", lambda msg: (_ for _ in ()).throw(AssertionError("SMTP used")))
    client = TestClient(app)
    start = client.get("/api/tour").json()
    assert start["total"] == len(start["outline"]) and start["agents"]
    steps = []
    while True:
        r = client.post(f"/api/tour/steps/{len(steps)}")
        assert r.status_code == 200
        step = r.json()
        steps.append(step)
        assert step["passed"], (step["title"], step.get("error"), [c for c in step["checks"] if not c["passed"]])
        if step["finished"]:
            break
    assert len(steps) == start["total"]
    assert client.post(f"/api/tour/steps/{start['total']}").status_code == 404


def test_guide_maps_every_step_and_feature():
    from app.services import tour

    assert set(tour.STEP_INFO) == {key for key, _fn in tour.STEPS} and len(tour.OUTLINE) == len(tour.STEPS)
    ids = {f["id"] for f in tour.FEATURES}
    assert all(set(info["features"]) <= ids for info in tour.STEP_INFO.values())
    start = tour.start()
    assert all(tc["steps"] for tc in start["test_cases"])  # every test case is shown in some step
    shown = {f["id"] for f in start["features"] if f["steps"]}
    assert ids - shown == {"settings"}  # everything but Settings is shown in the walkthrough itself
