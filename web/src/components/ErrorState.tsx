import { Icon } from "./Icon";

interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ title = "暂时无法显示内容", message, onRetry }: ErrorStateProps) {
  return (
    <section aria-live="polite" className="error-state" role="alert">
      <span className="error-state__icon"><Icon name="error" /></span>
      <div>
        <h2>{title}</h2>
        <p>{message}</p>
        {onRetry ? (
          <button className="button button--secondary" onClick={onRetry} type="button">
            <Icon name="refresh" />重新尝试
          </button>
        ) : null}
      </div>
    </section>
  );
}
