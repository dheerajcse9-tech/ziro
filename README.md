# PlacementOS — Agentic Placement Operations Platform

**Ziro Digital Agentic AI Hackathon 2026 · Problem statement: Placement Drive Coordinator**

A placement officer delegates a complete drive-preparation workflow to an AI agent, inspects its evidence and
proposed action, an approver signs off on the *exact* roster, and the system uploads it and **independently
verifies** the result at the destination.

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
| Agent | Durable workflow graph with typed, permissioned tools; LLM tool-use planner (Claude) **or** a clearly labelled deterministic fallback |
| Decisions | Deterministic rules engine (CGPA ≥ inclusive, backlog policy, branch, batch) → ELIGIBLE / INELIGIBLE / UNKNOWN with per-rule evidence |
| Safety | Consent enforced and re-checked before upload · approvals bound to a SHA-256 proposal hash · stale approvals auto-invalidated · destination allowlist · prompt-injection flagging · hash-chained append-only audit ledger |
| Integration | Replaceable `DriveFormAdapter`; demo target is a **LOCAL MOCK** employer drive portal (HTML form + read-back pages, fault injection) |
| Evidence | 56 backend tests, 4 official scenarios (TC1–TC4), 7 browser end-to-end tests — see [docs/TEST_EVIDENCE.md](docs/TEST_EVIDENCE.md) |

> **Honesty notes.** The organiser's official mock drive form URL/credentials were not available to this build, so
> every upload in this repository goes to the clearly labelled LOCAL MOCK portal served by the backend at
> `/mock-portal`. No ANTHROPIC_API_KEY was available in the build environment, so all recorded runs used the
> deterministic fallback planner; the LLM path is implemented and tested with a scripted client stand-in, not
> against the live API. All people and employers in the data are synthetic.

## Quick start (local)

Requirements: Python 3.12+, Node 22+.

```bash
# Backend (SQLite by default)
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.cli seed                 # synthetic demo data (34 students, 4 drives, 4 users)
uvicorn app.main:app --port 8000       # API + LOCAL MOCK portal at /mock-portal

# Frontend (separate terminal)
cd frontend
npm ci
npm run build && npm start             # http://localhost:3000  (proxies /api to :8000)
```

Sign in with a demo account (password `Demo@2026`, demo mode only):

| Account | Role | Can |
|---|---|---|
| `officer@placementos.demo` | Placement officer | run the agent, import/edit data, resolve exceptions |
| `approver@placementos.demo` | Head of Placements | approve / reject roster proposals |
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
