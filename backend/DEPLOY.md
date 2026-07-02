# Backend Container Deployment

This deploys only the FastAPI backend. The Vite frontend can stay on Vercel.

## Build Locally

```bash
docker build -t lexi-api:latest backend
```

Run it:

```bash
docker run --rm \
  --env-file backend/.env \
  -e DATABASE_URL=sqlite:////app/data/lexi.db \
  -p 8000:8000 \
  -v lexi-data:/app/data \
  lexi-api:latest
```

Health check:

```bash
curl http://localhost:8000/api/health
```

## Run With Compose

Create `backend/.env` from `backend/.env.example`, then set production values:

```env
GEMINI_API_KEY=...
JWT_SECRET=replace-with-a-long-random-secret
CORS_ORIGINS=https://your-vercel-app.vercel.app
```

Start the service:

```bash
docker compose -f compose.backend.yml up -d --build
```

View logs:

```bash
docker compose -f compose.backend.yml logs -f lexi-api
```

Update after a new image/build:

```bash
docker compose -f compose.backend.yml up -d --build
```

The SQLite database is stored in the named Docker volume `lexi-data`.

## Releasing As An Image

Tag a release:

```bash
docker build -t ghcr.io/YOUR_USER/lexi-api:v0.1.0 backend
docker tag ghcr.io/YOUR_USER/lexi-api:v0.1.0 ghcr.io/YOUR_USER/lexi-api:latest
docker push ghcr.io/YOUR_USER/lexi-api:v0.1.0
docker push ghcr.io/YOUR_USER/lexi-api:latest
```

On the production server, change `compose.backend.yml` to use the pushed image:

```yaml
services:
  lexi-api:
    image: ghcr.io/YOUR_USER/lexi-api:v0.1.0
```

Then deploy:

```bash
docker compose -f compose.backend.yml pull
docker compose -f compose.backend.yml up -d
```

Using a versioned image is usually safer than running `git pull` directly on production because it gives you repeatable builds and easier rollback.
