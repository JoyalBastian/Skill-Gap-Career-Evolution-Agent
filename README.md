# SkillGap AI — Backend

Django app for career assessment: resume analysis, AI interview, career predictions, skill gaps, roadmap, and recommendations.

You can run it **locally on your machine** or **with Docker** (Django + Ollama in one stack). Choose one AI backend:

| Provider | Best for | Needs |
|----------|----------|--------|
| **Gemini** (`AI_PROVIDER=gemini`) | Cloud API, strong JSON | Google API key, internet |
| **Ollama** (`AI_PROVIDER=ollama`) | No quota limits, private | Ollama installed or Docker GPU stack |

---

## What you need before starting

### Common (both local and Docker)

| Requirement | Version / notes |
|-------------|-----------------|
| **Git** | To clone the repo (optional if you already have the folder) |
| **`.env` file** | Copy from `.env.example` — never commit real secrets |

### Local run only

| Requirement | Version / notes |
|-------------|-----------------|
| **Python** | 3.11+ ([python.org](https://www.python.org/downloads/)) |
| **pip** | Comes with Python |
| **Virtual environment** | Created with `python -m venv venv` |

**If using Gemini locally**

| Requirement | Notes |
|-------------|--------|
| **GEMINI_API_KEY** | From [Google AI Studio](https://aistudio.google.com/apikey) |
| **Internet** | API calls go to Google |

**If using Ollama locally**

| Requirement | Notes |
|-------------|--------|
| **Ollama** | Install from [ollama.com](https://ollama.com/download) |
| **Model pulled** | e.g. `ollama pull llama3.2` |
| **RAM / VRAM** | 8 GB+ RAM recommended; GPU speeds up responses |

### Docker run only

| Requirement | Notes |
|-------------|--------|
| **Docker Desktop** | [docker.com](https://www.docker.com/products/docker-desktop/) |
| **WSL2** (Windows) | Enable in Docker Desktop settings |
| **Disk space** | ~5–10 GB+ for Ollama model download |
| **NVIDIA GPU** (recommended) | [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) + WSL2 so Ollama uses the GPU |
| **No GPU** | Remove the `deploy.resources` block under `ollama` in `docker-compose.yml` (CPU only, slower) |

---

## Configure environment (`.env`)

From the `skillgap_ai/backend` folder:

```powershell
copy .env.example .env
```

Edit `.env` with a text editor.

### Minimum settings

```env
DEBUG=True
SECRET_KEY=any-random-string-for-local-dev
ALLOWED_HOSTS=localhost,127.0.0.1
```

### Option A — Gemini (cloud)

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your-api-key-here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-2.0-flash,gemini-flash-latest
RUN_AI_PIPELINE_ON_RESUME=False
```

`RUN_AI_PIPELINE_ON_RESUME=False` avoids extra API calls when uploading a resume (recommended on free tier).

### Option B — Ollama (local AI)

```env
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_TIMEOUT=120
```

Good models for this app: `llama3.2`, `mistral`, `qwen2.5:7b`.

**Inside Docker Compose**, use `OLLAMA_BASE_URL=http://ollama:11434` (set automatically in `docker-compose.yml` for the `web` service).

---

## Run locally (without Docker)

Use this when you want fast iteration with `runserver` and your own Python install.

### Step 1 — Open the backend folder

```powershell
cd "D:\Projects\Autonomous Skill Gap & Career Evolution Agent\skillgap_ai\backend"
```

(Use your actual project path.)

### Step 2 — Create and activate virtual environment

```powershell
python -m venv venv
venv\Scripts\activate.bat
```

On Linux/macOS:

```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3 — Install dependencies

```powershell
pip install -r requirements.txt
```

### Step 4 — Configure `.env`

```powershell
copy .env.example .env
```

Set `AI_PROVIDER` and either Gemini or Ollama variables (see above).

### Step 5 — Start Ollama (only if `AI_PROVIDER=ollama`)

In a **separate terminal**:

```powershell
ollama serve
ollama pull llama3.2
```

Verify: open http://localhost:11434/ or run `ollama list`.

### Step 6 — Database and server

```powershell
python manage.py migrate
python manage.py runserver
```

### Step 7 — Open the app

Browser: **http://127.0.0.1:8000/**

Register an account, upload a resume (optional), then start the AI interview from the dashboard.

### Stop local server

Press `Ctrl+C` in the terminal where `runserver` is running.

---

## Run with Docker

Use this when you want **one command** to start Django + Ollama together. All Docker files are in `skillgap_ai/backend/`:

| File | Purpose |
|------|---------|
| `Dockerfile` | Production image (Gunicorn + WhiteNoise) |
| `docker-compose.yml` | Web + Ollama + automatic model pull |
| `docker-compose.dev.yml` | Dev mode: `runserver` + code hot reload |
| `docker/entrypoint.sh` | Runs migrations and `collectstatic` on start |

### Step 1 — Install Docker Desktop

- Install and start **Docker Desktop**.
- On Windows: enable **WSL2** backend in Docker settings.
- For GPU: install **NVIDIA drivers** + **NVIDIA Container Toolkit** (see prerequisites above).

### Step 2 — Prepare `.env`

```powershell
cd skillgap_ai\backend
copy .env.example .env
```

**Gemini in Docker** (uses your `.env`; no Ollama containers):

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
SECRET_KEY=change-this-to-a-long-random-string
DEBUG=False
```

**Ollama in Docker**:

```env
AI_PROVIDER=ollama
COMPOSE_PROFILES=ollama
OLLAMA_MODEL=llama3.2
SECRET_KEY=change-this-to-a-long-random-string
DEBUG=False
```

`COMPOSE_PROFILES=ollama` is required — without it, `docker compose up` only starts the **web** container, not Ollama.

### Step 3 — Production stack (Gunicorn)

From `skillgap_ai/backend`:

**Gemini** (reads `AI_PROVIDER` from `.env`):

```powershell
docker compose up --build
```

**Ollama** (starts Ollama + model pull; first run can take a long time):

```powershell
# Either set COMPOSE_PROFILES=ollama in .env, then:
docker compose up --build

# Or pass the profile on the command line:
docker compose --profile ollama up --build
```

Optional GPU (NVIDIA toolkit installed):

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile ollama up --build
```

| URL | Service |
|-----|---------|
| http://localhost:8000/ | SkillGap web app |
| http://localhost:11434/ | Ollama API (only with `--profile ollama`) |

Stop everything:

```powershell
docker compose down
```

### Step 4 — Development stack (hot reload)

Same folder, with dev override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
# Ollama dev stack:
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile ollama up --build
```

- Uses Django `runserver` instead of Gunicorn.
- Mounts your source code into the container (changes reload automatically).
- Sets `DEBUG=True` via override.

### Step 5 — Change Ollama model

1. Set in `.env`: `OLLAMA_MODEL=mistral` (or another model name).
2. Pull the model:

```powershell
docker compose run --rm ollama-init
```

Or:

```powershell
docker compose exec ollama ollama pull mistral
```

### Useful Docker commands

```powershell
# View logs
docker compose logs -f web
docker compose logs -f ollama

# Rebuild after code changes (production)
docker compose up --build

# Remove containers but keep database + models
docker compose down

# Remove everything including volumes (fresh start)
docker compose down -v
```

---

## Which option should I use?

| Situation | Recommendation |
|-----------|----------------|
| Daily coding, quick edits | **Local** + `runserver` |
| No GPU / only Gemini key | **Local** or Docker with `AI_PROVIDER=gemini` |
| Avoid API quota, have GPU | **Docker** with `AI_PROVIDER=ollama` |
| Demo on a clean machine | **Docker** `docker compose up --build` |
| Interview finish is slow | Normal with Ollama on CPU; use GPU or Gemini |

---

## Troubleshooting

### `database is locked` (SQLite)

- Do not open `db.sqlite3` in DB Browser while the app runs.
- Avoid double-clicking Submit on the interview.
- Restart the server; Docker uses WAL mode and a longer lock timeout.
- Prefer a single `runserver` or one Docker `web` container.

### Gemini `429` / quota exceeded

- Wait for daily reset, or enable billing on Google AI Studio.
- Set `AI_PROVIDER=ollama` or switch `GEMINI_MODEL`.
- Keep `RUN_AI_PIPELINE_ON_RESUME=False`.

### Ollama connection failed

**Local:** ensure `ollama serve` is running and `OLLAMA_BASE_URL=http://localhost:11434`.

**Docker:** ensure `ollama` container is healthy (`docker compose ps`). Web must use `http://ollama:11434`, not `localhost`.

### Docker GPU not detected

- Confirm `nvidia-smi` works in WSL2.
- Install NVIDIA Container Toolkit.
- Or remove the `deploy.resources` GPU block in `docker-compose.yml` for CPU-only mode.

### First Docker start hangs on `ollama-init`

- Model download is large; wait until logs show pull complete.
- Check disk space and network.

### `exec /app/docker/entrypoint.sh: no such file or directory`

- Usually **Windows CRLF line endings** in `docker/entrypoint.sh`. Rebuild after pulling latest Dockerfile (it runs `sed` to fix this).
- Rebuild: `docker compose build --no-cache web` then `docker compose up`.
- Ensure you run from `skillgap_ai/backend` where the `docker/` folder exists.

### `Local Ollama is not reachable` / `Cannot connect to Ollama`

| How you run Django | What you need |
|--------------------|---------------|
| **venv + `runserver`** | Ollama app running on Windows; `.env`: `OLLAMA_BASE_URL=http://localhost:11434`; run `ollama pull llama3.2` |
| **Docker** | `AI_PROVIDER=ollama` **and** `COMPOSE_PROFILES=ollama` in `.env`, then `docker compose up --build` |

Quick checks:

```powershell
# Should return JSON (local Ollama)
curl http://localhost:11434/api/tags

# After Docker Ollama profile is up
curl http://localhost:11434/api/tags
```

`AI_PROVIDER=Ollama` in `.env` is fine (case is normalized to `ollama`).

### `TLS handshake timeout` when building `backend-web`

- Often caused by slow or blocked access to Docker Hub.
- This project's `Dockerfile` does not use `# syntax=docker/dockerfile:1` (that line triggers an extra Hub fetch).
- Retry: `docker compose build --no-cache web` or restart Docker Desktop.
- If `python:3.11-slim` also times out, check VPN/firewall or configure a Docker registry mirror in Docker Desktop → Settings → Docker Engine.

---

## Project layout

| Path | Purpose |
|------|---------|
| `manage.py` | Django entry point |
| `core/settings/` | Settings (loads `.env`) |
| `ai_engine/llm_client.py` | Routes AI calls to Gemini or Ollama |
| `apps/` | Django apps (users, questionnaire, careers, …) |
| `services/` | Business logic + AI pipelines |
| `templates/` | HTML templates |
| `static/` | CSS and uploads |
| `db.sqlite3` or `db/db.sqlite3` | SQLite database |
| `.env` | Your secrets (not in git) |

---

## Production checklist

- `DEBUG=False`
- Strong `SECRET_KEY` (50+ random characters)
- `ALLOWED_HOSTS` set to your real domain
- Use Docker Gunicorn stack or deploy the image behind HTTPS
- For Gemini in production: billing enabled and key stored securely
- For Ollama in production: enough GPU/RAM for expected load

Gunicorn worker timeout is **180 seconds** (long AI pipeline when finishing the interview).
