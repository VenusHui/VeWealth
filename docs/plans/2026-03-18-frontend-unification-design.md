# Frontend Unification Design (2026-03-18)

> **Status: ✅ Implemented** — Ant Design v6.3.3 is the primary component system across all pages.

## Scope
- Global UI component standardization with Ant Design v6.3.3.
- Immediate fix for backtest detail table header/content misalignment.
- Improve strategy-config readability for strategy-select runs.

## Key Decisions
1. Adopt Ant Design v6.3.3 as the primary component system.
2. Keep Tailwind for layout-level composition only.
3. Replace custom backtest table/pagination implementation with AntD Table/Pagination.
4. Strategy config presentation uses layered information:
   - Business summary
   - Technical detail (DSL + SQL preview)
   - Raw payload (for debugging)

## Why AntD v6
- Latest stable major in npm.
- Better consistency and reduced custom component maintenance.
- Strong Table/Form/Descriptions primitives required by data-heavy pages.

## Phased Rollout
1. Backtest module migration (create/records/detail) + table alignment fix.
2. Watchlist migration.
3. Analysis migration.
4. Login migration.
5. Cleanup legacy UI components and style duplication.

## Acceptance
- No header/content column shift in backtest trade/round tables.
- Strategy config remains readable even with large symbol sets.
- Build and lint pipelines pass.
