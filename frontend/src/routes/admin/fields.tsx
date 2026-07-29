// 管理後台共用的表單零件。日後 admin 拆成獨立 bundle 時整個 admin/ 目錄一起搬。

/** 所有 admin 輸入框共用的樣式。 */
export const inputClass =
  'w-full px-3 py-2 text-sm rounded-md border border-border bg-bg-primary text-text-primary placeholder:text-text-secondary/70 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'

interface FieldProps {
  readonly label: string
  readonly hint?: string
  readonly required?: boolean
  readonly children: React.ReactNode
}

export function Field({ label, hint, required, children }: FieldProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-text-secondary">
        {label}
        {required && <span className="text-danger ml-1">*</span>}
      </span>
      {children}
      {hint && <span className="text-[11px] text-text-secondary">{hint}</span>}
    </label>
  )
}

interface SelectProps<T extends string> {
  readonly value: T
  readonly onChange: (v: T) => void
  readonly options: ReadonlyArray<{ readonly value: T; readonly label: string }>
}

export function Select<T extends string>({ value, onChange, options }: SelectProps<T>) {
  return (
    <select value={value} onChange={e => onChange(e.target.value as T)} className={inputClass}>
      {options.map(opt => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}
