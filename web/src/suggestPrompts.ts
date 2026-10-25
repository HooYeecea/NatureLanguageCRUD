/** Build a few clickable NL prompt suggestions from tables + glossary. */

import type { GlossaryTable } from './types'
import { heuristicTableAlias } from './tableAlias'

export function suggestPrompts(
  mode: 'query' | 'mutate',
  tables: string[],
  glossary: GlossaryTable[] = [],
  limit = 5,
): string[] {
  if (!tables.length) return []
  const aliasOf = (name: string) => {
    const g = glossary.find((t) => t.table === name)
    return g?.alias || heuristicTableAlias(name) || name
  }
  const primary = tables[0]
  const label = aliasOf(primary)
  const second = tables[1]
  const secondLabel = second ? aliasOf(second) : ''

  const queryHints = [
    `列出${label}的前几条记录`,
    `统计${label}一共有多少条`,
    second ? `看看${label}和${secondLabel}之间有什么关联` : `按状态分组看一下${label}`,
    `查出最近更新的${label}`,
    `只看${label}里状态为启用的记录`,
  ]

  const mutateHints = [
    `给${label}新增一条示例记录`,
    `把某条${label}的状态改成已完成`,
    `更新${label}里 id 为 1 的记录备注`,
    second ? `为${secondLabel}关联一条新的${label}` : `给${label}补一条高优先级数据`,
    `删除${label}里明显的测试数据（需确认）`,
  ]

  const pool = mode === 'query' ? queryHints : mutateHints
  const out: string[] = []
  for (const p of pool) {
    if (!p || out.includes(p)) continue
    out.push(p)
    if (out.length >= limit) break
  }
  return out
}
