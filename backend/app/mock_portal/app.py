"""LOCAL MOCK employer drive portal.

Stands in for the organiser's official mock drive form, which was not
available to this project. It is a separate ASGI app with its own routes and
tables; PlacementOS reaches it only over HTTP through the destination adapter.
Every page is visibly labelled as a local mock.

Pages (HTML, as a person or browser automation would see them):
  GET  /                              list of open drive forms
  GET  /forms/{ref}                   drive form: published criteria, schedule, fields
  POST /forms/{ref}/submit            submit candidates (form-encoded)
  GET  /forms/{ref}/roster            candidates currently registered for the drive
  GET  /forms/{ref}/submissions       look up a submission by client reference
Admin (token protected, setup/testing only):
  POST /admin/forms                   create or update a form
  POST /admin/faults                  arm a fault for the next submission/read
  POST /admin/forms/{ref}/reset       clear submissions for a form
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import secrets
import time
from collections.abc import Callable, Iterator

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.mock_portal.models import MockPortalCandidate, MockPortalFault, MockPortalForm, MockPortalSubmission

FAULT_MODES = {
    "NONE", "UNAVAILABLE", "TIMEOUT_AFTER_COMMIT", "DROP_LAST", "DUPLICATE_FIRST", "INJECT_EXTRA",
    "READBACK_UNAVAILABLE",
}

_STYLE = """
body{font-family:system-ui,sans-serif;margin:0;background:#f6f5f1;color:#14213d}
header{background:#7c2d12;color:#fff;padding:10px 20px;font-size:14px}
main{max-width:880px;margin:24px auto;background:#fff;border:1px solid #e5e2da;border-radius:8px;padding:24px}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left}
label{display:block;font-weight:600;margin-top:12px}textarea,input{width:100%;font-family:monospace;padding:6px}
.badge{display:inline-block;background:#fde68a;color:#78350f;padding:2px 8px;border-radius:4px;font-size:12px}
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)} | LOCAL MOCK portal</title>"
        f"<style>{_STYLE}</style></head><body><header><strong>LOCAL MOCK</strong> employer drive portal — "
        f"simulated destination for PlacementOS demos. Not an external system.</header><main>{body}</main></body></html>",
        status_code=status,
    )


class FormIn(BaseModel):
    form_ref: str
    employer_name: str
    drive_title: str
    published_criteria: str
    schedule_text: str
    required_fields: list[str] = ["roll_number", "full_name", "branch", "cgpa"]
    is_open: bool = True


class FaultIn(BaseModel):
    form_ref: str
    mode: str
    remaining: int = 1
    extra: dict = {}


def create_mock_portal_app(session_factory: Callable[[], Session], admin_token: str) -> FastAPI:
    app = FastAPI(title="LOCAL MOCK Employer Drive Portal", docs_url=None, redoc_url=None, openapi_url=None)

    def db() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    def admin(x_mock_admin_token: str = Header(default="")) -> None:
        if not secrets.compare_digest(x_mock_admin_token, admin_token):
            raise HTTPException(status_code=401, detail="invalid admin token")

    def _form(s: Session, ref: str) -> MockPortalForm:
        form = s.scalar(select(MockPortalForm).where(MockPortalForm.form_ref == ref))
        if form is None:
            raise HTTPException(status_code=404, detail="form not found")
        return form

    def _take_fault(s: Session, ref: str, modes: set[str]) -> MockPortalFault | None:
        fault = s.scalar(select(MockPortalFault).where(MockPortalFault.form_ref == ref))
        if fault is None or fault.mode not in modes or fault.remaining <= 0:
            return None
        fault.remaining -= 1
        s.commit()
        return fault

    @app.get("/", response_class=HTMLResponse)
    def index(s: Session = Depends(db)):
        rows = "".join(
            f"<tr><td><a href='forms/{html.escape(f.form_ref)}'>{html.escape(f.drive_title)}</a></td>"
            f"<td>{html.escape(f.employer_name)}</td><td>{html.escape(f.schedule_text)}</td></tr>"
            for f in s.scalars(select(MockPortalForm).order_by(MockPortalForm.id))
        )
        return _page("Drive forms", f"<h1>Open drive forms</h1><table><tr><th>Drive</th><th>Employer</th><th>Schedule</th></tr>{rows}</table>")

    @app.get("/forms/{ref}", response_class=HTMLResponse)
    def form_page(ref: str, s: Session = Depends(db)):
        f = _form(s, ref)
        fields = ",".join(f.required_fields)
        body = (
            f"<span class='badge'>LOCAL MOCK</span><h1 id='drive-title'>{html.escape(f.drive_title)}</h1>"
            f"<p>Employer: <span id='employer-name'>{html.escape(f.employer_name)}</span></p>"
            f"<p>Schedule: <span id='schedule'>{html.escape(f.schedule_text)}</span></p>"
            f"<h2>Eligibility (as published by employer)</h2><pre id='published-criteria'>{html.escape(f.published_criteria)}</pre>"
            f"<h2>Submit candidates</h2>"
            f"<form id='candidate-form' method='post' action='{html.escape(ref)}/submit' data-status='{'open' if f.is_open else 'closed'}'>"
            f"<label for='submitted_by'>Submitted by</label><input id='submitted_by' name='submitted_by' required>"
            f"<label for='client_reference'>Your reference (idempotency key)</label><input id='client_reference' name='client_reference' required>"
            f"<label for='candidates_csv'>Candidates CSV (header: <code id='required-fields'>{html.escape(fields)}</code>)</label>"
            f"<textarea id='candidates_csv' name='candidates_csv' rows='10' required></textarea>"
            f"<button type='submit'>Submit roster</button></form>"
        )
        return _page(f.drive_title, body)

    @app.post("/forms/{ref}/submit", response_class=HTMLResponse)
    def submit(
        ref: str,
        submitted_by: str = Form(...),
        client_reference: str = Form(...),
        candidates_csv: str = Form(...),
        s: Session = Depends(db),
    ):
        f = _form(s, ref)
        if not f.is_open:
            return _page("Closed", "<h1>Form closed</h1>", 409)
        fault = _take_fault(s, ref, FAULT_MODES - {"READBACK_UNAVAILABLE"})
        mode = fault.mode if fault else "NONE"
        if mode == "UNAVAILABLE":
            return _page("Unavailable", "<h1>Service temporarily unavailable</h1>", 503)

        prior = s.scalar(select(MockPortalSubmission).where(
            MockPortalSubmission.form_id == f.id, MockPortalSubmission.client_reference == client_reference))
        if prior is not None:
            return _receipt(prior, f, duplicate=True)

        reader = csv.DictReader(io.StringIO(candidates_csv.strip()))
        missing = [c for c in f.required_fields if c not in (reader.fieldnames or [])]
        if missing:
            return _page("Rejected", f"<h1>Rejected</h1><p id='error'>Missing columns: {html.escape(', '.join(missing))}</p>", 422)
        rows = [r for r in reader if (r.get("roll_number") or "").strip()]
        claimed = len(rows)
        if mode == "DROP_LAST" and rows:
            rows = rows[: -max(1, int(fault.extra.get("count", 1)))]
        if mode == "DUPLICATE_FIRST" and rows:
            rows = [rows[0]] + rows
        if mode == "INJECT_EXTRA":
            rows = rows + [{"roll_number": fault.extra.get("roll_number", "XTRA9999"), "full_name": "Unexpected Record",
                            "branch": "CSE", "cgpa": "9.9"}]

        sub = MockPortalSubmission(
            form_id=f.id, receipt_no=f"MP-{secrets.token_hex(4).upper()}", client_reference=client_reference,
            submitted_by=submitted_by, candidate_count=claimed,
            raw_payload_sha256=hashlib.sha256(candidates_csv.encode()).hexdigest(), fault_applied=None if mode == "NONE" else mode,
        )
        s.add(sub)
        s.flush()
        for r in rows:
            s.add(MockPortalCandidate(form_id=f.id, submission_id=sub.id, roll_number=r["roll_number"].strip(),
                                      full_name=(r.get("full_name") or "").strip(), branch=r.get("branch"),
                                      cgpa=r.get("cgpa"), email=r.get("email")))
        s.commit()
        if mode == "TIMEOUT_AFTER_COMMIT":
            delay = float(fault.extra.get("sleep_seconds", 0))
            if delay:
                time.sleep(delay)
            return _page("Gateway timeout", "<h1>Gateway timeout</h1>", 504)
        return _receipt(sub, f, duplicate=False)

    def _receipt(sub: MockPortalSubmission, f: MockPortalForm, duplicate: bool) -> HTMLResponse:
        body = (
            f"<span class='badge'>LOCAL MOCK</span><h1>{'Already received' if duplicate else 'Submission received'}</h1>"
            f"<dl><dt>Receipt number</dt><dd id='receipt-no'>{sub.receipt_no}</dd>"
            f"<dt>Your reference</dt><dd id='client-reference'>{html.escape(sub.client_reference)}</dd>"
            f"<dt>Candidates received</dt><dd id='candidate-count'>{sub.candidate_count}</dd>"
            f"<dt>Received at</dt><dd id='received-at'>{sub.created_at.isoformat()}</dd>"
            f"<dt>Payload SHA-256</dt><dd id='payload-sha256'>{sub.raw_payload_sha256}</dd>"
            f"<dt>Duplicate</dt><dd id='duplicate'>{'yes' if duplicate else 'no'}</dd></dl>"
        )
        return _page("Receipt", body, 200 if duplicate else 201)

    @app.get("/forms/{ref}/roster", response_class=HTMLResponse)
    def roster(ref: str, s: Session = Depends(db)):
        f = _form(s, ref)
        if _take_fault(s, ref, {"READBACK_UNAVAILABLE"}):
            return _page("Unavailable", "<h1>Roster temporarily unavailable</h1>", 503)
        rows = s.execute(
            select(MockPortalCandidate, MockPortalSubmission.receipt_no)
            .join(MockPortalSubmission, MockPortalSubmission.id == MockPortalCandidate.submission_id)
            .where(MockPortalCandidate.form_id == f.id).order_by(MockPortalCandidate.id)
        ).all()
        trs = "".join(
            f"<tr data-roll='{html.escape(c.roll_number)}' data-receipt='{receipt}'><td>{html.escape(c.roll_number)}</td>"
            f"<td>{html.escape(c.full_name)}</td><td>{html.escape(c.branch or '')}</td><td>{receipt}</td></tr>"
            for c, receipt in rows
        )
        body = (f"<span class='badge'>LOCAL MOCK</span><h1>Registered candidates — {html.escape(f.drive_title)}</h1>"
                f"<p>Total: <span id='total-count'>{len(rows)}</span></p>"
                f"<table id='candidates'><tr><th>Roll number</th><th>Name</th><th>Branch</th><th>Receipt</th></tr>{trs}</table>")
        return _page("Roster", body)

    @app.get("/forms/{ref}/submissions", response_class=HTMLResponse)
    def lookup(ref: str, client_reference: str, s: Session = Depends(db)):
        f = _form(s, ref)
        sub = s.scalar(select(MockPortalSubmission).where(
            MockPortalSubmission.form_id == f.id, MockPortalSubmission.client_reference == client_reference))
        if sub is None:
            return _page("Not found", "<h1 id='not-found'>No submission with that reference</h1>", 404)
        return _receipt(sub, f, duplicate=True)

    @app.post("/admin/forms", dependencies=[Depends(admin)])
    def upsert_form(payload: FormIn, s: Session = Depends(db)):
        form = s.scalar(select(MockPortalForm).where(MockPortalForm.form_ref == payload.form_ref))
        if form is None:
            form = MockPortalForm(**payload.model_dump())
            s.add(form)
        else:
            for k, v in payload.model_dump().items():
                setattr(form, k, v)
        s.commit()
        return {"form_ref": form.form_ref, "id": form.id}

    @app.post("/admin/faults", dependencies=[Depends(admin)])
    def arm_fault(payload: FaultIn, s: Session = Depends(db)):
        if payload.mode not in FAULT_MODES:
            raise HTTPException(status_code=422, detail=f"mode must be one of {sorted(FAULT_MODES)}")
        fault = s.scalar(select(MockPortalFault).where(MockPortalFault.form_ref == payload.form_ref))
        if fault is None:
            fault = MockPortalFault(form_ref=payload.form_ref, mode=payload.mode)
            s.add(fault)
        fault.mode, fault.remaining, fault.extra = payload.mode, payload.remaining, payload.extra
        s.commit()
        return {"form_ref": fault.form_ref, "mode": fault.mode, "remaining": fault.remaining}

    @app.get("/admin/faults", dependencies=[Depends(admin)])
    def list_faults(s: Session = Depends(db)):
        return [{"form_ref": f.form_ref, "mode": f.mode, "remaining": f.remaining, "extra": f.extra}
                for f in s.scalars(select(MockPortalFault))]

    @app.post("/admin/forms/{ref}/reset", dependencies=[Depends(admin)])
    def reset_form(ref: str, s: Session = Depends(db)):
        f = _form(s, ref)
        s.execute(delete(MockPortalCandidate).where(MockPortalCandidate.form_id == f.id))
        s.execute(delete(MockPortalSubmission).where(MockPortalSubmission.form_id == f.id))
        s.commit()
        return {"form_ref": ref, "reset": True}

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        if request.url.path.startswith("/admin") or "/admin/" in request.url.path:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return _page("Error", f"<h1>{exc.status_code}</h1><p>{html.escape(str(exc.detail))}</p>", exc.status_code)

    return app
