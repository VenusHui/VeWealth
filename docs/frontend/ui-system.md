# UI System (Ant Design v6 + Tailwind)

## Stack
- Component system: Ant Design `6.3.3`
- Styling helper: Tailwind CSS (layout only)

## Boundary Rules
- Use AntD for: table, pagination, form controls, detail views, feedback, cards.
- Use Tailwind for: page-level spacing, grid/flex layout, section wrappers.
- Avoid mixed controls in one block (e.g., custom pager + AntD table).

## Data Table Rules
- Always set stable `rowKey`.
- Set explicit width for key columns.
- Right align numeric columns.
- Use `ellipsis` for long text.
- Use `scroll={{ x: ... }}` for dense tables.

## Strategy Config Readability Rules
- Layer 1: business summary (human-readable).
- Layer 2: technical details (DSL + SQL preview).
- Layer 3: raw JSON payload.
- Large lists default to preview + count.

## Theming
- Import `antd/dist/reset.css` once in root layout.
- Token customization can be introduced incrementally in a dedicated theme provider.
