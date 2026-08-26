# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VeWealth is an A股 (China A-share) stock analysis and monitoring platform. It provides real-time stock data, price distribution analysis using Gaussian Mixture Model (GMM), watchlist monitoring, price alerts via WeChat, and backtesting capabilities.

## Commands

### Local Development
```bash
./start.sh              # Starts backend + frontend together (dev_server.py + npm run dev)
```

### Individual Services
```bash
# Backend (port 8001)
cd backend && python dev_server.py          # Hot-reload dev server (watches app/ directory)
cd backend && python main.py                # Standard uvicorn run (no reload)
ENV=local python main.py                    # Use local env config (default)
ENV=prod python main.py                     # Use production env config

# Frontend (port 3000)
cd frontend && npm run dev                 # Dev server
cd frontend && npm run build && npm start   # Production build
```

### Code Quality (CI gates)
```bash
cd backend && black --check .               # Python format check (requires vewealth conda env)
cd frontend && npm run lint                 # ESLint check
./.github/scripts/local-lint.sh              # Both checks together
```

### Tests
```bash
cd backend && python -m unittest discover -s tests -p 'test_*.py'
```

### Docker
```bash
./start.sh                                           # Starts backend (dev_server.py) + frontend (npm run dev)
docker-compose up -d --build                        # Default ENV=local
ENV=prod docker-compose up -d --build               # Production
./docker-start.sh                                    # Alias for ENV=prod docker-compose up -d --build
```

## Architecture

### Backend (`backend/`)

**Config loading**: `app/core/config.py` reads from `backend/settings/.local.env` (default) or `backend/settings/.prod.env` based on the `ENV` env var. The `ENV=local` vs `ENV=prod` distinction controls which settings file is loaded — this is how secrets are kept out of version control.

**Entry points**: `main.py` is the production entry (standard uvicorn). `dev_server.py` wraps it with `--reload` watching the `app/` directory. Always use `dev_server.py` for local development.

**Core layers**:
- `app/core/` — Infrastructure: `config.py` (settings), `database.py` (SQLAlchemy engine/session), `security.py` (JWT/password utils), `deps.py` (FastAPI dependencies like `get_current_user()`), `logger.py`
- `app/routers/` — FastAPI API route modules: `auth.py`, `stock.py`, `watchlist.py`, `scheduler.py`, `backtest.py`, `alert.py`
- `app/services/` — Business logic: `stock_service.py` (AKShare/Tushare fetching, GMM fitting), `data_collector.py`, `alert_service.py`, `wechat_service.py`, `scheduler.py` (APScheduler setup), `backtest/` (full backtesting subsystem)
- `app/providers/` — Market data source adapters: `base.py` (abstract interface), `akshare_provider.py`, `astock_provider.py` (TDX), `astock_data.py` (data types)
- `app/models/` — SQLAlchemy ORM models: `user.py`, `watchlist.py`, `stock_data.py`, `backtest.py`, `backtest_job.py`, `alert_history.py`, `security_universe.py`
- `app/schemas/` — Pydantic request/response schemas: `auth.py`, `stock.py`, `watchlist.py`, `backtest.py`, `alert.py`

**Lifespan lifecycle** (`main.py`): On startup — init DB (`init_db()` creates schema on unmanaged DBs or applies pending Alembic migrations on managed ones), recover stale backtest jobs, start APScheduler. On shutdown — stop scheduler.

**GMM core**: The platform's core algorithm fits Gaussian Mixture Models to stock price distributions. Key file: `backend/app/utils/data_processor.py` (GMM fitting, probability density computation). This is used by `stock_service.py` for analysis endpoint responses.

**Auth**: Master key required for registration (prevents unauthorized signups). JWT for session. `get_current_user()` dependency injects the authenticated user into route handlers.

### Frontend (`frontend/`)

**Framework**: Next.js 14 with App Router. Pages: `/` (dashboard), `/login`, `/depth` (Volume Profile + GMM), `/watchlist`, `/backtest`, `/alerts`. Shared UI components in `frontend/app/components/`. Backtest module has its own components in `frontend/app/backtest/components/`.

**UI approach**: Hybrid — uses both Ant Design 6 components and Tailwind CSS utility classes. A-share color convention: red = up, green = down (non-Western convention). Color helpers in `frontend/app/lib/marketColors.ts`.

**API integration**: The API base URL is resolved at runtime by `frontend/app/lib/api.ts` (`getApiBaseUrl()`), not at build time. It derives the backend address from `window.location.hostname` with port 8001, so the same build works on localhost and any public IP. Frontend routes that need auth read the JWT from localStorage via `frontend/app/lib/auth.ts` helpers.

### Data Flow

1. AKShare provides real-time/cached stock data (free, no auth)
2. Data is stored in PostgreSQL for long-term history (突破 AKShare 5日限制)
3. GMM is fitted on historical price data for distribution analysis
4. APScheduler runs alert checks during trading hours (9:00-15:00 weekdays)
5. WeChat notifications sent via wechatpy when price crosses thresholds

