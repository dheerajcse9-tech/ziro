# PlaceCrew — a crew of AI agents that runs placement drives; people approve

**Ziro Digital Agentic AI Hackathon 2026 · Problem statement: Placement Drive Coordinator**

**Live demo: https://placecrew.vercel.app**

> **Evaluators:** click **"Guided walkthrough"** on the login page (or open `/tour`). No login: press
> **Play it automatically** (or "Yes, continue") and the real agents run a whole drive in 12 steps, ending with every
> test scenario. See [docs/EVALUATOR_GUIDE.md](docs/EVALUATOR_GUIDE.md).

A crew of 21 named agents runs the placement process in three stages, each owned by a person:

1. **Drive upload officer:** a company emails the placement Gmail. Agents read the email (Gemini + rules), check every
   detail and the deadline, create the drive, prepare its consent form and email it to every student.
2. **Placement officer:** runs the drive. Agents apply CGPA, backlog, branch and batch rules, keep only students who
   said YES, detect schedule clashes and propose the roster.
3. **Approver:** an agent re-checks the proposal; students missing details or consent get a personal form, and their
   answers are re-checked and added. After human approval the exact roster is uploaded, **read back and verified**, an
   attendance sheet is created, and the verified list is emailed to the company.

Staff pages also have **Ask PlaceCrew**, a conversational agent (Gemini function calling over read-only tools) that
answers from live data, runs what-ifs and only proposes actions.

```
company email ─► read (Gemini + rules) ─► checks ─► drive + consent forms ─► students answer
   ─► eligibility + consent + clash detection ─► hashed proposal ─► HUMAN APPROVAL (approver only)
   ─► upload ─► read back ─► exact ID comparison ─► receipt + attendance sheet ─► company emailed
```

| | |
|---|---|
| Agents | 21 named agents in 3 stages; each may call only its own tools; durable workflow graph that pauses for people; no agent can approve |
| AI | Gemini function calling for email reading, planning, Ask PlaceCrew and review summaries; always cross-checked by rules, with a labelled rule-based fallback |
| Decisions | Deterministic rules (CGPA ≥ inclusive, backlogs, branch, batch) → ELIGIBLE / INELIGIBLE / UNKNOWN, with the reason for every decision |
| Safety | Consent enforced before upload · approvals bound to a SHA-256 hash of the exact list · stale approvals cancelled · hash-chained audit log · email text treated as data, never instructions |
| Mail | Gmail API with Google sign-in (read company emails, send from the placement mailbox); SMTP as a backup |
| Upload target | The organiser's mock form was not provided, so uploads go to a clearly labelled **LOCAL MOCK** drive form (`/mock-portal`) behind a replaceable adapter |
| Tests | 125 backend tests (pytest), 13 browser end-to-end tests (Playwright), and the in-app Test Lab (TC1–TC6) |

## Official test cases

| Test | Scenario | Result in PlaceCrew |
|---|---|---|
| TC1 | CGPA ≥ 7 and no backlogs | 7.00 passes, 6.99 fails, any backlog fails, missing data → UNKNOWN |
| TC2 | Two students named Rahul Kumar | Matched by roll number everywhere |
| TC3 | Student without consent | Excluded from the upload (also after approval, if consent is withdrawn) |
| TC4 | Two drives in the same slot | Clash flagged and held for a person before assignment |

Extra: **TC5** company-email cases and **TC6** every student's journey. Details: [docs/TEST_SCENARIOS.md](docs/TEST_SCENARIOS.md).

## Accounts (password `Demo@2026`)

| Login | Role | Does |
|---|---|---|
| `driveuploadtesting@gmail.com` | Drive upload officer | Company emails → drives → consent forms |
| `officer@placementos.demo` | Placement officer | Runs the drive, settles clashes, adds drives |
| `approver@placementos.demo` | Approver | Sends forms for missing details, approves (the only role that can) |
| `admin@placementos.demo` | Admin | Setup, Gmail, Test Lab, audit log; cannot approve |

## Run locally

Requirements: Python 3.12+, Node 22+. Copy `.env.example` to `backend/.env` and fill in what you need.

```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.cli seed                 # 16 test students, 4 staff logins
uvicorn app.main:app --port 8000       # API + LOCAL MOCK form at /mock-portal

cd frontend                            # separate terminal
npm ci && npm run dev                  # http://localhost:3000 (proxies /api to :8000)
```

Gmail and Gemini setup: [docs/SETUP_GMAIL_GEMINI.md](docs/SETUP_GMAIL_GEMINI.md). The agents: [docs/AGENT_CREW.md](docs/AGENT_CREW.md).

## Tests

```bash
cd backend && python -m pytest            # unit, reliability, TC1–TC6, live HTTP API
cd frontend && npx playwright test        # browser end-to-end (needs both servers running)
```

## Tech stack

FastAPI · SQLAlchemy · PostgreSQL (Neon) · Next.js · Tailwind · Gemini API · Gmail API (Google OAuth) · pytest · Playwright · deployed on Vercel

## Repository layout

```
backend/   FastAPI: domain/ (rules) · agent/ (graph, crew, Ask PlaceCrew) · services/ · integrations/ · mock_portal/
           audit/ (hash-chained log) · api/ · migrations/ · tests/
frontend/  Next.js app: staff workspace, guided walkthrough (/tour), Test Lab · e2e/ (Playwright)
docs/      Evaluator guide, test scenarios, agent crew, Gmail/Gemini setup
```
