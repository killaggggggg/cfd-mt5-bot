import type { ReactNode } from "react";
import type { BadgeTone } from "../../../domain/types";

interface Props {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  title?: string;
}

export function StatusBadge({ tone = "neutral", children, className = "", title }: Props) {
  return (
    <span className={`status-badge tone-${tone} ${className}`} title={title}>
      {children}
    </span>
  );
}
