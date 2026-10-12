import type { ReactNode } from "react";

export function PageHeading({ title, kicker, description, actions, className = "" }: {
  title: string; kicker: string; description: string; actions?: ReactNode; className?: string;
}) {
  return <header className={`learning-heading page-heading page-intro ${className}`}>
    <div className="page-intro__copy"><span className="page-intro__kicker">{kicker}</span>
      <h1>{title}</h1><p>{description}</p>
    </div>
    {actions ? <div className="page-intro__actions">{actions}</div> : null}
  </header>;
}
