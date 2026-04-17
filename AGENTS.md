# Repository Guidelines

## Project Structure & Module Organization
- `backend/`: FastAPI service. Core config in `app/core/`, API routes in `app/routers/`, domain logic in `app/services/`, SQLAlchemy models in `app/models/`, schemas in `app/schemas/`, and tests in `backend/tests/`.
- `frontend/`: Next.js 14 (App Router) UI. Route pages live in `frontend/app/**/page.tsx`, shared UI in `frontend/app/components/`, and client utilities in `frontend/app/lib/`.
- `docs/`: architecture/design notes and implementation plans (`docs/plans/`).
- Root scripts: `start.sh` (local backend + frontend), `docker-start.sh` (Docker prod-style startup), `docker-compose.yml`.

## Build, Test, and Development Commands
- `./start.sh`: starts backend (`python dev_server.py`) and frontend (`npm run dev`) together.
- `cd backend && pip install -r requirements.txt && python dev_server.py`: run backend with reload on `:8001`.
- `cd frontend && npm ci && npm run dev`: run frontend on `:3000`.
- `cd frontend && npm run build && npm run start`: production build/start for Next.js.
- `cd backend && black --check .`: Python format check used by CI.
- `cd frontend && npm run lint`: ESLint/Next lint check used by CI.

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indentation, type hints where practical; format with Black.
- TypeScript/React: follow Next.js ESLint defaults; existing code uses 2-space indentation and single quotes.
- Naming: Python modules/functions `snake_case`; React components/types `PascalCase`; route folders should be descriptive and lowercase.

## Testing Guidelines
- Backend tests live in `backend/tests/` with `test_*.py` naming (example: `test_backtest_strategy_v2_migration.py`).
- Run backend tests with `cd backend && python -m unittest discover -s tests -p 'test_*.py'`.
- Add focused tests for new strategy logic, validators, and API behavior changes. No fixed coverage threshold is currently enforced; maintain or improve existing coverage in touched areas.

## Commit & Pull Request Guidelines
- Follow Conventional Commits seen in history: `feat(scope): ...`, `fix(scope): ...`, `style(scope): ...`, `refactor(scope): ...`, `docs(scope): ...`.
- Use clear scopes like `frontend`, `backtest`, or `watchlist`.
- PRs should include: concise change summary, linked issue (if any), testing evidence (commands/results), and screenshots/GIFs for UI changes.
- Ensure CI passes (`black --check`, `npm run lint`, Docker build jobs) before requesting review.

## Security & Configuration Tips
- Never commit secrets. Keep env values in `backend/settings/.local.env` or `.prod.env` and `frontend/.env.local`.
- For production, rotate `SECRET_KEY`/`MASTER_KEY`, restrict `CORS_ORIGINS`, and verify `ENV` before deployment.
