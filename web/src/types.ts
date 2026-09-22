export type Dialect = 'sqlite' | 'mysql' | 'postgresql' | 'sqlserver'

export type Connection = {
  id: string
  name: string
  dialect: Dialect
  host?: string | null
  port?: number | null
  database?: string | null
  username?: string | null
  has_password: boolean
  options: Record<string, unknown>
}

export type ColumnInfo = {
  name: string
  type: string
  nullable: boolean
  default?: string | null
  primary_key: boolean
}

export type TableInfo = {
  name: string
  schema_name?: string | null
  columns: ColumnInfo[]
  primary_key: string[]
  foreign_keys: Array<{
    constrained_columns: string[]
    referred_table: string
    referred_columns: string[]
  }>
}

export type SchemaOverview = {
  connection_id: string
  dialect: Dialect
  tables: TableInfo[]
}

export type InterpretResult = {
  connection_id: string
  dialect: Dialect
  selected_tables: string[]
  overview: string
  tables: Array<{ name: string; purpose: string; key_fields: string[] }>
  relationships: Array<{
    from_table: string
    to_table: string
    via: string
    note: string
  }>
  join_hints: string[]
  warnings: string[]
  source: string
}

export type QueryResult = {
  sql: string
  tables: string[]
  limit?: number | null
  dry_run: boolean
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  explanation?: string | null
  reply?: string | null
}

export type MutatePreview = {
  preview_id: string
  operation: string
  table: string
  sql: string
  affected_count: number
  sample_rows: Record<string, unknown>[]
  blocked: boolean
  block_reason?: string | null
  explanation?: string | null
  reply?: string | null
}

export type Settings = {
  llm_configured: boolean
  llm_base_url?: string | null
  llm_model?: string | null
}

export type WizardState = {
  step: number
  connection: Connection | null
  schema: SchemaOverview | null
  selectedTables: Array<{ table: string; schema_name?: string | null }>
  interpret: InterpretResult | null
}
