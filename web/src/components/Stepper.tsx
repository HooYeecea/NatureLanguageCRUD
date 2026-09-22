type Props = {
  step: number
  labels: string[]
}

export function Stepper({ step, labels }: Props) {
  return (
    <ol className="stepper">
      {labels.map((label, index) => {
        const n = index + 1
        const state = n < step ? 'done' : n === step ? 'current' : 'todo'
        return (
          <li key={label} className={`stepper-item ${state}`}>
            <span className="stepper-num">{n}</span>
            <span className="stepper-label">{label}</span>
          </li>
        )
      })}
    </ol>
  )
}
