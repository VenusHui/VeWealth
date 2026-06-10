# Stock Universe Filter Design (2026-03-18)

> **Status: ✅ Implemented** — `security_universe` table, board/ST filtering, and frontend controls are live.

## Goal
Add a unified stock-universe filter for strategy-based stock selection:
- Default: keep main board only
- Default: exclude ST
- Optional: include GEM / STAR / BSE boards

## Decisions
1. Use **DB dimension table** in main business DB: `security_universe`.
2. Reuse existing weekly symbol-refresh job, but write/update DB table instead of relying on txt as runtime source.
3. Frontend provides controls; backend enforces defaults and validation.

## Data Model
Table `security_universe`:
- id (PK)
- stock_code (unique, 6-digit)
- stock_name
- market (SH/SZ/BJ)
- board (main/gem/star/bse)
- is_st (bool)
- is_active (bool)
- list_date (nullable)
- delist_date (nullable)
- created_at, updated_at

Indexes:
- unique(stock_code)
- idx(board, is_st)
- idx(is_active)

## Request Contract
In `strategy_params` for strategy-select mode:
- `boards: string[]` (allowed: main/gem/star/bse), default `['main']`
- `exclude_st: boolean`, default `true`

Compatibility:
- If params absent, backend applies defaults.
- Invalid boards -> 400.
- Empty boards -> 400.

## Runtime Flow
1. Resolve effective filter params.
2. Load symbols from `security_universe` with board/ST filters.
3. Use filtered symbols as universe for strategy scan.
4. Persist effective filter in run diagnostics.

## Rollout
1. Add table/model + DB init import.
2. Add refresh script DB upsert.
3. Add service query API (`get_all_stock_symbols(boards, exclude_st)`).
4. Add backend strategy-select filtering + validation.
5. Add frontend controls and payload fields.
6. Build + smoke test.
