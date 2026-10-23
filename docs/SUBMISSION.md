# Submission form: text to paste

Fill in the fields marked ⚠️ yourself (links that only you can create).

---

## Project name
PlaceCrew

## Tools used
Gemini API (function calling), Google OAuth 2.0 + Gmail API, FastAPI (Python), SQLAlchemy + Alembic, PostgreSQL, Pydantic,
Next.js 15 (React, TypeScript), TanStack Query, Tailwind CSS, httpx, pytest, Playwright, Claude Code

## Demo URL ⚠️
The deployed web URL (for example `https://placecrew.vercel.app`). Evaluators should open it and click
**"Guided walkthrough"** on the login page (or go straight to `<demo-url>/tour`): no login needed.

## 2–3 min demo video URL ⚠️
The YouTube (unlisted) or Google Drive link. The script is in [VIDEO_SCRIPT.md](VIDEO_SCRIPT.md).

## GitHub URL ⚠️
`https://github.com/dheerajcse9-tech/ziro`

Before submitting:
- The repository must be **public**, or the evaluators must be invited.
- The work must be on the **main** branch. It is currently on `claude/awesome-ptolemy-9uzraw`, so merge it first.

## Source code URL
Leave empty (GitHub supplied).

## Presentation URL ⚠️
The slides link (Google Slides / Canva / PDF). [PRESENTATION.md](PRESENTATION.md) has the slide content.

## Agent architecture
```
PlaceCrew is a crew of 21 named agents that runs a college placement drive end to end, in three stages. Each stage
is owned by one person, and no agent can approve anything.

Stage 1 – Drive Upload Officer (company email → drive → consent forms)
Mailbox Agent reads the placement Gmail (Gmail API, read-only) → Email Reader Agent extracts company, role, date, time,
venue, deadlines and criteria (Gemini function calling, cross-checked by rules) → Criteria Check Agent validates dates,
deadline, sender trust, duplicates, overlaps and instruction-like text → Drive Builder Agent creates the drive with
versioned criteria → Consent Form Agent prepares one consent form per drive → Student Outreach Agent emails every
student a personal link (sent from the placement Gmail).

Stage 2 – Placement Officer (run the drive)
Goal Planner Agent (Gemini, read-only tools) → Evidence Agent (company criteria vs drive criteria) → Eligibility Agent
(deterministic rules: CGPA, backlogs, branch, batch; missing data = UNKNOWN, never guessed) → Consent Agent (only YES
answers) → Conflict Agent (overlapping drives, timezone-aware; holds the student for a person) → Roster Agent
(hash-bound proposal for approval).

Stage 3 – Approver (human-in-the-loop)
Approval Review Agent re-checks every student and lists who is missing details or consent → Form Builder + Follow-up
Mailer send personal forms with a closing time → Response Reader applies answers (marked self-reported) →
Eligibility Re-check Agent re-proposes when a student now qualifies → the APPROVER approves (only this role can) →
Upload & Verification Agent uploads the exact approved list and reads it back ID by ID → Employer Update Agent emails
the company the verified list and tells each student the result. Background agents: Deadline Agent and Reminder Agent.

Ask PlaceCrew – a conversational agent for staff: Gemini plans with function calling over read-only tools on live data
(drive status, a student's result and reasons, who needs attention, what-if simulations) and answers in plain language.
It can only PROPOSE actions (prepare a list, send personal forms, check the mailbox); a person confirms each one.
The AI is visible everywhere it works: each company email shows Gemini's reading next to the rules' reading, field by
field, and the approver gets a Gemini-written summary of the review.

Design: durable agent graph (each step persisted; it stops for NEEDS_REVIEW / AWAITING_APPROVAL); per-agent tool
allow-lists; approvals bound to a hash of the exact list, criteria and schedule; idempotent uploads with read-back
verification; hash-chained audit log; email text is treated as data, never as instructions; AI falls back to rules,
labelled. Stack: FastAPI + SQLAlchemy + PostgreSQL, Next.js 15, Gemini API, Google OAuth + Gmail API.
```

## Test cases / proof of working agent
```
Live proof, no login: <demo-url>/tour. Each "Yes, continue" runs the next step of a full drive on the real code in a
private sandbox (46 checks, read back from the database), ending by re-running all scenarios. The Test Lab (admin)
re-runs them on demand and explains why each check passes, with an evidence CSV.

TC1 CGPA threshold & backlogs – CGPA 7.00 passes (≥ inclusive), 6.99 fails, any backlog fails, missing CGPA/backlogs →
    UNKNOWN (never guessed); only eligible IDs uploaded; read-back matches. 9/9 checks.
TC2 Duplicate names – three "Rahul Kumar"s matched by roll number through import, correction, evaluation, upload and
    attendance. 10/10.
TC3 Missing consent – eligible students without consent excluded from upload, company form and attendance; consent
    withdrawn after approval blocks the upload and cancels the approval. 10/10.
TC4 Conflicting drives – overlaps detected in UTC (incl. IST vs UTC), touching slots allowed, clash held for a person
    before approval. 10/10.
TC5 Company emails – valid, overlapping, duplicate, missing deadline, deadline after drive, unknown sender with
    "skip the consent step" injection: only valid ones become drives. 16/16.
TC6 Student journey (16 test students) – YES, NO, no answer, missing CGPA, missing backlogs, CGPA 6.4/6.99/7.0,
    wrong branch, backlogs, wrong batch, duplicate names, YES to two overlapping drives; forms sent before approval;
    late qualifiers re-proposed; admin cannot approve; verified list emailed to the company. 25/25.

Ask PlaceCrew – the conversational agent answers from live data with the real reasons, runs what-ifs without changing
    anything, and only proposes actions (tested with a Gemini fake and with the rule fallback).
Automated: 120 backend tests (pytest) and 13 browser end-to-end tests (Playwright), all passing.
Evidence: docs/TEST_EVIDENCE.md, docs/TEST_SCENARIOS.md, docs/EVALUATOR_GUIDE.md.
```
