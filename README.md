# NL CRUD Workbench

[中文文档](./README_CN.md)

Natural-language workbench for relational databases. Connect your DB, pick tables, let the model understand schema relationships, then query or mutate data in plain language — without writing SQL by hand.

> Internal tool / MVP: policy-guarded reads and writes with preview-before-confirm for mutations.

## Features

- **Multi-dialect connections**: SQLite, MySQL, PostgreSQL, SQL Server
- **Guided UI flow**: Connect → Select tables → Interpret relationships → Workbench
- **Table search**: filter by name / Chinese alias / columns; group by schema
- **Business glossary**: table/column aliases injected into NL prompts (editable on interpret step)
- **Access policy**: table/column whitelist, operation flags, row limits, required WHERE for update/delete
- **Controlled query**: NL → SELECT only, validated by sqlglot, forced `LIMIT`
- **Sample-aware SQL**: a few redacted sample rows (not persisted) help the model learn real labels and codes
- **Cheap answer check**: heuristics first; rewrite once when a named-entity query looks like a table dump; edit SQL and re-run through the guard
- **Multi-turn chat**: follow-ups reuse recent questions/SQL
- **Restricted write**: structured insert/update/delete → preview → confirm; large impact needs a second ack; confirm returns read-back rows
- **PK-safe mutate**: update/delete refused when the target table has no primary key
- **Editable mutate SQL**: revise preview SQL through the same structured guard (`.../mutate/revise`) before confirm
- **Pooled engines**: reuse SQLAlchemy engines across requests; invalidate on connection update/delete
- **CSV export**: download query results; LIMIT truncation is called out in the UI
- **Workspace restore**: wizard + chat survive refresh via `sessionStorage`
- **Schema analysis cache**: reuse relationship interpretation; optional re-analyze with LLM
- **LLM settings UI**: API Key / Base URL / Model (presets + custom), encrypted key storage
- **Audit UI**: browse action history, SQL, and row impact from the top bar

## Project layout

```text
NatureLanguageCRUD/
├── app/                 # FastAPI backend
│   ├── api/             # REST routers
│   ├── db/              # engines + schema introspection
│   ├── query/           # guarded SELECT + NL query
│   ├── mutate/          # restricted write pipeline
│   └── ...
├── web/                 # React + Vite + TypeScript UI
├── main.py              # backend entry (uvicorn)
├── requirements.txt
└── .env.example
```

## Requirements

- Python 3.11+
- Node.js 18+ (for the UI)
- Optional: ODBC Driver for SQL Server on Windows

## Quick start

### 1. Backend

```bash
python -m venv .venv

# Windows
.\.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # or: cp .env.example .env
python main.py
```

API docs: http://127.0.0.1:8000/docs  
Health: http://127.0.0.1:8000/health

On first launch the app seeds a local SQLite demo (`demo.db`) and connection `local-demo-sqlite`.

### 2. Frontend

```bash
cd web
npm install
npm run dev
```

Open http://127.0.0.1:5173  
Vite proxies `/api` to the backend on port `8000`.

### 3. Configure LLM and secrets

In the UI click **API Settings** (top-right), or set in `.env`:

```env
WORKBENCH_ENV=development
WORKBENCH_SECRET_KEY=change-me-to-a-long-random-string
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

- UI-configured LLM keys override `.env` and are encrypted in `workbench.db`.
- With `WORKBENCH_ENV=production`, the app **refuses to start** if `WORKBENCH_SECRET_KEY` is missing, too short (&lt;16), or still a placeholder.

## Typical usage

1. Create or reuse a database connection and test it
2. Search/multi-select tables (aliases help Chinese search)
3. Review schema/relationship analysis; edit glossary aliases; re-analyze if needed
4. In the workbench:
   - **Query**: natural language → SQL → result table (edit SQL / export CSV / follow-ups)
   - **Write**: natural language → mutation preview → confirm (large updates need ack; then read-back)
5. Open **审计** in the top bar to inspect recent SQL and impact

## Safety model

| Layer | Behavior |
|-------|----------|
| Read | SELECT only; whitelist tables/columns; enforce max rows |
| Samples | Tiny `LIMIT` reads of introspected identifiers only; statement timeout; secrets redacted; not stored in analysis cache |
| Write | No free-form DML; structured mutate only; PK required for update/delete; WHERE required by policy; preview (+ optional SQL revise) + confirm; large-impact ack |
| Secrets | DB passwords and LLM API keys encrypted at rest; production blocks insecure `WORKBENCH_SECRET_KEY` |
| Audit | Actions logged; browsable in the UI |

## Main APIs

| Area | Examples |
|------|----------|
| Connections | `GET/POST /api/connections`, `POST .../test`, `GET .../schema` |
| Policy | `GET/PUT /api/connections/{id}/policy` |
| Workspace | `PUT .../workspace/tables`, `POST .../workspace/interpret`, `GET .../workspace/analysis`, `GET/PUT .../workspace/glossary` |
| Query | `POST .../query/nl`, `.../query/sql`, `.../query/structured` |
| Mutate | `POST .../mutate/preview`, `.../mutate/nl`, `.../mutate/revise`, `.../mutate/confirm` |
| Settings | `GET/PUT /api/settings` |
| Audit | `GET /api/audit`, `GET /api/audit/{id}` |

## Notes

- SQL Server needs a working ODBC driver (`ODBC Driver 18 for SQL Server` by default).
- Without an LLM key, schema analysis falls back to metadata/FK summary; NL query/write require a key.
- This is an internal MVP — not a substitute for production RBAC, network isolation, or DBA review of high-risk writes.

## License

Private / internal use unless otherwise specified.
