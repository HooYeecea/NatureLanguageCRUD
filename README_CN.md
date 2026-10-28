# NL CRUD 自然语言数据库工作台

[English](./README.md)

用自然语言操作关系型数据库的内部工作台。连接数据库、选择表、让模型理解表结构与关系，然后用中文完成查询或写入，无需手写 SQL。

> 定位：内部工具 / MVP。查询与写入均受策略约束；写入采用「预览 → 确认」两段式。

## 功能特性

- **多数据库连接**：SQLite、MySQL、PostgreSQL、SQL Server
- **向导式界面**：连接 → 选表 → 解读关系 → 工作台
- **选表搜索**：按表名 / 中文别名 / 字段过滤，并按 schema 分组
- **业务词典**：表/字段别名注入 NL 提示（解读页可编辑）
- **访问策略**：表白名单 / 字段黑白名单、操作权限、行数上限、更新删除强制 WHERE
- **受控查询**：自然语言 → 仅允许 SELECT，经 sqlglot 校验并强制 `LIMIT`
- **样例辅助**：解读与查写时抽取少量脱敏样例行（不落分析缓存）
- **低成本结果校验**：优先启发式；点名对象却整表倒出时自动改写一次；也可编辑 SQL 再执行
- **多轮对话**：追问会复用近期问题与 SQL
- **受限写入**：结构化 insert/update/delete → 预览 → 确认；大批量需二次勾选；执行后回读变更行
- **无主键拒写**：目标表无主键时拒绝 update/delete
- **可编辑写入 SQL**：确认前可通过 `mutate/revise` 按结构化守卫重解析
- **连接池**：跨请求复用引擎；连接变更时失效
- **事务化预览**：update/delete 在事务内 dry-run 后 rollback，确认前校验 SQL
- **原子确认**：`preview_id` 仅可 `pending → executing` 一次，防重复点击
- **敏感列脱敏**：策略 `secret_columns` + 列名启发式，覆盖样例 / 查询结果 / 写入回读
- **可选 API Key**：设置 `WORKBENCH_API_KEY`（生产环境必填）；前端通过 `X-API-Key` 访问
- **取消预览走后端**：确认条「取消」会调用 `mutate/cancel`
- **变更 Diff**：确认后返回 before/after，工作台并排对比字段
- **敏感列 UI**：选表步骤可勾选脱敏字段
- **推荐问法**：按已选表与词典生成可点击示例
- **受控 JOIN / 聚合**：INNER/LEFT/RIGHT JOIN（需 ON）；COUNT/SUM/AVG/MIN/MAX + GROUP BY
- **Schema 漂移检测**：指纹比对选表结构，变化时自动重新解读
- **可撤销删除**：DELETE 后短时保留主键快照，可通过 `mutate/undo` 恢复
- **一键推荐 / 空结果引导**：推荐问法点击即发送；0 行时给出可点改写建议
- **选表权限开关**：选表步骤可开/关新增、更新、删除
- **Docker Compose + CI**：`docker compose up` 起 API+UI；GitHub Actions 跑 pytest 与前端构建
- **CSV 导出**：查询结果可下载；打到 `LIMIT` 时明确提示已截断

- **工作区恢复**：向导与聊天刷新后尽量保留（`sessionStorage`）
- **表关系分析缓存**：相同选表可复用；支持重新用大模型分析
- **API 设置界面**：API Key / Base URL / Model（预设 + 自定义），Key 加密存储
- **审计界面**：顶栏打开，查看操作历史、SQL 与影响行

## 目录结构

```text
NatureLanguageCRUD/
├── app/                 # FastAPI 后端
│   ├── api/             # REST 接口
│   ├── db/              # 引擎与 schema 发现
│   ├── query/           # 受控查询与 NL 查询
│   ├── mutate/          # 受限写入流水线
│   └── ...
├── web/                 # React + Vite + TypeScript 前端
├── main.py              # 后端启动入口
├── requirements.txt
└── .env.example
```

## 环境要求

- Python 3.11+
- Node.js 18+（前端）
- 可选：Windows 上 SQL Server 需安装 ODBC 驱动

## 快速开始

### 1. 启动后端

```bash
python -m venv .venv

# Windows
.\.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # 或：cp .env.example .env
python main.py
```

接口文档：http://127.0.0.1:8000/docs  
健康检查：http://127.0.0.1:8000/health

首次启动会自动准备本地演示库 `demo.db`，以及连接 `local-demo-sqlite`。

### 2. 启动前端

```bash
cd web
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:5173  
Vite 已将 `/api` 代理到后端 `8000` 端口。

### 或：Docker Compose

```bash
copy .env.example .env   # 填入 LLM_API_KEY / 密钥
docker compose up --build
```

- 界面：http://127.0.0.1:5173  
- API：http://127.0.0.1:8000  

### 3. 配置大模型与密钥

在界面右上角打开 **API 设置**，或在 `.env` 中配置：

```env
WORKBENCH_ENV=development
WORKBENCH_SECRET_KEY=change-me-to-a-long-random-string
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

- 界面配置的 LLM Key 优先于 `.env`，并加密保存在 `workbench.db`。
- 当 `WORKBENCH_ENV=production` 时，若 `WORKBENCH_SECRET_KEY` 缺失、过短（&lt;16）或仍是占位符，**服务会拒绝启动**。

## 使用流程

1. 新建或复用数据库连接，并测试连通
2. 搜索并多选要操作的表（可用中文别名搜）
3. 查看表结构 / 关系解读，必要时改业务词典，或重新分析
4. 进入工作台：
   - **查询**：自然语言 → SQL → 结果表（可编辑 SQL / 导出 CSV / 追问）
   - **写入**：自然语言 → 变更预览 → 确认（大批量需勾选；执行后回读）
5. 点顶栏 **审计** 查看近期 SQL 与影响行

## 安全机制

| 层级 | 行为 |
|------|------|
| 读 | 仅 SELECT；表白名单；强制最大返回行数 |
| 样例 | 只对 introspection 确认过的标识符做小 `LIMIT` 读取；带语句超时；密钥列打码；不写入分析缓存 |
| 写 | 禁止自由 DML；只走结构化 mutate；策略要求 WHERE；预览后确认；大批量二次确认 |
| 密钥 | 数据库密码与 LLM API Key 加密落库；生产环境禁止不安全的 `WORKBENCH_SECRET_KEY` |
| 审计 | 关键操作落库，可在界面浏览 |

## 主要接口

| 模块 | 示例 |
|------|------|
| 连接 | `GET/POST /api/connections`、`POST .../test`、`GET .../schema` |
| 策略 | `GET/PUT /api/connections/{id}/policy` |
| 工作区 | `PUT .../workspace/tables`、`POST .../workspace/interpret`、`GET .../workspace/analysis`、`GET/PUT .../workspace/glossary` |
| 查询 | `POST .../query/nl`、`.../query/sql`、`.../query/structured` |
| 写入 | `POST .../mutate/preview`、`.../mutate/nl`、`.../mutate/confirm` |
| 设置 | `GET/PUT /api/settings` |
| 审计 | `GET /api/audit`、`GET /api/audit/{id}` |

## 说明

- SQL Server 默认使用 `ODBC Driver 18 for SQL Server`，需本机已安装驱动。
- 未配置 LLM 时，表关系解读会退化为元数据 / 外键摘要；自然语言查写仍需 API Key。
- 本项目为内部 MVP，不能替代生产级权限体系、网络隔离，以及对高危写入的人工复核。

## 许可证

默认私有 / 内部使用，除非另行说明。
