/** Persist wizard + workbench chat across refresh (sessionStorage). */

import type { Connection, InterpretResult, SchemaOverview } from './types'

const KEY = 'nlcrud.workspace.v1'

export type PersistedChatItem = {
  role: 'user' | 'assistant'
  kind?: 'text' | 'sql' | 'result' | 'error'
  text: string
  sql?: string
  editableSql?: boolean
  rows?: Record<string, unknown>[]
  rowCount?: number
  validationOk?: boolean | null
  validationNote?: string | null
  originalSql?: string | null
  truncated?: boolean
  limit?: number | null
}

export type WorkspaceSession = {
  step: number
  connection: Connection | null
  schema: SchemaOverview | null
  selectedTables: Array<{ table: string; schema_name?: string | null }>
  interpret: InterpretResult | null
  chatItems?: PersistedChatItem[]
  mode?: 'query' | 'mutate'
}

export function loadWorkspaceSession(): WorkspaceSession | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const data = JSON.parse(raw) as WorkspaceSession
    if (!data || typeof data.step !== 'number') return null
    return data
  } catch {
    return null
  }
}

export function saveWorkspaceSession(session: WorkspaceSession): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    /* quota / private mode */
  }
}

export function clearWorkspaceSession(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}
