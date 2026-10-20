# Build Status

## Progress log
| Phase | Status | Notes |
|---|---|---|
| 0 Discovery | done | Empty repo. No organiser form URL/credentials, no LLM key, no deploy database. Python 3.13, Node 22, PostgreSQL 16, Chromium available. |
| 1 Specification | done | `docs/*.md` (kept aligned with the implementation) |
| 2 Foundation | done | FastAPI, SQLAlchemy 2, Alembic, JWT auth/RBAC, seed, health |
| 3 Domain engine | done | eligibility, normalisation, schedule, criteria parser, consent, imports |
| 4 Agent | done | durable graph, 12 typed tools, LLM + fallback planner, interrupts, retries |
| 5 Approval / upload / verification | done | hash-bound approvals, idempotent adapter, mock portal with faults, read-back, receipt, attendance |
| 6 Frontend | done | 19 routes on a shared design system |
| 7 Integration & tests | done | 56 pytest, PostgreSQL live tests, TC1–TC4, 7 Playwright e2e |
| 8 Deployment | **blocked** | needs a PostgreSQL database decision (see DEPLOYMENT.md); deploy configs ready |
| 9 Presentation | done | PRESENTATION.md (outline, demo script); video to be recorded by the team |

## Decisions
- Custom durable graph instead of LangGraph (approval pauses across HTTP requests/instances without extra infra).
- Deterministic eligibility; the LLM only interprets goals using read-only tools.
- LOCAL MOCK portal as a separate ASGI app reached over HTTP through the same adapter a real form would use.
- Frontend proxies `/api` for first-party httpOnly cookies and no client-side secrets.
- Pinned well-known library majors (Next 15, React 19.1, TanStack Table 8, Zod 3) over newer majors to reduce risk.

## Open items / next steps
1. Provision PostgreSQL and deploy (Option A in DEPLOYMENT.md), then verify the public workflow end to end.
2. Configure the organiser's official mock form as an allowlisted `ORGANISER_FORM` destination when its URL is shared;
   add a Playwright adapter if the form needs JavaScript; run TC1–TC4 against it.
3. Set `ANTHROPIC_API_KEY` and record an LLM-planned run.
4. Record the 2–3 minute demo video following PRESENTATION.md.
5. Hardening: login rate limiting, CSRF token for multipart upload, accessibility audit.
