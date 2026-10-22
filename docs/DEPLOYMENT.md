# Deployment

## Current status (honest)
**Not yet deployed to a public URL.** The application is deploy-ready and has been verified locally against
PostgreSQL 16, but a public deployment needs a persistent PostgreSQL database. The connected Vercel team
(`dheeraj13`) has no database integration, and provisioning one (e.g. Neon from the Vercel Marketplace) creates a new
— possibly billed — resource that the account owner should choose. A serverless deployment on SQLite was rejected
because each instance would have its own ephemeral database, breaking approvals that span requests.

## Environment variables
See [`.env.example`](../.env.example). Required in production:

| Variable | Service | Notes |
|---|---|---|
| `APP_ENV=production` | API | enforces `JWT_SECRET`, secure cookies |
| `DATABASE_URL` | API | `postgresql://…` (also accepts `postgres://`) |
| `JWT_SECRET` | API | ≥ 32 random bytes; also derives the mock-portal admin token |
| `AUTO_MIGRATE=true` | API | runs `alembic upgrade head` (+ demo seed if empty and `DEMO_MODE=true`) at startup |
| `DEMO_MODE=true` | API | demo accounts, fault injection, demo reset (synthetic data only) |
| `MOCK_PORTAL_BASE_URL` | API | public URL of the API + `/mock-portal` |
| `ALLOWED_DESTINATION_HOSTS` | API | must include the API host (and the organiser host, when available) |
| `CORS_ORIGINS` | API | the web origin (only needed if the browser calls the API directly) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | API | optional; Gemini for the Email Reader and Goal Planner agents (`LLM_PROVIDER=auto` prefers it) |
| `ANTHROPIC_API_KEY` | API | optional alternative (Claude) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | API | Google sign-in for the placement Gmail; the OAuth client's redirect URI must be `PUBLIC_APP_URL/api/integrations/google/callback` |
| `INBOX_SUBJECT_FILTER`, `INBOX_LOOKBACK_DAYS` | API | which company emails the Mailbox Agent reads |
| `AUTOPILOT`, `AUTOPILOT_INTERVAL_SECONDS` | API | background agents (mailbox, deadlines, reminders); on serverless hosts call `POST /api/autopilot/run` from a scheduler instead |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | API | optional; without them emails stay in the in-app local mailbox |
| `PUBLIC_APP_URL` | API | public web URL used in links emailed to students |
| `BACKEND_URL` | Web | server-side only; target of the `/api` and `/mock-portal` rewrites |

## Option A — Vercel (two projects from this repository)
1. **Database:** create a PostgreSQL database (e.g. Vercel Marketplace → Neon) and copy its connection string.
2. **API project:** new project, root directory `backend` (FastAPI is auto-detected from `app/main.py`;
   `backend/vercel.json` sets `maxDuration`). Set the variables above, with
   `MOCK_PORTAL_BASE_URL=https://<api-domain>/mock-portal`, `ALLOWED_DESTINATION_HOSTS=<api-domain>`, `AUTO_MIGRATE=true`.
3. **Web project:** root directory `frontend`, framework Next.js, `BACKEND_URL=https://<api-domain>`.
4. Verify: `GET https://<web-domain>/api/health` → `database: ok, database_dialect: postgresql`; sign in; run the
   agent on the upcoming drive; approve as the approver; confirm VERIFIED; run the Test Lab.

## Option B — Docker / any container host
`docker compose up --build` starts PostgreSQL, the API (auto-migrates and seeds) and the web app on
http://localhost:3000. The compose file and Dockerfiles were written but **not executed** in the build environment
(no Docker daemon). For a single host (Render, Fly.io, Railway, a VM) use the same two images and environment variables.

## Option C — local, no containers
See the README quick start (SQLite). For a local PostgreSQL, create the database and put
`DATABASE_URL=postgresql://user:password@localhost:5432/placementos` in `backend/.env` (read automatically at
startup; real environment variables take precedence). Then `alembic upgrade head` and `python -m app.cli seed`.

## Evaluator walkthrough on the deployed link
`/tour` needs nothing extra: every step builds its own in-memory sandbox (and replays the earlier steps), so it works on
serverless hosts and never touches the main database or sends email. The last step (all test scenarios) takes a few
seconds; `backend/vercel.json` already allows longer functions. Give evaluators the web URL and point them to the
"Guided walkthrough" link on the login page.

## Operations
- Health: `GET /api/health` (database connectivity, dialect, planner mode).
- Logs: structured stdout logging; unhandled errors are logged with stack traces and return a generic 500.
- Demo reset: Settings → Demo reset (admin, demo mode only) or `python -m app.cli reset`.
- Retention: Settings → Apply retention purge (admin) clears attendance content older than the configured days.
