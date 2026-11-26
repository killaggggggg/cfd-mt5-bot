import { useEffect, useState } from "react";
import { HelpCircle, type LucideIcon } from "lucide-react";
import { HelpHintCard, type HelpHintContent } from "./HelpHint";

interface Props {
  label: string;
  value: string;
  hint?: string;
  icon: LucideIcon;
  valueTone?: "default" | "success" | "danger" | "warning" | "brand";
  helpHint?: HelpHintContent;
  forceOpen?: boolean;
}

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  valueTone = "default",
  helpHint,
  forceOpen = false,
}: Props) {
  const [hintOpen, setHintOpen] = useState(false);

  useEffect(() => {
    setHintOpen(forceOpen);
  }, [forceOpen]);

  const toneClass =
    valueTone === "success"
      ? "text-success"
      : valueTone === "danger"
      ? "text-danger"
      : valueTone === "warning"
      ? "text-warning"
      : valueTone === "brand"
      ? "text-brand"
      : "";

  return (
    <div className="stat-card">
      <div className="stat-card-top">
        <div className="stat-card-label-row">
          <span className="stat-card-label">{label}</span>
          {helpHint && (
            <button
              type="button"
              className={`help-dot-btn ${hintOpen ? "is-open" : ""}`}
              aria-expanded={hintOpen}
              aria-label={`คำอธิบาย: ${label}`}
              title={helpHint.summary}
              onClick={() => setHintOpen((prev) => !prev)}
            >
              <HelpCircle size={13} />
            </button>
          )}
        </div>
        <span className="stat-icon-box">
          <Icon size={17} />
        </span>
      </div>
      <div className={`stat-card-val tabular ${toneClass}`}>{value}</div>
      {hint && <div className="stat-card-hint tabular">{hint}</div>}
      {helpHint && hintOpen && (
        <HelpHintCard title={label} hint={helpHint} onClose={() => setHintOpen(false)} />
      )}
    </div>
  );
}
