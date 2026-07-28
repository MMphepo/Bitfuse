# Bitfuse — Step-by-Step Build Guide

## Goal

Get from an empty repo to a running local environment with PostgreSQL, Redis, Celery, MinIO, and Blnk all talking to each other, plus a fully working Phase 1 Authentication flow. Everything after this — KYC, wallets, buy/sell flows — builds on top of what you set up here.

## Stack

- Django + Django REST Framework
- PostgreSQL
- Redis
- Celery
- Blnk (ledger)
- MinIO (S3-compatible storage for local dev)
- Docker Compose

## Prerequisites

Verify the required tools are installed:

```bash
python3 --version
docker --version
docker compose version
git --version
```

## Step 1 — Repository structure

Create the monorepo skeleton:

```bash
mkdir bitfuse && cd bitfuse
mkdir backend frontend infra docs
git init
```

Expected structure:

```text
bitfuse/
├── backend/         # Django project lives here
├── frontend/        # React/Next.js app, built later
├── infra/           # docker-compose files, nginx config
└── docs/            # architecture docs, this guide, etc.
```

## Step 2 — Initialize the backend

Inside `backend/`, create a Python virtual environment and install Django dependencies.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install django djangorestframework psycopg[binary] redis celery django-cors-headers python-dotenv
```

Create a Django project and app:

```bash
django-admin startproject bitfuse_project .
python manage.py startapp accounts
```

## Step 3 — Configure Docker Compose in `infra/`

Create a `docker-compose.yml` file to run PostgreSQL, Redis, MinIO, Celery, and the Django backend.

Key services:

- `postgres` — stores user accounts, auth, and app data
- `redis` — broker for Celery and caching/session support
- `minio` — local object storage for uploads and media
- `backend` — Django app with REST API and worker support
- `celery` — asynchronous task runner

## Step 4 — Database and environment settings

Add a `.env` file for local environment variables.

Example `.env` values:

```env
DEBUG=True
SECRET_KEY=replace-with-a-secure-key
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_NAME=bitfuse_dev
DATABASE_USER=bitfuse_user
DATABASE_PASSWORD=bitfuse_pass
DATABASE_HOST=postgres
DATABASE_PORT=5432
REDIS_URL=redis://redis:6379/0
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_USE_SSL=False
```

Update `backend/bitfuse_project/settings.py`:

- Use `python-dotenv` or `django-environ` to load `.env`
- Set up PostgreSQL in `DATABASES`
- Configure `CACHES` and `CELERY_BROKER_URL` with Redis
- Configure `DEFAULT_FILE_STORAGE` for MinIO if required
- Add `rest_framework`, `corsheaders`, and the local `accounts` app

## Step 5 — Authentication app basics

Implement Phase 1 Authentication in `backend/accounts/`:

- `models.py` — custom `User` model if needed
- `serializers.py` — sign-up, login, profile serializers
- `views.py` — registration and login API views
- `urls.py` — endpoint routing for auth flows

Example endpoints:

- `POST /api/auth/register/`
- `POST /api/auth/login/`
- `GET /api/auth/me/`

## Step 6 — Run migrations and create superuser

From `backend/`:

```bash
source .venv/bin/activate
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

## Step 7 — Local dev with Docker Compose

From `infra/`:

```bash
docker compose up -d
```

Then verify services:

- Postgres on port `5432`
- Redis on port `6379`
- MinIO on port `9000`
- Django backend on the port mapped in `docker-compose.yml`

## Step 8 — Celery and background tasks

Configure Celery in `backend/bitfuse_project/celery.py` and start worker(s):

```bash
cd backend
source .venv/bin/activate
celery -A bitfuse_project worker --loglevel=info
```

For local dev, you can also run the Celery worker as a Docker service.

## Step 9 — Confirm Phase 1 Authentication works

Use a REST client or `curl` to test:

```bash
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"Password123"}'
```

Then login:

```bash
curl -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"Password123"}'
```

## Step 10 — Next steps after Phase 1

Once Authentication is working, build on top of it:

- KYC onboarding and document upload
- wallet creation and balance tracking
- buy/sell order flows
- Blnk ledger integration for transaction recording
- frontend integration with React/Next.js

---

## Recommended files to add next

- `infra/docker-compose.yml`
- `infra/.env`
- `backend/requirements.txt`
- `backend/README.md`
- `frontend/README.md`
- `docs/ARCHITECTURE.md`
