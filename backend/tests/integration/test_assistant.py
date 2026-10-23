"""Ask PlaceCrew: Gemini plans with read-only tools; actions are only proposed; rules answer when AI is unavailable."""
from app.agent import assistant
from app.models.entities import AuditEvent
from app.models.enums import Role
from app.services import tour


class FakeGemini:
    """Speaks the generateContent shape: first asks for a tool, then answers in text."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def generate(self, *, system, contents, declarations, mode="ANY", allowed=None):
        self.calls.append({"last": contents[-1], "tools": [d["name"] for d in declarations], "mode": mode})
        return {"candidates": [{"content": self.script.pop(0)}]}


def _sandbox_with_lists():
    t = tour.Tour()
    t.sb.activate()
    for _key, fn in tour.STEPS[:5]:  # institute, two drives, answers, the officer's lists
        fn(t)
    return t


def test_ai_answers_with_tools_and_only_proposes_actions():
    t = _sandbox_with_lists()
    try:
        fake = FakeGemini([
            {"role": "model", "parts": [{"functionCall": {"name": "explain_student", "args": {"student": "Farah", "drive": "Nimbus"}}}]},
            {"role": "model", "parts": [{"functionCall": {"name": "propose_action",
                                                          "args": {"action": "send_personal_forms", "drive": "Nimbus",
                                                                   "reason": "Three students could still qualify"}}}]},
            {"role": "model", "parts": [{"text": "Farah's CGPA 6.4 is below Nimbus's 7.0. I suggest sending personal forms."}]},
        ])
        user = t.state["people"][Role.APPROVER]
        r = assistant.ask(t.sb.db, user, "Why isn't Farah on the Nimbus list?", client=fake)
        assert r["mode"] == "AI" and "6.4" in r["answer"]
        assert [s["tool"] for s in r["steps"]] == ["explain_student", "propose_action"]
        assert r["proposals"][0]["action"] == "send_personal_forms" and r["proposals"][0]["students"] == 3
        assert "approve" not in " ".join(fake.calls[0]["tools"])  # no tool that changes anything is offered
        tool_reply = fake.calls[1]["last"]["parts"][0]["functionResponse"]["response"]
        assert "CGPA 6.40 below minimum 7.00" in tool_reply["drives"][0]["reasons"][0]
        assert t.sb.db.query(AuditEvent).filter(AuditEvent.action == "ASSISTANT_ASKED").count() == 1
    finally:
        t.sb.deactivate()
        t.sb.close()


def test_rules_answer_when_ai_is_unavailable():
    t = _sandbox_with_lists()
    try:
        user = t.state["people"][Role.OFFICER]
        r = assistant.ask(t.sb.db, user, "What if Nimbus lowered the CGPA to 6.5?")
        assert r["mode"] == "RULES" and r["ai_error"] and "Kabir Singh (22CSE006)" in r["answer"]
        r = assistant.ask(t.sb.db, user, "Who still needs attention for Nimbus?")
        assert "Diya Menon (22CSE003) (missing CGPA)" in r["answer"]
        r = assistant.ask(t.sb.db, user, "Who still needs attention for the next drive?")
        assert "Diya Menon (22CSE003)" in r["answer"]
        r = assistant.ask(t.sb.db, user, "prepare the list for Orion")
        assert r["proposals"][0]["action"] == "prepare_list" and "Orion" in r["proposals"][0]["drive"]
    finally:
        t.sb.deactivate()
        t.sb.close()


def test_slow_ai_falls_back_to_rules_in_time(monkeypatch):
    t = _sandbox_with_lists()
    try:
        monkeypatch.setattr(assistant, "ASK_BUDGET_SECONDS", 0)
        fake = FakeGemini([{"role": "model", "parts": [{"text": "never reached"}]}])
        user = t.state["people"][Role.OFFICER]
        r = assistant.ask(t.sb.db, user, "Who still needs attention for Nimbus?", client=fake)
        assert r["mode"] == "RULES" and r["ai_error"] == "the AI service did not answer in time"
        assert "Diya Menon (22CSE003)" in r["answer"] and fake.calls == []
    finally:
        t.sb.deactivate()
        t.sb.close()


def test_approver_summary_is_written_by_gemini_or_from_the_checks():
    from app.services import approval_review, approvals

    t = _sandbox_with_lists()
    try:
        approval = approvals.get_approval(t.sb.db, t.state["first_approval"])
        review = approval_review.review(t.sb.db, approval)
        plain = approval_review.plain_summary(review, approval)
        assert plain["mode"] == "RULES" and "3 more could still qualify" in plain["text"] and "Diya Menon" in plain["text"]

        class FakeText:
            def generate_text(self, system, prompt):
                assert "Diya Menon (22CSE003): missing CGPA" in prompt  # Gemini only sees the review's facts
                return "Three students are on the list; Diya, Charan and Eshan could still qualify."

        ai = approval_review.plain_summary(review, approval, client=FakeText())
        assert ai["mode"] == "AI" and ai["text"].startswith("Three students")
    finally:
        t.sb.deactivate()
        t.sb.close()


def test_busy_model_retries_then_uses_the_backup_model(monkeypatch):
    import httpx

    from app.agent import gemini

    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-flash")
    monkeypatch.setattr(gemini.time, "sleep", lambda _s: None)
    seen = []

    def handler(request):
        model = request.url.path.split("/models/")[1].split(":")[0]
        seen.append(model)
        if model == "main-flash":
            return httpx.Response(503, json={"error": {"message": "This model is currently experiencing high demand."}})
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}}]})

    client = gemini.GeminiClient(api_key="k", model="main-flash", transport=httpx.MockTransport(handler))
    assert gemini.generate_text(client, "s", "p") == "OK"
    assert seen == ["main-flash", "main-flash", "backup-flash"] and client.used_model == "backup-flash"

    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    try:
        gemini.generate_text(gemini.GeminiClient(api_key="k", model="main-flash", transport=httpx.MockTransport(handler)), "s", "p")
    except gemini.GeminiError as exc:
        assert gemini.friendly_error(exc) == "the Gemini model is busy at Google right now (high demand)"
    else:
        raise AssertionError("expected the busy error")


def test_low_thinking_is_requested_and_dropped_for_models_without_it(monkeypatch):
    import json

    import httpx

    from app.agent import gemini

    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "generationConfig" in body:
            return httpx.Response(400, json={"error": {"message": "Thinking level is not supported for this model."}})
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}}]})

    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    client = gemini.GeminiClient(api_key="k", model="m", transport=httpx.MockTransport(handler))
    assert gemini.generate_text(client, "s", "p") == "OK"
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "generationConfig" not in bodies[1] and client.thinking_level == ""


def test_instant_rules_answer_is_not_logged():
    t = _sandbox_with_lists()
    try:
        user = t.state["people"][Role.OFFICER]
        r = assistant.ask(t.sb.db, user, "Who still needs attention for the next drive?", mode="rules")
        assert r["mode"] == "RULES" and r["ai_error"] is None and "Diya Menon (22CSE003)" in r["answer"]
        assert t.sb.db.query(AuditEvent).filter(AuditEvent.action == "ASSISTANT_ASKED").count() == 0
    finally:
        t.sb.deactivate()
        t.sb.close()


def test_failed_follow_up_call_still_answers_from_the_lookups():
    t = _sandbox_with_lists()
    try:
        class Flaky(FakeGemini):
            def generate(self, **kw):
                if self.calls:
                    raise RuntimeError("Gemini API returned HTTP 500: internal error")
                return super().generate(**kw)

            def generate_text(self, *, system, prompt):
                assert "CGPA 6.40 below minimum 7.00" in prompt
                return "Farah's CGPA of 6.4 is below the 7.0 minimum."

        fake = Flaky([{"role": "model", "parts": [{"functionCall": {"name": "explain_student", "args": {"student": "Farah"}}}]}])
        r = assistant.ask(t.sb.db, t.state["people"][Role.APPROVER], "Why isn't Farah on the list?", client=fake)
        assert r["mode"] == "AI" and "6.4" in r["answer"] and [s["tool"] for s in r["steps"]] == ["explain_student"]
    finally:
        t.sb.deactivate()
        t.sb.close()
