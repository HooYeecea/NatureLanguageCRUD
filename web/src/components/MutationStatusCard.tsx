import { useEffect, useState } from 'react'
import { api } from '../api'
import type { MutationStatus } from '../types'

const labels: Record<string,string> = {pending:'待确认，尚未取得执行权', executing:'执行中或等待核对', executed:'已完成写入', failed:'执行失败', cancelled:'已取消', expired:'预览已过期', indeterminate:'提交结果待核实'}
export function MutationStatusCard({connectionId, previewId}: {connectionId: string; previewId: string}) {
  const [status, setStatus] = useState<MutationStatus>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [stopped, setStopped] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let attempts = 0
    async function refresh() {
      setLoading(true)
      try {
        const data = await api.mutationStatus(connectionId, previewId)
        if (!live) return
        setStatus(data); setError('')
        attempts += 1
        if (['pending','executing'].includes(data.status) && attempts < 12) timer = setTimeout(refresh, 5000)
        else setStopped(true)
      } catch(e) {if(live) {setError(String(e)); setStopped(true)}}
      finally {if(live) setLoading(false)}
    }
    setStopped(false); void refresh()
    return () => {live=false; if(timer) clearTimeout(timer)}
  }, [connectionId,previewId,revision])
  return <aside className="feature-entry" aria-live="polite">
    <strong>写入状态：{status ? labels[status.status] || status.status : '正在核实'}</strong>
    <p className="muted small">操作编号 {previewId}</p>
    {status?.executed_at && <p>执行时间 {new Date(status.executed_at).toLocaleString()}</p>}
    {(!status || ['executing','indeterminate'].includes(status.status)) && <p>请勿重复提交相同写入。持续未完成或结果待核实时，请联系管理员核对目标数据库。</p>}
    {status?.status === 'pending' && <p>此处不会自动确认。若原请求已结束，可重新预览并核对后操作。</p>}
    {error && <p role="alert">状态查询失败：{error}。无法据此判断写入是否成功。</p>}
    {stopped && <button type="button" className="btn ghost" disabled={loading} onClick={() => setRevision(v => v+1)}>重新查询状态</button>}
  </aside>
}
