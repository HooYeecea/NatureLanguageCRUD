/** Lightweight Chinese aliases for table search (mirrors backend glossary heuristics). */

const PREFIXES = ['sys_', 'tb_', 't_', 'tbl_', 't_sys_']
const TOKEN_ALIAS: Record<string, string> = {
  role: '角色',
  roles: '角色',
  user: '用户',
  users: '用户',
  menu: '菜单',
  dept: '部门',
  depart: '部门',
  department: '部门',
  dict: '字典',
  log: '日志',
  config: '配置',
  conf: '配置',
  perm: '权限',
  perms: '权限',
  permission: '权限',
  order: '订单',
  task: '任务',
  job: '任务',
  post: '岗位',
  notice: '通知',
  file: '文件',
  oper: '操作',
  login: '登录',
  session: '会话',
}

export function heuristicTableAlias(table: string): string {
  let stem = table
  const lower = table.toLowerCase()
  for (const p of PREFIXES) {
    if (lower.startsWith(p)) {
      stem = table.slice(p.length)
      break
    }
  }
  const parts = stem.split(/[_\-]+/).filter(Boolean)
  if (!parts.length) return table
  return parts.map((p) => TOKEN_ALIAS[p.toLowerCase()] || p).join('')
}
