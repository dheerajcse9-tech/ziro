# PlacementOS — Agentic Placement Operations Platform

**Ziro Digital Agentic AI Hackathon 2026 · Problem statement: Placement Drive Coordinator**

A crew of 21 named agents runs the placement process in three stages, each owned by a person:

1. **Drive upload officer:** a company emails the placement Gmail. Agents read the email (Gemini + rules), check every
   detail and the deadline, create the drive, prepare its consent form and email it to every student.
2. **Placement officer:** filters the student registry and runs the drive. Agents apply the criteria, keep only students
   who said YES, detect clashes and propose the roster.
3. **Approver:** an agent re-checks the proposal. Students missing details or consent get a personal form with a
   closing time; their answers are applied, re-checked and added. After human approval the exact roster is uploaded,
   **independently verified**, and emailed to the company. Shortlisted and not-shortlisted students are told, and
   reminders go out before deadlines and the drive.

See [docs/AGENT_CREW.md](docs/AGENT_CREW.md) for every agent, and [docs/SETUP_GMAIL_GEMINI.md](docs/SETUP_GMAIL_GEMINI.md) to connect
Gmail and Gemini.

```
"Prepare the eligible roster for the upcoming drive, identify unresolved records,
 and show me the proposal for approval."
        │
        ▼
 plan → validate scope → retrieve employer form (evidence) → cross-check criteria
      → deterministic eligibility + consent → schedule-clash detection
      → hashed proposal ──► HUMAN APPROVAL (approver role, bound to hash)
      → idempotent upload → re-read destination → exact ID comparison
      → receipt + attendance sheet → VERIFIED
```

| | |
|---|---|
| Agents | 21 named agents in 3 stages; each may call only its own tools; durable workflow graph; Gemini (or Claude) reads goals and company emails, with a labelled rule-based fallback; autopilot runs the mailbox, deadline and reminder agents |
| Decisions | Deterministic rules engine (CGPA ≥ inclusive, backlog policy, branch, batch) → ELIGIBLE / INELIGIBLE / UNKNOWN with per-rule evidence |
| Safety | Consent enforced and re-checked before upload · approvals bound to a SHA-256 proposal hash · stale approvals auto-invalidated · destination allowlist · prompt-injection flagging · hash-chained append-only audit ledger |
| Integration | Replaceable `DriveFormAdapter`; demo target is a **LOCAL MOCK** employer drive portal (HTML form + read-back pages, fault injection) |
| Mail | Google sign-in to the placement Gmail: company emails are read (read-only) and all email is sent from it; SMTP or an in-app local mailbox as alternatives |
| Outreach | One consent form per drive to every student; personal forms for missing details/consent with closing times; reminders; shortlisted / not-shortlisted emails; selection results and placement history |
| Evidence | 99 backend tests, 4 official scenarios (TC1–TC4), 10 browser end-to-end tests — see [docs/TEST_EVIDENCE.md](docs/TEST_EVIDENCE.md) |

> **Honesty notes.** The organiser's official mock drive form URL/credentials were not available to this build, so
> every upload in this repository goes to the clearly labelled LOCAL MOCK portal served by the backend at
> `/mock-portal`. No Gemini or Anthropic key and no Google OAuth client were available in the build environment, so
> recorded runs used the rule-based fallbacks. The Gemini and Gmail paths follow Google's documented REST shapes
> and are tested against faithful stand-ins (a fake Gemini client and an httpx mock of the OAuth/Gmail endpoints),
> not against the live services. All people and employers in the data are synthetic.

## Quick start (local)

Requirements: Python 3.12+, Node 22+.

```bash
# Backend (SQLite by default; for PostgreSQL put DATABASE_URL=postgresql://... in backend/.env)
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.cli seed                 # synthetic demo data (34 students, 4 drives, 5 users)
uvicorn app.main:app --port 8000       # API + LOCAL MOCK portal at /mock-portal

# Frontend (separate terminal)
cd frontend
npm ci
npm run build && npm start             # http://localhost:3000  (proxies /api to :8000)
```

Sign in with a demo account (password `Demo@2026`, demo mode only):

| Account | Role | Can |
|---|---|---|
| `drives@placementos.demo` | Drive upload officer | company inbox, connect Gmail, create drives, send consent forms |
| `officer@placementos.demo` | Placement officer | run the agent, import/edit data, resolve exceptions |
| `approver@placementos.demo` | Head of Placements | approve / reject roster proposals, send personal forms to students |
| `admin@placementos.demo` | Admin | everything, settings, fault injection, demo reset |
| `viewer@placementos.demo` | Viewer | read only |

Full stack with PostgreSQL: `docker compose up --build` (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md); the compose file
was not executed in the build environment, which has no Docker daemon).

## Tests

```bash
cd backend && python -m pytest                    # unit + reliability + TC1–TC4 + live HTTP API
cd backend && python -m app.cli run-tests         # official scenarios, results stored for the Test Lab
cd frontend && CHROMIUM_PATH=... npx playwright test   # browser e2e against running servers
```

## Documentation

| Document | Contents |
|---|---|
| [AGENT_CREW](docs/AGENT_CREW.md) | The 21 agents, the three stages, who each agent works for |
| [SETUP_GMAIL_GEMINI](docs/SETUP_GMAIL_GEMINI.md) | Step-by-step: Gemini key, Google sign-in for Gmail, trusted senders |
| [PRODUCT_SPEC](docs/PRODUCT_SPEC.md) | Users, problems, MVP scope, features |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | System diagram, modules, request flows |
| [AGENT_WORKFLOW](docs/AGENT_WORKFLOW.md) | Graph, states, tools & permissions, interrupts, recovery |
| [DATA_MODEL](docs/DATA_MODEL.md) | Tables, keys, versioning and traceability |
| [SECURITY](docs/SECURITY.md) | Threats and controls |
| [API](docs/API.md) | Endpoint reference (OpenAPI at `/docs`, snapshot in `docs/evidence/openapi.json`) |
| [TEST_PLAN](docs/TEST_PLAN.md) · [ACCEPTANCE_CRITERIA](docs/ACCEPTANCE_CRITERIA.md) | What is tested and how |
| [TEST_EVIDENCE](docs/TEST_EVIDENCE.md) | Actual results, logs and screenshots |
| [DEPLOYMENT](docs/DEPLOYMENT.md) | Environment variables, Vercel/Docker instructions, current status |
| [PRESENTATION](docs/PRESENTATION.md) | Pitch outline, 2–3 minute demo script, limitations |
| [BUILD_STATUS](docs/BUILD_STATUS.md) | Progress log and open items |

## Repository layout

```
backend/   FastAPI app: domain/ (pure rules) · services/ · agent/ (graph, tools, planner) · integrations/ (adapters)
           mock_portal/ (LOCAL MOCK destination) · audit/ (hash-chained ledger) · api/ · migrations/ · tests/
frontend/  Next.js App Router + TypeScript + Tailwind: officer workspace & Agent Control Room · e2e/ (Playwright)
docs/      Specifications, evidence (logs, OpenAPI snapshot, screenshots)
```
