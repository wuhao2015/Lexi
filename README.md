# Lexi

A small full-stack dictionary web app with multilingual pair lookups (backed by **Gemini** with a **global translation cache**) and per-user review queues with local grading.

## Requirements

- Python 3.9+
- Node.js 18+ (for building the frontend; optional if you only run the API)
- A [Google AI Studio](https://aistudio.google.com/) API key (`GEMINI_API_KEY`) for live translation on cache miss

## Backend setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: set GEMINI_API_KEY and JWT_SECRET
```

Run the API (serves the built SPA from `backend/static` when present):

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- JSON API: `http://localhost:8000/api` (e.g. `GET /api/health`, interactive docs at `http://localhost:8000/api/docs`)
- Web UI: `http://localhost:8000/` after you build the frontend (see below)

SQLite database file (default): `backend/data/lexi.db`. If you upgraded from an older tree that used `word_killer.db`, copy it to `lexi.db` or set `DATABASE_URL` to the old file path.

## Frontend (dev)

```bash
cd frontend
npm install
npm run dev
```

Vite dev server proxies `/api` to `http://127.0.0.1:8000`. Start **uvicorn** on port 8000 in parallel.

## Frontend (production build)

Outputs to `backend/static` (served by the same uvicorn process):

```bash
cd frontend
npm install
npm run build
```

Then open `http://localhost:8000/` with only uvicorn running.

## Deploying the frontend to [Vercel](https://vercel.com/)

Vercel is a good fit for the **Vite UI**. The **FastAPI** app uses SQLite and a long‑running server, so run the API on a VPS, [Railway](https://railway.app/), [Render](https://render.com/), [Fly.io](https://fly.io/), or similar, then point the static site at that API.

1. Push this repository to GitHub (or GitLab / Bitbucket).
2. In Vercel: **Add New Project** → import the repo.
3. Set **Root Directory** to `frontend` (the repo has no root `package.json`).
4. **Environment variables** (Production): add `VITE_API_BASE_URL` with your public API origin, **no trailing slash** (for example `https://lexi-api.example.com`). Vite bakes this in at build time; redeploy after you change it.
5. Deploy. Open the `.vercel.app` URL and confirm login and lookups work.

On the API host, set **`CORS_ORIGINS`** to include your Vercel site URL (for example `https://your-app.vercel.app`), comma‑separated with any other origins you use. Set **`JWT_SECRET`**, **`GEMINI_API_KEY`**, and the rest of the backend variables there as well.

## Environment variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Required for translating new terms (not in global cache) |
| `GEMINI_MODEL` | Primary model id (default `gemini-2.5-flash-lite`) |
| `GEMINI_MODELS` | Comma-separated fallback order; backend tries each model until one responds (default: `gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.5-pro,gemini-3-flash-preview`) |
| `JWT_SECRET` | Secret for signing JWT access tokens |
| `CORS_ORIGINS` | Comma-separated origins (default includes Vite `http://localhost:5173`) |
| `DATABASE_URL` | Optional SQLAlchemy URL; default is SQLite under `backend/data/` |
| `CACHE_MAINTENANCE_ENABLED` | Enable in-process scheduled cache maintenance worker (default `true`) |
| `CACHE_MAINTENANCE_INTERVAL_SECONDS` | Maintenance interval in seconds (default `86400`, daily) |
| `CACHE_MERGE_IDENTICAL_ENABLED` | Enable merge for same normalized `(term, source_lang, target_lang)` groups (default `true`) |
| `CACHE_CONFLICT_POLICY` | Winner-selection policy when merging cache conflicts (default `keep_highest_quality_score`) |

### Frontend (Vite) — build-time

| Variable | Description |
|----------|-------------|
| `VITE_API_BASE_URL` | Optional. Public origin of the FastAPI server (no trailing slash). Omit locally so `/api` uses the Vite dev proxy. Required for a Vercel build that talks to a separate API. |

## API overview

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/auth/register` | No | Create user, returns JWT |
| POST | `/api/auth/login` | No | Login, returns JWT |
| GET | `/api/me` | Yes | Current user |
| GET | `/api/languages` | No | List supported language options (top 10) |
| POST | `/api/lookup` | Yes | Resolve selected language pair via `translation_cache` or Gemini; auto-detects direction if input matches the other side |
| GET | `/api/review/next` | Yes | Next item with `priority > 0` (supports `source_lang` + `target_lang`; pair filter is bidirectional) |
| POST | `/api/review/answer` | Yes | Grade explanation locally; returns `grading_mode` (`offline` or `language_mismatch`) |

Send `Authorization: Bearer <token>` for authenticated routes.

## Behaviour notes

- **Global cache**: The same normalized `(term, source_lang, target_lang)` is shared by all users; Gemini runs only on cache miss.
- **Pair direction**: For a selected pair, lookup auto-detects whether input matches language A or B and translates to the other one.
- **Review pairs**: Review selection is bidirectional; for pair `(A, B)`, items from both `(A -> B)` and `(B -> A)` can appear.
- **Review priority**: Higher priority is shown first. Correct answers reduce priority; wrong answers increase it. Items at priority `0` are treated as done for the queue.
- **Scheduled cache maintenance**: A background worker inside the Lexi service runs periodically to remove bad self-echo cache rows and merge duplicate cache groups.
- If `GEMINI_API_KEY` is missing, new terms cannot be translated until you add a row to `translation_cache` or set a key.
