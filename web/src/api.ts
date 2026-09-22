import type {
  Connection,
  Dialect,
  InterpretResult,
  MutatePreview,
  QueryResult,
  SchemaOverview,
  Settings,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
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
    use_llm = true,
  ) =>
    request<InterpretResult>(`/api/connections/${id}/workspace/interpret`, {
      method: 'POST',
      body: JSON.stringify({ tables, use_llm }),
    }),

  nlQuery: (id: string, prompt: string, dry_run = false) =>
    request<QueryResult>(`/api/connections/${id}/query/nl`, {
      method: 'POST',
      body: JSON.stringify({ prompt, dry_run }),
    }),

  structuredQuery: (
    id: string,
    body: { table: string; columns?: string[]; limit?: number },
  ) =>
    request<QueryResult>(`/api/connections/${id}/query/structured`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  nlMutate: (id: string, prompt: string) =>
    request<MutatePreview>(`/api/connections/${id}/mutate/nl`, {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    }),

  confirmMutate: (id: string, preview_id: string) =>
    request<{
      preview_id: string
      operation: string
      table: string
      sql: string
      rowcount: number
      status: string
    }>(`/api/connections/${id}/mutate/confirm`, {
      method: 'POST',
      body: JSON.stringify({ preview_id }),
    }),
}
