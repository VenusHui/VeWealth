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
docker-compose up -d --build                        # Default ENV=local
ENV=prod docker-compose up -d --build               # Production
./docker-start.sh                                    # Alias for ENV=prod
```

## Architecture

### Backend (`backend/`)

**Config loading**: `app/core/config.py` reads from `backend/settings/.local.env` (default) or `backend/settings/.prod.env` based on the `ENV` env var. The `ENV=local` vs `ENV=prod` distinction controls which settings file is loaded — this is how secrets are kept out of version control.

**Entry points**: `main.py` is the production entry (standard uvicorn). `dev_server.py` wraps it with `--reload` watching the `app/` directory. Always use `dev_server.py` for local development.

**Core layers**:
- `app/core/` — Infrastructure: `config.py` (settings), `database.py` (SQLAlchemy engine/session), `security.py` (JWT/password utils), `deps.py` (FastAPI dependencies like `get_current_user()`), `logger.py`
- `app/routers/` — FastAPI API route modules: `auth.py`, `stock.py`, `watchlist.py`, `scheduler.py`, `backtest.py`
- `app/services/` — Business logic: `stock_service.py` (AKShare/Tushare fetching, GMM fitting), `data_collector.py`, `alert_service.py`, `wechat_service.py`, `scheduler.py` (APScheduler setup), `backtest/` (full backtesting subsystem)
- `app/models/` — SQLAlchemy ORM models
- `app/schemas/` — Pydantic request/response schemas

**Lifespan lifecycle** (`main.py`): On startup — init DB (creates tables from models), recover stale backtest jobs, start APScheduler. On shutdown — stop scheduler.

**GMM core**: The platform's core algorithm fits Gaussian Mixture Models to stock price distributions. Key file: `backend/app/utils/data_processor.py` (GMM fitting, probability density computation). This is used by `stock_service.py` for analysis endpoint responses.

**Auth**: Master key required for registration (prevents unauthorized signups). JWT for session. `get_current_user()` dependency injects the authenticated user into route handlers.

### Frontend (`frontend/`)

**Framework**: Next.js 14 with App Router. Pages live in `frontend/app/{page_name}/page.tsx`. Shared UI components in `frontend/app/components/`.

**UI approach**: Hybrid — uses both Ant Design 6 components and Tailwind CSS utility classes. When adding UI, prefer shadcn/ui patterns (already refactored in). A-share color convention: red = up, green = down (non-Western convention).

**API integration**: All backend calls go through `NEXT_PUBLIC_API_URL` (default `http://localhost:8001`). Frontend routes that need auth read the JWT from localStorage via `frontend/app/lib/auth.ts` helpers.

### Data Flow

1. AKShare provides real-time/cached stock data (free, no auth)
2. Data is stored in PostgreSQL for long-term history (突破 AKShare 5日限制)
3. GMM is fitted on historical price data for distribution analysis
4. APScheduler runs alert checks during trading hours (9:15-15:00 weekdays)
5. WeChat notifications sent via wechatpy when price crosses thresholds

### Backtest Subsystem

`backend/app/services/backtest/` is a self-contained module:
- `engine.py` — Core execution engine
- `service.py` — Orchestration layer
- `job_manager.py` — Job lifecycle (create, run, poll results)
- `registry.py` — Strategy registry
- `strategies/` — Strategy implementations (MA cross, volume shrink/drop, etc.)
- `validators/` — Strategy parameter validation
- `policies/` — Policy definitions
- `costs.py` — Transaction cost modeling
- `metrics.py` — Performance metrics (Sharpe, max drawdown, etc.)

## Key Conventions

- **API prefix**: All API routes are prefixed with `/api` (from `settings.API_PREFIX`)
- **Database**: Auto-creates tables on startup via `init_db()` — no manual migration needed for development. Use Alembic (`backend/migration/db/`) for schema changes that need version tracking.
- **Stock codes**: A-share format (e.g., `000001.XSHE`, `600519.XSHG` for Shanghai/Shenzhen exchange)
- **Backend lint**: Black formatter with 4-space indent; Python modules/functions in `snake_case`
- **Frontend lint**: Next.js ESLint defaults; existing code uses 2-space indent and single quotes
- **A-share colors**: Red = price up, green = price down (reverse of Western convention)

## Workflow After Code Changes

After completing any code modification:

1. **Commit** the changes with a descriptive message (follow existing commit style):
   ```bash
   git add <changed-files>
   git commit -m "type(scope): description"
   ```

2. **Push** to the remote branch:
   ```bash
   git push origin <current-branch>
   ```

3. **Redeploy via Docker** to apply changes locally:
   ```bash
   docker-compose up -d --build
   ```
   This rebuilds both backend and frontend containers and restarts them. No manual restart needed — the `--build` flag ensures new code is picked up.

Do this after every batch of changes without waiting for the user to ask.
