import type {
  Metric, QueryTemplate, MutationStatus,
  AccessPolicy,
  AuditLog,
  AuthStatus,
  ChatTurn,
  Connection,
  Dialect,
  GlossaryTable,
  InterpretResult,
  MutateExecuteResult,
  MutatePreview,
  MutateUndoResult,
  QueryResult,
  SchemaOverview,
  Settings,
} from './types'
import { friendlyError } from './errors'

const WORKBENCH_API_KEY_STORAGE = 'nlcrud.workbenchApiKey'

export function getWorkbenchApiKey(): string {
  try {
    localStorage.removeItem(WORKBENCH_API_KEY_STORAGE)
    return sessionStorage.getItem(WORKBENCH_API_KEY_STORAGE) || ''
  } catch {
    return ''
  }
}

export function setWorkbenchApiKey(key: string): void {
  try {
    const trimmed = key.trim()
    localStorage.removeItem(WORKBENCH_API_KEY_STORAGE)
    if (trimmed) sessionStorage.setItem(WORKBENCH_API_KEY_STORAGE, trimmed)
    else sessionStorage.removeItem(WORKBENCH_API_KEY_STORAGE)
  } catch {
    /* private mode */
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 60000)
  try {
    return await requestOnce<T>(path, {...init, signal: controller.signal})
  } catch (error) {
    if (controller.signal.aborted) throw new Error('请求等待超时。写入可能仍在执行，请查看原操作状态，不要重复提交。')
    throw error
  } finally { clearTimeout(timer) }
}

async function requestOnce<T>(path: string, init?: RequestInit): Promise<T> {
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
  metrics: (id: string) => request<{items: Metric[]; can_edit: boolean}>(`/api/connections/${id}/metrics`),
  saveMetric: (id: string, body: Omit<Metric, 'id'>, itemId?: string) => request<Metric>(`/api/connections/${id}/metrics${itemId ? '/' + itemId : ''}`, {method: itemId ? 'PUT' : 'POST', body: JSON.stringify(body)}),
  deleteMetric: (id: string, itemId: string) => request(`/api/connections/${id}/metrics/${itemId}`, {method: 'DELETE'}),
  templates: (id: string) => request<{items: QueryTemplate[]}>(`/api/connections/${id}/templates`),
  saveTemplate: (id: string, body: Omit<QueryTemplate, 'id'>, itemId?: string) => request<QueryTemplate>(`/api/connections/${id}/templates${itemId ? '/' + itemId : ''}`, {method: itemId ? 'PUT' : 'POST', body: JSON.stringify(body)}),
  deleteTemplate: (id: string, itemId: string) => request(`/api/connections/${id}/templates/${itemId}`, {method: 'DELETE'}),
  runTemplate: (id: string, itemId: string, values: Record<string, string>) => request<QueryResult>(`/api/connections/${id}/templates/${itemId}/run`, {method: 'POST', body: JSON.stringify({values})}),
  mutationStatus: (id: string, previewId: string) => request<MutationStatus>(`/api/connections/${id}/mutate/status/${previewId}`),
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

  getPolicy: (id: string) =>
    request<AccessPolicy>(`/api/connections/${id}/policy`),

  selectTables: (
    id: string,
    tables: Array<{
      table: string
      schema_name?: string | null
      secret_columns?: string[]
    }>,
    permissions: {
      allow_select?: boolean
      allow_insert?: boolean
      allow_update?: boolean
      allow_delete?: boolean
    } = {},
  ) =>
    request<AccessPolicy>(`/api/connections/${id}/workspace/tables`, {
      method: 'PUT',
      body: JSON.stringify({
        tables,
        allow_select: permissions.allow_select ?? true,
        allow_insert: permissions.allow_insert ?? true,
        allow_update: permissions.allow_update ?? true,
        allow_delete: permissions.allow_delete ?? false,
      }),
    }),

  cancelMutate: (id: string, preview_id: string) =>
    request<{ preview_id: string; status: string }>(
      `/api/connections/${id}/mutate/cancel/${preview_id}`,
      { method: 'POST' },
    ),

  undoMutate: (id: string, undo_id: string, ack_large_impact = false) =>
    request<MutateUndoResult>(`/api/connections/${id}/mutate/undo/${undo_id}`, {
      method: 'POST',
      body: JSON.stringify({ ack_large_impact }),
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
