/** Suggest follow-up prompts when a query returns zero rows. */

export function emptyResultHints(opts: {
  userPrompt?: string | null
  sql?: string | null
  tables?: string[]
  limit?: number
}): string[] {
  const sql = (opts.sql || '').toLowerCase()
  const tables = opts.tables || []
  const label = tables[0] || '该表'
  const hints: string[] = []

  if (/\bwhere\b/.test(sql)) {
    hints.push('去掉过滤条件，先列出几条看看有哪些取值')
    hints.push('把等号改成模糊匹配再查一次')
  } else {
    hints.push(`列出${label}的前几条记录`)
  }

  if (/\blike\b/.test(sql) || /%/.test(sql)) {
    hints.push('换一个更短的关键词再试')
  }

  if (/\bstatus\b/.test(sql) || /状态/.test(opts.userPrompt || '')) {
    hints.push('先查一下有哪些不同的状态值')
  }

  if (tables.length > 1) {
    hints.push(`只查 ${tables[0]}，先确认单表有没有数据`)
  }

  hints.push(`统计${label}一共有多少条`)

  const out: string[] = []
  for (const h of hints) {
    if (!h || out.includes(h)) continue
    out.push(h)
    if (out.length >= 4) break
  }
  return out
}
