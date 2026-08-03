import type { ReactNode } from "react";

type ProactiveCardProps = {
  title: string;
  subtitle?: string;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
  card: string;
  id?: string;
  state?: string;
  actionState?: "idle" | "pending" | "error";
};

/** 为主动工作面提供统一的低干扰运营卡片承载层。 */
export function ProactiveCard({ title, subtitle, children, actions, className, card, id, state, actionState = "idle" }: ProactiveCardProps) {
  return <article className={`proactive-card${className ? ` ${className}` : ""}`} data-proactive-card={card} data-proactive-id={id} data-proactive-state={state} data-proactive-action-state={actionState}>
    <header>
      <h3>{title}</h3>
      {subtitle && <p>{subtitle}</p>}
    </header>
    <div className="proactive-card-content">{children}</div>
    {actions && <footer className="proactive-card-actions">{actions}</footer>}
  </article>;
}
