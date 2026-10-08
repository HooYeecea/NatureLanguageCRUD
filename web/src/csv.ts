/** Browser-side CSV download for query result tables. */

function escapeCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

export function rowsToCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return ''
  const cols = Object.keys(rows[0])
  const lines = [cols.map(escapeCell).join(',')]
  for (const row of rows) {
    lines.push(cols.map((c) => escapeCell(row[c])).join(','))
  }
  return lines.join('\n')
}

export function downloadCsv(filename: string, rows: Record<string, unknown>[]): void {
  const csv = rowsToCsv(rows)
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
