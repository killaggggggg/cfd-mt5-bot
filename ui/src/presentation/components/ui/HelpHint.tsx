import { useEffect, useState } from "react";
import { HelpCircle, X } from "lucide-react";

export interface HelpHintContent {
  summary: string;
  example?: string;
  recommended?: string;
}

interface HelpHintCardProps {
  title: string;
  hint: HelpHintContent;
  onClose: () => void;
}

/**
 * Shared expandable explanation box reused across fields, KPI cards, and section hints.
 */
export function HelpHintCard({ title, hint, onClose }: HelpHintCardProps) {
  return (
    <div className="help-hint-card" role="note">
      <div className="help-hint-head">
        <span>คำอธิบาย: {title}</span>
        <button
          type="button"
          className="help-hint-close"
          aria-label={`ปิดคำอธิบาย: ${title}`}
          onClick={onClose}
        >
          <X size={13} />
        </button>
      </div>
      <p className="help-hint-text">{hint.summary}</p>
      {hint.example && (
        <div className="help-hint-meta">
          <strong>ตัวอย่าง:</strong> {hint.example}
        </div>
      )}
      {hint.recommended && (
        <div className="help-hint-meta text-brand">
          <strong>ค่าแนะนำ:</strong> {hint.recommended}
        </div>
      )}
    </div>
  );
}

export interface InlineHintToggleProps {
  label: string;
  hint: HelpHintContent;
  forceOpen?: boolean;
  titleText?: string;
}

/**
 * Compact `(?)` trigger button with standalone expandable HelpHintCard for sections,
 * inline filter rows, and custom control headers outside `FieldWithHint`.
 */
export function InlineHintToggle({
  label,
  hint,
  forceOpen = false,
  titleText,
}: InlineHintToggleProps) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setOpen(forceOpen);
  }, [forceOpen]);

  return (
    <div className="inline-hint-wrap">
      <div className="inline-hint-row">
        {titleText && <span className="inline-hint-title">{titleText}</span>}
        <button
          type="button"
          className={`help-dot-btn ${open ? "is-open" : ""}`}
          aria-expanded={open}
          aria-label={`คำอธิบายเพิ่มเติม: ${label}`}
          title={hint.summary}
          onClick={() => setOpen((prev) => !prev)}
        >
          <HelpCircle size={13} />
        </button>
      </div>
      {open && <HelpHintCard title={label} hint={hint} onClose={() => setOpen(false)} />}
    </div>
  );
}

interface LabelWithHintProps {
  label: string;
  hint: HelpHintContent;
  forceOpen?: boolean;
  children?: React.ReactNode;
  smallText?: string;
  badgeRight?: React.ReactNode;
  disabledVisual?: boolean;
}

/**
 * Field wrapper pairing a form label with a concise `<small>` hint, optional
 * status/action pill, and an interactive `(?)` expandable explanation card.
 */
export function FieldWithHint({
  label,
  hint,
  forceOpen = false,
  smallText,
  badgeRight,
  disabledVisual = false,
  children,
}: LabelWithHintProps) {
  const [localOpen, setLocalOpen] = useState(false);

  useEffect(() => {
    setLocalOpen(forceOpen);
  }, [forceOpen]);

  return (
    <div className={`field ${disabledVisual ? "is-opt-off" : ""}`}>
      <div className="field-label-row">
        <label>{label}</label>
        <div className="field-label-tools">
          {badgeRight}
          <button
            type="button"
            className={`help-dot-btn ${localOpen ? "is-open" : ""}`}
            aria-expanded={localOpen}
            aria-label={`คำอธิบายเพิ่มเติม: ${label}`}
            title={hint.summary}
            onClick={() => setLocalOpen((prev) => !prev)}
          >
            <HelpCircle size={14} />
          </button>
        </div>
      </div>

      {children}

      {smallText && <small>{smallText}</small>}

      {localOpen && <HelpHintCard title={label} hint={hint} onClose={() => setLocalOpen(false)} />}
    </div>
  );
}

interface OptionalNumberFieldProps {
  label: string;
  hint: HelpHintContent;
  value: number | null;
  onChange: (val: number | null) => void;
  smallText?: string;
  forceOpen?: boolean;
  integerOnly?: boolean;
  min?: number;
  max?: number;
  placeholder?: string;
  offText?: string;
}

/**
 * Truly optional numeric setting: leaving it empty (`null`) or `0` disables
 * the corresponding guard completely, with a 1-click "ปิดใช้" clear action.
 */
export function OptionalNumberField({
  label,
  hint,
  value,
  onChange,
  smallText,
  forceOpen = false,
  integerOnly = false,
  min = 0,
  max,
  placeholder = "เว้นว่าง = ไม่ใช้งาน",
  offText = "ไม่ใช้งาน",
}: OptionalNumberFieldProps) {
  const isDisabled = value === null || value <= 0;

  return (
    <FieldWithHint
      label={label}
      hint={hint}
      forceOpen={forceOpen}
      disabledVisual={isDisabled}
      smallText={
        isDisabled
          ? `ตอนนี้เว้นว่าง/ปิดอยู่ — ระบบจะไม่นำข้อนี้มาจำกัด (${smallText ?? "ใส่ตัวเลขเมื่อต้องการเปิดใช้"})`
          : smallText
      }
      badgeRight={
        isDisabled ? (
          <span className="opt-status-pill is-off">{offText}</span>
        ) : (
          <button
            type="button"
            className="opt-clear-btn"
            title="ล้างค่าเพื่อปิดการใช้งานข้อนี้"
            onClick={() => onChange(null)}
          >
            ไม่ใช้
          </button>
        )
      }
    >
      <input
        type="number"
        step="any"
        min={min}
        max={max}
        placeholder={placeholder}
        value={value === null ? "" : value}
        onChange={(e) => {
          const raw = e.target.value.trim();
          if (raw === "") {
            onChange(null);
            return;
          }
          const num = Number(raw);
          if (!Number.isFinite(num)) return;
          onChange(integerOnly ? Math.max(0, Math.round(num)) : num);
        }}
      />
    </FieldWithHint>
  );
}
