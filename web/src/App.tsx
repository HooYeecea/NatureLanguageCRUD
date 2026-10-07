import { useEffect, useState } from 'react'
import { Stepper } from './components/Stepper'
import { SettingsModal } from './components/SettingsModal'
import { ConnectStep } from './steps/ConnectStep'
import { InterpretStep } from './steps/InterpretStep'
import { SelectTablesStep } from './steps/SelectTablesStep'
import { WorkbenchStep } from './steps/WorkbenchStep'
import { api } from './api'
import {
  clearWorkspaceSession,
  loadWorkspaceSession,
  saveWorkspaceSession,
  type PersistedChatItem,
} from './session'
import type { Connection, InterpretResult, SchemaOverview, Settings } from './types'
import './App.css'

const LABELS = ['连接数据库', '选择表', '解读关系', '工作台']

export default function App() {
  const restored = loadWorkspaceSession()
  const initialStep =
    restored?.step === 4 && (!restored.connection || !restored.interpret)
      ? 3
      : restored?.step || 1
  const [step, setStep] = useState(initialStep)
  const [connection, setConnection] = useState<Connection | null>(restored?.connection || null)
  const [schema, setSchema] = useState<SchemaOverview | null>(restored?.schema || null)
  const [selectedTables, setSelectedTables] = useState<
    Array<{ table: string; schema_name?: string | null }>
  >(restored?.selectedTables || [])
  const [interpret, setInterpret] = useState<InterpretResult | null>(restored?.interpret || null)
  const [chatItems, setChatItems] = useState<PersistedChatItem[] | undefined>(restored?.chatItems)
  const [workbenchMode, setWorkbenchMode] = useState<'query' | 'mutate'>(
    restored?.mode || 'query',
  )
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    api.settings().then(setSettings).catch(() => setSettings(null))
    // Validate restored connection still exists
    if (restored?.connection?.id) {
      api
        .listConnections()
        .then((list) => {
          const still = list.find((c) => c.id === restored.connection!.id)
          if (!still) {
            clearWorkspaceSession()
            setStep(1)
            setConnection(null)
            setSchema(null)
            setSelectedTables([])
            setInterpret(null)
            setChatItems(undefined)
          } else {
            setConnection(still)
          }
        })
        .catch(() => undefined)
        .finally(() => setHydrated(true))
    } else {
      setHydrated(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!hydrated) return
    if (step === 1 && !connection) {
      clearWorkspaceSession()
      return
    }
    saveWorkspaceSession({
      step,
      connection,
      schema,
      selectedTables,
      interpret,
      chatItems,
      mode: workbenchMode,
    })
  }, [
    hydrated,
    step,
    connection,
    schema,
    selectedTables,
    interpret,
    chatItems,
    workbenchMode,
  ])

  function restart() {
    clearWorkspaceSession()
    setStep(1)
    setConnection(null)
    setSchema(null)
    setSelectedTables([])
    setInterpret(null)
    setChatItems(undefined)
    setWorkbenchMode('query')
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="brand">NL CRUD Workbench</p>
          <h1>自然语言数据库工作台</h1>
        </div>
        <div className="topbar-right">
          <p className="tagline">连接 → 选表 → 理解结构 → 安全操作</p>
          <button
            type="button"
            className="btn ghost settings-btn"
            onClick={() => setSettingsOpen(true)}
          >
            API 设置
            <span className={`dot ${settings?.llm_configured ? 'on' : ''}`} />
          </button>
        </div>
      </header>

      <Stepper step={step} labels={LABELS} />

      <main>
        {step === 1 && (
          <ConnectStep
            onConnected={(conn) => {
              setConnection(conn)
              setChatItems(undefined)
              setInterpret(null)
              setStep(2)
            }}
          />
        )}

        {step === 2 && connection && (
          <SelectTablesStep
            connection={connection}
            onBack={() => setStep(1)}
            onNext={(s, selected) => {
              setSchema(s)
              setSelectedTables(selected)
              setStep(3)
            }}
          />
        )}

        {step === 3 && connection && (
          <InterpretStep
            connection={connection}
            selectedTables={selectedTables}
            onBack={() => setStep(2)}
            onNext={(result) => {
              setInterpret(result)
              setStep(4)
            }}
          />
        )}

        {step === 4 && connection && interpret && (
          <WorkbenchStep
            connection={connection}
            interpret={interpret}
            initialItems={chatItems}
            initialMode={workbenchMode}
            onChatChange={setChatItems}
            onModeChange={setWorkbenchMode}
            onBack={() => setStep(3)}
            onRestart={restart}
          />
        )}
      </main>

      {schema && step > 2 && (
        <footer className="foot muted">
          当前 schema 已加载 {schema.tables.length} 张表 · 已选 {selectedTables.length} 张
          {step === 4 && ' · 工作区已自动保存'}
        </footer>
      )}

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={(s) => setSettings(s)}
      />
    </div>
  )
}
