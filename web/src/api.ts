import type {
  AuditLog,
  AuthStatus,
  ChatTurn,
  Connection,
  Dialect,
  GlossaryTable,
  InterpretResult,
  MutateExecuteResult,
  MutatePreview,
  QueryResult,
  SchemaOverview,
  Settings,
} from './types'
import { friendlyError } from './errors'

const WORKBENCH_API_KEY_STORAGE = 'nlcrud.workbenchApiKey'

export function getWorkbenchApiKey(): string {
  try {
    return localStorage.getItem(WORKBENCH_API_KEY_STORAGE) || ''
  } catch {
    return ''
  }
}

export function setWorkbenchApiKey(key: string): void {
  try {
    const trimmed = key.trim()
    if (trimmed) localStorage.setItem(WORKBENCH_API_KEY_STORAGE, trimmed)
    else localStorage.removeItem(WORKBENCH_API_KEY_STORAGE)
  } catch {
    /* private mode */
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const workbenchKey = getWorkbenchApiKey()
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(workbenchKey ? { 'X-API-Key': workbenchKey } : {}),
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    let detail: unknown = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? body
    } catch {
      /* ignore */
    }
    if (res.status === 401) {
      throw new Error(friendlyError(detail, '需要工作台 API Key，请在设置中填写 WORKBENCH_API_KEY。'))
    }
    throw new Error(friendlyError(detail))
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  authStatus: () => request<AuthStatus>('/api/auth/status'),

  settings: () => request<Settings>('/api/settings'),

  updateSettings: (body: {
    api_key?: string
    base_url?: string
    model?: string
    clear_api_key?: boolean
  }) =>
    request<Settings>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  listConnections: () => request<Connection[]>('/api/connections'),

  createConnection: (body: {
    name: string
    dialect: Dialect
    host?: string
    port?: number
    database?: string
    username?: string
    password?: string
    options?: Record<string, unknown>
  }) =>
    request<Connection>('/api/connections', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  deleteConnection: (id: string) =>
    request<void>(`/api/connections/${id}`, { method: 'DELETE' }),

  testConnection: (id: string) =>
    request<{ ok: boolean; message: string; server_version?: string }>(
      `/api/connections/${id}/test`,
      { method: 'POST' },
    ),

  getSchema: (id: string) =>
    request<SchemaOverview>(`/api/connections/${id}/schema`),

  selectTables: (
    id: string,
    tables: Array<{ table: string; schema_name?: string | null }>,
  ) =>
    request(`/api/connections/${id}/workspace/tables`, {
      method: 'PUT',
      body: JSON.stringify({
        tables,
        allow_select: true,
        allow_insert: true,
        allow_update: true,
        allow_delete: false,
      }),
    }),

  interpret: (
    id: string,
    tables: Array<{ table: string; schema_name?: string | null }>,
    options: { use_llm?: boolean; force?: boolean } = {},
  ) =>
    request<InterpretResult>(`/api/connections/${id}/workspace/interpret`, {
      method: 'POST',
      body: JSON.stringify({
        tables,
        use_llm: options.use_llm ?? true,
        force: options.force ?? false,
      }),
    }),

  getAnalysis: (id: string) =>
    request<InterpretResult>(`/api/connections/${id}/workspace/analysis`),

  getGlossary: (id: string) =>
    request<{ connection_id: string; tables: GlossaryTable[] }>(
      `/api/connections/${id}/workspace/glossary`,
    ),

  saveGlossary: (id: string, tables: GlossaryTable[]) =>
    request<{ connection_id: string; tables: GlossaryTable[] }>(
      `/api/connections/${id}/workspace/glossary`,
      {
        method: 'PUT',
        body: JSON.stringify({ tables }),
      },
    ),

  nlQuery: (id: string, prompt: string, options: { dry_run?: boolean; history?: ChatTurn[] } = {}) =>
    request<QueryResult>(`/api/connections/${id}/query/nl`, {
      method: 'POST',
      body: JSON.stringify({
        prompt,
        dry_run: options.dry_run ?? false,
        history: options.history ?? [],
      }),
    }),

  runSql: (id: string, sql: string, dry_run = false) =>
    request<QueryResult>(`/api/connections/${id}/query/sql`, {
      method: 'POST',
      body: JSON.stringify({ sql, dry_run }),
    }),

  structuredQuery: (
    id: string,
    body: { table: string; columns?: string[]; limit?: number },
  ) =>
    request<QueryResult>(`/api/connections/${id}/query/structured`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  nlMutate: (id: string, prompt: string, history: ChatTurn[] = []) =>
    request<MutatePreview>(`/api/connections/${id}/mutate/nl`, {
      method: 'POST',
      body: JSON.stringify({ prompt, history }),
    }),

  confirmMutate: (
    id: string,
    preview_id: string,
    options: { ack_large_impact?: boolean } = {},
  ) =>
    request<MutateExecuteResult>(`/api/connections/${id}/mutate/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        preview_id,
        ack_large_impact: options.ack_large_impact ?? false,
      }),
    }),

  reviseMutate: (id: string, preview_id: string, sql: string) =>
    request<MutatePreview>(`/api/connections/${id}/mutate/revise`, {
      method: 'POST',
      body: JSON.stringify({ preview_id, sql }),
    }),

  listAudit: (params: {
    connection_id?: string
    action?: string
    status?: string
    limit?: number
    offset?: number
  } = {}) => {
    const qs = new URLSearchParams()
    if (params.connection_id) qs.set('connection_id', params.connection_id)
    if (params.action) qs.set('action', params.action)
    if (params.status) qs.set('status', params.status)
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.offset != null) qs.set('offset', String(params.offset))
    const q = qs.toString()
    return request<AuditLog[]>(`/api/audit${q ? `?${q}` : ''}`)
  },

  getAudit: (id: string) => request<AuditLog>(`/api/audit/${id}`),
}
