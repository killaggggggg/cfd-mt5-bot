import type { ReactNode } from "react";

interface Props {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  headerRight?: ReactNode;
  children: ReactNode;
}

export function SectionCard({ title, subtitle, icon, headerRight, children }: Props) {
  return (
    <section className="section-card">
      <div className="section-card-head">
        <div>
          <h2 className="section-card-title">
            {icon}
            <span>{title}</span>
          </h2>
          {subtitle && <p className="section-card-sub">{subtitle}</p>}
        </div>
        {headerRight && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", flex: "0 0 auto", maxWidth: "100%" }}>
            {headerRight}
          </div>
        )}
      </div>
      <div className="section-card-body">{children}</div>
    </section>
  );
}
