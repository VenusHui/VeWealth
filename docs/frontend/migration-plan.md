# Frontend Migration Plan

## Objective
Move from mixed custom UI to a unified AntD v6 system with minimal regressions.

## Plan

### Phase 1 (Current)
- [x] Add AntD dependency and global reset style.
- [x] Backtest records/detail tables migrated to AntD.
- [x] Fix trade/round table misalignment.
- [x] Improve strategy-config readability structure.

### Phase 2
- [x] Migrate Watchlist page to AntD Form/Table/Card/Alert.

### Phase 3
- [x] Migrate Analysis page data controls and list/table sections.

### Phase 4
- [x] Migrate Login page form/feedback components.

### Phase 5
- [x] Remove obsolete custom pagination/table helpers.
- [ ] Normalize spacing/typography conventions.

## Validation Checklist
- `npm run lint`
- `npm run build`
- Manual smoke test of key pages

## Rollback
- Revert migration commit set by feature branch checkpoints.
- Keep API contract unchanged during UI migration to reduce rollback risk.
