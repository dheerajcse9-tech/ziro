"""Destination adapters for employer drive forms.

`DriveFormAdapter` is the only interface the core application uses to talk to
an upload destination. `HtmlDriveFormAdapter` drives a server-rendered HTML
form over plain HTTP (form-encoded POST, HTML read-back), exactly as a browser
would, and is what the LOCAL MOCK portal implements. A Playwright-based adapter
for a JavaScript-heavy organiser form can implement the same protocol without
touching the domain code.

The adapter never decides *what* to upload; it only transmits an already
approved payload and reports observations.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import quote

import httpx


class DestinationError(Exception):
    """Definite failure: nothing was accepted (e.g. 4xx/503 before commit)."""


class DestinationUncertain(Exception):
    """Unknown outcome (timeout, 504, connection drop after send). Must reconcile before retrying."""


class DestinationUnreadable(Exception):
    """The destination state could not be read back."""


@dataclass
class FormDefinition:
    form_ref: str
    url: str
    drive_title: str
    employer_name: str
    schedule_text: str
    published_criteria: str
    required_fields: list[str]
    is_open: bool
    raw_html_sha256: str


@dataclass
class SubmissionReceipt:
    receipt_no: str
    client_reference: str
    candidate_count: int
    received_at: str | None
    payload_sha256: str | None
    duplicate: bool
    http_status: int


@dataclass
class ObservedRoster:
    url: str
    roll_numbers: list[str]  # in page order, duplicates preserved
    total_count_label: int | None
    rows: list[dict] = field(default_factory=list)


class DriveFormAdapter(Protocol):
    name: str

    def describe_form(self, form_ref: str) -> FormDefinition: ...
    def submit(self, form_ref: str, rows: list[dict], *, client_reference: str, submitted_by: str) -> SubmissionReceipt: ...
    def find_submission(self, form_ref: str, client_reference: str) -> SubmissionReceipt | None: ...
    def read_roster(self, form_ref: str) -> ObservedRoster: ...


class _Extractor(HTMLParser):
    """Collects text by element id, form fields and roster table rows."""

    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, str] = {}
        self.attrs_by_id: dict[str, dict[str, str]] = {}
        self.input_names: list[str] = []
        self.rows: list[dict[str, str]] = []
        self._stack: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag in {"input", "textarea"} and a.get("name"):
            self.input_names.append(a["name"])
        if tag == "tr" and "data-roll" in a:
            self.rows.append({"roll_number": a["data-roll"], "receipt": a.get("data-receipt", "")})
        if tag in {"input", "br", "meta"}:
            return
        el_id = a.get("id")
        if el_id:
            self.by_id.setdefault(el_id, "")
            self.attrs_by_id[el_id] = a
        self._stack.append(el_id)

    def handle_endtag(self, tag):
        if tag in {"input", "br", "meta"}:
            return
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        for el_id in self._stack:
            if el_id:
                self.by_id[el_id] += data


def _parse(text: str) -> _Extractor:
    p = _Extractor()
    p.feed(text)
    return p


class HtmlDriveFormAdapter:
    name = "html-form-http"

    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def describe_form(self, form_ref: str) -> FormDefinition:
        import hashlib

        url = self._url(f"/forms/{quote(form_ref)}")
        try:
            r = self._client.get(url)
        except httpx.HTTPError as exc:
            raise DestinationUnreadable(f"Form page unreachable: {exc}") from exc
        if r.status_code != 200:
            raise DestinationUnreadable(f"Form page returned HTTP {r.status_code}")
        p = _parse(r.text)
        fields = [f.strip() for f in p.by_id.get("required-fields", "").split(",") if f.strip()]
        form_attrs = p.attrs_by_id.get("candidate-form", {})
        return FormDefinition(
            form_ref=form_ref, url=url, drive_title=p.by_id.get("drive-title", "").strip(),
            employer_name=p.by_id.get("employer-name", "").strip(), schedule_text=p.by_id.get("schedule", "").strip(),
            published_criteria=p.by_id.get("published-criteria", "").strip(), required_fields=fields,
            is_open=form_attrs.get("data-status") == "open" and "client_reference" in p.input_names,
            raw_html_sha256=hashlib.sha256(r.content).hexdigest(),
        )

    @staticmethod
    def _receipt(text: str, status: int) -> SubmissionReceipt:
        p = _parse(text)
        receipt = p.by_id.get("receipt-no", "").strip()
        if not receipt:
            raise DestinationUncertain(f"HTTP {status} without a receipt number")
        return SubmissionReceipt(
            receipt_no=receipt, client_reference=p.by_id.get("client-reference", "").strip(),
            candidate_count=int(p.by_id.get("candidate-count", "0").strip() or 0),
            received_at=p.by_id.get("received-at", "").strip() or None,
            payload_sha256=p.by_id.get("payload-sha256", "").strip() or None,
            duplicate=p.by_id.get("duplicate", "").strip() == "yes", http_status=status,
        )

    def submit(self, form_ref: str, rows: list[dict], *, client_reference: str, submitted_by: str) -> SubmissionReceipt:
        form = self.describe_form(form_ref)
        if not form.is_open:
            raise DestinationError("Destination form is closed or does not accept a client reference")
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=form.required_fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in form.required_fields})
        data = {"submitted_by": submitted_by, "client_reference": client_reference, "candidates_csv": buf.getvalue()}
        try:
            r = self._client.post(self._url(f"/forms/{quote(form_ref)}/submit"), data=data)
        except httpx.TimeoutException as exc:
            raise DestinationUncertain(f"Timed out waiting for destination: {exc}") from exc
        except httpx.TransportError as exc:
            raise DestinationUncertain(f"Connection error after sending: {exc}") from exc
        if r.status_code in (200, 201):
            return self._receipt(r.text, r.status_code)
        if r.status_code in (502, 504):
            raise DestinationUncertain(f"Destination returned HTTP {r.status_code}; outcome unknown")
        raise DestinationError(f"Destination rejected submission with HTTP {r.status_code}")

    def find_submission(self, form_ref: str, client_reference: str) -> SubmissionReceipt | None:
        try:
            r = self._client.get(self._url(f"/forms/{quote(form_ref)}/submissions"),
                                 params={"client_reference": client_reference})
        except httpx.HTTPError as exc:
            raise DestinationUnreadable(f"Submission lookup failed: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise DestinationUnreadable(f"Submission lookup returned HTTP {r.status_code}")
        return self._receipt(r.text, r.status_code)

    def read_roster(self, form_ref: str) -> ObservedRoster:
        url = self._url(f"/forms/{quote(form_ref)}/roster")
        try:
            r = self._client.get(url)
        except httpx.HTTPError as exc:
            raise DestinationUnreadable(f"Roster read failed: {exc}") from exc
        if r.status_code != 200:
            raise DestinationUnreadable(f"Roster page returned HTTP {r.status_code}")
        p = _parse(r.text)
        label = p.by_id.get("total-count", "").strip()
        return ObservedRoster(url=url, roll_numbers=[row["roll_number"] for row in p.rows],
                              total_count_label=int(label) if label.isdigit() else None, rows=p.rows)


class MockPortalAdmin:
    """Setup/testing client for the LOCAL MOCK portal's admin endpoints."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=10)
        self._prefix = self.base_url
        self._headers = {"X-Mock-Admin-Token": token}

    def upsert_form(self, **payload) -> dict:
        r = self._client.post(f"{self._prefix}/admin/forms", json=payload, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def arm_fault(self, form_ref: str, mode: str, remaining: int = 1, extra: dict | None = None) -> dict:
        r = self._client.post(f"{self._prefix}/admin/faults", headers=self._headers,
                              json={"form_ref": form_ref, "mode": mode, "remaining": remaining, "extra": extra or {}})
        r.raise_for_status()
        return r.json()

    def list_faults(self) -> list[dict]:
        r = self._client.get(f"{self._prefix}/admin/faults", headers=self._headers)
        r.raise_for_status()
        return r.json()

    def reset_form(self, form_ref: str) -> dict:
        r = self._client.post(f"{self._prefix}/admin/forms/{quote(form_ref)}/reset", headers=self._headers)
        r.raise_for_status()
        return r.json()
