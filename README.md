# NL CRUD Workbench

[中文文档](./README_CN.md)

Natural-language workbench for relational databases. Connect your DB, pick tables, let the model understand schema relationships, then query or mutate data in plain language — without writing SQL by hand.

> Internal tool / MVP: policy-guarded reads and writes with preview-before-confirm for mutations.

## Features

- **Multi-dialect connections**: SQLite, MySQL, PostgreSQL, SQL Server
- **Guided UI flow**: Connect → Select tables → Interpret relationships → Workbench
- **Access policy**: table/column whitelist, operation flags, row limits, required WHERE for update/delete
- **Controlled query**: NL → SELECT only, validated by sqlglot, forced `LIMIT`
- **Restricted write**: structured insert/update/delete → preview → confirm
- **Schema analysis cache**: reuse relationship interpretation; optional re-analyze with LLM
- **LLM settings UI**: API Key / Base URL / Model (presets + custom), encrypted key storage
- **Audit log**: connection, policy, query, and mutate actions

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

### 3. Configure LLM

In the UI click **API Settings** (top-right), or set in `.env`:

```env
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
WORKBENCH_SECRET_KEY=change-me-to-a-long-random-string
```

UI-configured keys override `.env` and are encrypted in `workbench.db`.

## Typical usage

1. Create or reuse a database connection and test it
2. Multi-select tables you want to operate on
3. Review schema/relationship analysis (cached for reuse; click re-analyze to refresh)
4. In the workbench:
   - **Query**: natural language → SQL preview → result table
   - **Write**: natural language → mutation preview → confirm execute

## Safety model

| Layer | Behavior |
|-------|----------|
| Read | SELECT only; whitelist tables/columns; enforce max rows |
| Write | No free-form DML; structured mutate only; WHERE required by policy; preview + confirm |
| Secrets | DB passwords and LLM API keys encrypted at rest in meta DB |

## Main APIs

| Area | Examples |
|------|----------|
| Connections | `GET/POST /api/connections`, `POST .../test`, `GET .../schema` |
| Policy | `GET/PUT /api/connections/{id}/policy` |
| Workspace | `PUT .../workspace/tables`, `POST .../workspace/interpret`, `GET .../workspace/analysis` |
| Query | `POST .../query/nl`, `.../query/sql`, `.../query/structured` |
| Mutate | `POST .../mutate/preview`, `.../mutate/nl`, `.../mutate/confirm` |
| Settings | `GET/PUT /api/settings` |
| Audit | `GET /api/audit` |

## Notes

- SQL Server needs a working ODBC driver (`ODBC Driver 18 for SQL Server` by default).
- Without an LLM key, schema analysis falls back to metadata/FK summary; NL query/write require a key.
- This is an internal MVP — not a substitute for production RBAC, network isolation, or DBA review of high-risk writes.

## License

Private / internal use unless otherwise specified.
