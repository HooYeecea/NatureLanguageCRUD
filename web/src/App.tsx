import { useEffect, useState } from 'react'
import { Stepper } from './components/Stepper'
import { SettingsModal } from './components/SettingsModal'
import { ConnectStep } from './steps/ConnectStep'
import { InterpretStep } from './steps/InterpretStep'
import { SelectTablesStep } from './steps/SelectTablesStep'
import { WorkbenchStep } from './steps/WorkbenchStep'
import { api } from './api'
import type { Connection, InterpretResult, SchemaOverview, Settings } from './types'
import './App.css'

const LABELS = ['连接数据库', '选择表', '解读关系', '工作台']

export default function App() {
  const [step, setStep] = useState(1)
  const [connection, setConnection] = useState<Connection | null>(null)
  const [schema, setSchema] = useState<SchemaOverview | null>(null)
  const [selectedTables, setSelectedTables] = useState<
    Array<{ table: string; schema_name?: string | null }>
  >([])
  const [interpret, setInterpret] = useState<InterpretResult | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settings, setSettings] = useState<Settings | null>(null)

  useEffect(() => {
    api.settings().then(setSettings).catch(() => setSettings(null))
  }, [])

  function restart() {
    setStep(1)
    setConnection(null)
    setSchema(null)
    setSelectedTables([])
    setInterpret(null)
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
            onBack={() => setStep(3)}
            onRestart={restart}
          />
        )}
      </main>

      {schema && step > 2 && (
        <footer className="foot muted">
          当前 schema 已加载 {schema.tables.length} 张表 · 已选 {selectedTables.length} 张
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