### Backtest Subsystem

`backend/app/services/backtest/` is a self-contained module:
- `engine.py` — Core execution engine
- `service.py` — Orchestration layer
- `job_manager.py` — Job lifecycle (create, run, poll results)
- `registry.py` — Strategy registry
- `strategy_management_service.py` — Strategy management (availability, validation)
- `strategies/` — Strategy implementations: `base.py`, `contracts.py` (V2 contracts), `ma_cross_v1.py`, `volume_shrink_drop_v1.py`, `gmm_volume_v1.py`
- `validators/` — Strategy parameter validation: `strategy_validator.py`
- `policies/` — Policy definitions: `base.py`, `registry.py`, `profiles.py`
- `costs.py` — Transaction cost modeling
- `metrics.py` — Performance metrics (Sharpe, max drawdown, etc.)

## Key Conventions

- **API prefix**: All API routes are prefixed with `/api` (from `settings.API_PREFIX`)
- **Database**: On startup `init_db()` runs `alembic upgrade head` on Alembic-managed DBs (tables tracking `alembic_version`) so pending migrations apply on the deploy path; on unmanaged DBs it falls back to `create_all` + `stamp head`. Schema changes are tracked via Alembic (`backend/alembic/`, config `backend/alembic.ini`); the initial baseline `0001_initial_baseline` snapshots the v1.2.0 schema. See `backend/alembic/README.md` for usage/rollback.
- **Stock codes**: A-share format (e.g., `000001.XSHE`, `600519.XSHG` for Shanghai/Shenzhen exchange)
- **Backend lint**: Black formatter with 4-space indent; Python modules/functions in `snake_case`
- **Frontend lint**: Next.js ESLint defaults; existing code uses 2-space indent and single quotes
- **A-share colors**: Red = price up, green = price down (reverse of Western convention)

## Deployment Checklist

This machine IS the production server (IP `124.221.239.27`). Docker containers serve traffic directly on ports 3000 (frontend) and 8001 (backend). The `.env` file at the repo root sets `ENV=prod` so the backend loads `backend/settings/.prod.env`.

### Three pitfalls that break production

#### 1. Frontend API URL must be runtime, not build-time

**Problem**: `NEXT_PUBLIC_API_URL` is a Next.js build-time variable. If set to `http://localhost:8001` (the docker-compose default), it gets inlined into the JS bundle. Remote browsers then try their own `localhost`, which has no backend.

**Rule**: Every frontend page that calls the API must use the shared helper:

```typescript
import { getApiBaseUrl } from '<relative-path>/lib/api'
const API_BASE_URL = getApiBaseUrl()
```

`getApiBaseUrl()` (in `frontend/app/lib/api.ts`) reads `window.location.hostname` at runtime and returns `{protocol}//{hostname}:8001`. Never write `process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'` in page files. When adding a new page, always delegate to the helper.

#### 2. CORS must include the public origin

The backend's `CORS_ORIGINS` setting must list every origin that browsers use to reach the frontend. If a user accesses the site via a new IP or domain, add it to `backend/settings/.prod.env`:

```
CORS_ORIGINS=["http://localhost:3000","http://124.221.239.27:3000",...]
```

After editing `.prod.env`, restart the backend (the file is volume-mounted, no rebuild needed):
```bash
docker-compose restart backend
```

When adding a new frontend origin (different port, new domain, etc.), always check whether CORS_ORIGINS needs updating.

#### 3. New .env keys need Settings class fields

The `Settings` class in `backend/app/core/config.py` uses Pydantic v2 with `extra = "ignore"` — unknown env keys are silently dropped. If you add a key to `.prod.env` (or `.local.env`), you MUST also add a corresponding field to the `Settings` class, or the value will be ignored. Conversely, if you add a field to `Settings`, ensure the env files have a matching entry if the default isn't right for production.

### Standard redeploy

```bash
git add <changed-files>
git commit -m "type(scope): description"
git push origin <current-branch>
docker-compose up -d --build
```

This machine is the production server, so `docker-compose up -d --build` deploys live. Do this after every batch of changes without waiting for the user to ask.

### Verify deployment

```bash
# Backend health and CORS
curl -s http://localhost:8001/health
curl -s -I -X OPTIONS http://localhost:8001/api/backtest/strategies \
  -H "Origin: http://124.221.239.27:3000" \
  -H "Access-Control-Request-Method: GET" | grep access-control-allow-origin

# Frontend
curl -s -o /dev/null -w "HTTP %{http_code}" http://localhost:3000

# Check that no hardcoded localhost leaked into the JS bundle
curl -s http://localhost:3000/backtest | grep -oP 'static/chunks/app/backtest/page-[a-f0-9]+\.js' | head -1 | xargs -I{} curl -s "http://localhost:3000/_next/{}" | grep -c 'localhost:8001'
# Expected: 0
```
