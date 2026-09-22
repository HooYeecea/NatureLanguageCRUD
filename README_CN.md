# NL CRUD 自然语言数据库工作台

[English](./README.md)

用自然语言操作关系型数据库的内部工作台。连接数据库、选择表、让模型理解表结构与关系，然后用中文完成查询或写入，无需手写 SQL。

> 定位：内部工具 / MVP。查询与写入均受策略约束；写入采用「预览 → 确认」两段式。

## 功能特性

- **多数据库连接**：SQLite、MySQL、PostgreSQL、SQL Server
- **向导式界面**：连接 → 选表 → 解读关系 → 工作台
- **访问策略**：表白名单 / 字段白黑名单、操作权限、行数上限、更新删除强制 WHERE
- **受控查询**：自然语言 → 仅允许 SELECT，经 sqlglot 校验并强制 `LIMIT`
- **受限写入**：结构化 insert/update/delete → 预览影响 → 确认执行
- **表关系分析缓存**：相同选表可复用分析结果；支持重新用大模型分析
- **API 设置界面**：API Key / Base URL / Model（下拉预设 + 自定义），Key 加密存储
- **审计日志**：连接、策略、查询、写入等关键操作可追溯

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

### 3. 配置大模型

在界面右上角打开 **API 设置**，或在 `.env` 中配置：

```env
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
WORKBENCH_SECRET_KEY=change-me-to-a-long-random-string
```

界面配置的 Key 优先于 `.env`，并加密保存在 `workbench.db`。

## 使用流程

1. 新建或复用数据库连接，并测试连通
2. 多选需要操作的表
3. 查看表结构 / 关系解读（会缓存；可点「重新用大模型分析」）
4. 进入工作台：
   - **查询**：自然语言 → SQL → 结果表
   - **写入**：自然语言 → 变更预览 → 确认执行

## 安全机制

| 层级 | 行为 |
|------|------|
| 读 | 仅 SELECT；表白名单；强制最大返回行数 |
| 写 | 禁止自由 DML；只走结构化 mutate；策略要求 WHERE；预览后确认 |
| 密钥 | 数据库密码与 LLM API Key 加密落库 |

## 主要接口

| 模块 | 示例 |
|------|------|
| 连接 | `GET/POST /api/connections`、`POST .../test`、`GET .../schema` |
| 策略 | `GET/PUT /api/connections/{id}/policy` |
| 工作区 | `PUT .../workspace/tables`、`POST .../workspace/interpret`、`GET .../workspace/analysis` |
| 查询 | `POST .../query/nl`、`.../query/sql`、`.../query/structured` |
| 写入 | `POST .../mutate/preview`、`.../mutate/nl`、`.../mutate/confirm` |
| 设置 | `GET/PUT /api/settings` |
| 审计 | `GET /api/audit` |

## 说明

- SQL Server 默认使用 `ODBC Driver 18 for SQL Server`，需本机已安装驱动。
- 未配置 LLM 时，表关系解读会退化为元数据 / 外键摘要；自然语言查写仍需 API Key。
- 本项目为内部 MVP，不能替代生产级权限体系、网络隔离，以及对高危写入的人工复核。

## 许可证

默认私有 / 内部使用，除非另行说明。
