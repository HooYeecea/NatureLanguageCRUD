/** Turn raw backend/driver errors into short Chinese UI messages. */

export function friendlyError(raw: unknown, fallback = '操作失败，请稍后重试'): string {
  const text = normalize(raw)
  if (!text) return fallback
  const lower = text.toLowerCase()

  if (
    lower.includes('llm api key') ||
    lower.includes('llm_api_key') ||
    lower.includes('llm not configured') ||
    text.includes('未配置')
  ) {
    return '尚未配置大模型 API Key，请先点击右上角「API 设置」。'
  }

  if (lower.includes('connection refused') || lower.includes('actively refused')) {
    return '无法连接数据库服务，请确认主机地址和端口是否正确、服务是否已启动。'
  }

  if (
    lower.includes('password authentication failed') ||
    lower.includes('access denied for user') ||
    lower.includes('login failed') ||
    lower.includes('authentication failed')
  ) {
    return '数据库账号或密码不正确，请检查后重试。'
  }

  if (
    lower.includes('unknown database') ||
    (lower.includes('does not exist') && lower.includes('database'))
  ) {
    return '指定的数据库不存在，请确认库名是否正确。'
  }

  if (
    lower.includes('no such table') ||
    (lower.includes('does not exist') && lower.includes('relation'))
  ) {
    return '查询涉及的表不存在，或当前策略未允许访问该表。'
  }

  if (lower.includes('timeout') || lower.includes('timed out')) {
    return '连接或查询超时，请检查网络与数据库负载后重试。'
  }

  if (lower.includes('preview expired')) {
    return '预览已过期，请重新发起写入预览后再确认。'
  }

  if (lower.includes('preview not found')) {
    return '找不到该预览记录，可能已执行或已取消。'
  }

  if (lower.includes('multiple sql') || lower.includes('only select')) {
    return '当前仅允许单条只读查询，请换一种问法。'
  }

  if (lower.includes('not in whitelist') || lower.includes('not allowed')) {
    return '该操作不在允许范围内，请回到选表步骤调整可操作表或权限。'
  }

  if (lower.includes('odbc') || lower.includes('driver')) {
    return 'SQL Server 驱动不可用，请确认已安装对应 ODBC 驱动。'
  }

  if (lower.includes('unable to open database') || lower.includes('no such file')) {
    return 'SQLite 文件路径无效或无法打开，请检查路径是否正确。'
  }

  // Strip long traceback / sqlalchemy prefixes for leftover cases
  const cleaned = text
    .replace(/\(.*?Error\)\s*/g, '')
    .replace(/sqlalchemy\.[a-zA-Z.]+:\s*/gi, '')
    .replace(/\s+/g, ' ')
    .trim()

  if (cleaned.length > 160) {
    return `${cleaned.slice(0, 160)}…`
  }
  return cleaned || fallback
}

function normalize(raw: unknown): string {
  if (raw == null) return ''
  if (raw instanceof Error) return raw.message
  if (typeof raw === 'string') return raw
  if (Array.isArray(raw)) {
    return raw
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return JSON.stringify(item)
      })
      .join('；')
  }
  if (typeof raw === 'object' && raw && 'detail' in raw) {
    return normalize((raw as { detail: unknown }).detail)
  }
  try {
    return JSON.stringify(raw)
  } catch {
    return String(raw)
  }
}
