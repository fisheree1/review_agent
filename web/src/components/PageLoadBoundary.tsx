import { Component, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { AppHeader } from "./AppHeader";

export function PageLoading({ authenticated = true }: { authenticated?: boolean }) {
  const reading = useLocation().pathname.startsWith("/documents/");
  return <div className={authenticated ? reading ? "reader-page" : "app-page" : undefined}>
    {authenticated ? <AppHeader /> : null}
    <main className="page-loading" id="main-content" tabIndex={-1} aria-busy="true">
      <p role="status">正在打开页面…</p>
      <div className="page-loading__panel" aria-hidden="true" />
    </main>
  </div>;
}

export class PageLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  render() {
    if (!this.state.failed) return this.props.children;
    return <main className="auth-screen" id="main-content" tabIndex={-1}>
      <section className="learning-card" aria-labelledby="page-load-error">
        <h1 id="page-load-error">页面暂时无法打开</h1>
        <p role="alert">请检查网络后重新加载。已保存的资料和学习记录不会受影响。</p>
        <button className="button button--primary" type="button" onClick={() => window.location.reload()}>重新加载页面</button>
      </section>
    </main>;
  }
}
