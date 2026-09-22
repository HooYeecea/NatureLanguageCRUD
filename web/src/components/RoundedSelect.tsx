import { useEffect, useId, useRef, useState } from 'react'

export type SelectOption = { label: string; value: string }

type Props = {
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  ariaLabel: string
}

export function RoundedSelect({ value, options, onChange, ariaLabel }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  const current = options.find((o) => o.value === value) || options[0]

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  return (
    <div className={`rounded-select ${open ? 'open' : ''}`} ref={rootRef}>
      <button
        type="button"
        className="rounded-select-trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="rounded-select-value">{current?.label ?? '请选择'}</span>
        <span className="rounded-select-caret" aria-hidden />
      </button>

      {open && (
        <ul className="rounded-select-menu" role="listbox" id={listId}>
          {options.map((opt) => {
            const active = opt.value === value
            return (
              <li key={opt.value} role="option" aria-selected={active}>
                <button
                  type="button"
                  className={`rounded-select-option ${active ? 'active' : ''}`}
                  onClick={() => {
                    onChange(opt.value)
                    setOpen(false)
                  }}
                >
                  {opt.label}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
