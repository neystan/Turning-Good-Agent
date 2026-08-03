import type { ReactNode } from "react";

type ProactiveCardProps = {
  title: string;
  subtitle?: string;
  children: ReactNode;
};

/** 为主动工作面提供统一的低干扰运营卡片承载层。 */
export function ProactiveCard({ title, subtitle, children }: ProactiveCardProps) {
  return <section className="proactive-card">
    <header>
      <h3>{title}</h3>
      {subtitle && <p>{subtitle}</p>}
    </header>
    <div className="proactive-card-content">{children}</div>
  </section>;
}
