import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { getAuthConfig, login, register, type AuthIdentity } from "../api/auth";
import { ApiError } from "../api/documents";
import { BrandMark } from "../components/BrandMark";
import { Icon } from "../components/Icon";
import { ThemeToggle } from "../components/ThemeToggle";

export function LoginPage({ identity }: { identity: AuthIdentity | null }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signup, setSignup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const errorSummary = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (error) errorSummary.current?.focus(); }, [error]);
  const config = useQuery({ queryKey: ["auth-config"], queryFn: getAuthConfig });
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  if (identity) return <Navigate to="/" replace />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = signup ? await register(email, password) : await login(email, password);
      queryClient.setQueryData(["auth-me"], result);
      const target = (location.state as { from?: string } | null)?.from;
      navigate(target?.startsWith("/") && !target.startsWith("//") ? target : "/", { replace: true });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "无法连接服务，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return <main className="auth-screen" id="main-content" tabIndex={-1}>
    <aside className="auth-story" aria-label="学习空间介绍">
      <span className="auth-story__kicker">你的知识，值得被认真对待</span>
      <h2>读有所思。<br />学有所获。</h2>
      <p>从原文到理解，从练习到记忆。<br />为每一次学习，留下一条清晰的路径。</p>
      <div className="auth-story__journey"><span><Icon name="document" />阅读资料</span><span><Icon name="chat" />理解知识</span><span><Icon name="review" />持续复习</span></div>
      <div className="auth-story__note"><Icon name="shield" /><span>资料与学习记录，保存在你的专属空间。</span></div>
    </aside>
    <section className="auth-card" aria-labelledby="auth-title">
      <div className="auth-brand"><span className="brand" translate="no"><span className="brand__mark"><BrandMark /></span><span>Review Agent</span></span><ThemeToggle /></div>
      <h1 id="auth-title">{signup ? "创建学习账号" : "登录学习空间"}</h1>
      <p>你的资料、问答和练习保存在专属空间。</p>
      {config.data?.login_mode === "portal" ? <a className="button button--primary" href="/login">使用门户账号登录</a> : <form onSubmit={(event) => void submit(event)}>
        <label htmlFor="auth-email">邮箱</label>
        <input id="auth-email" name="email" type="email" inputMode="email" spellCheck={false} aria-describedby={error ? "auth-error" : undefined} autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        <label htmlFor="auth-password">密码</label>
        <input id="auth-password" name="password" aria-describedby={error ? "auth-error" : signup ? "auth-password-help" : undefined} type="password" autoComplete={signup ? "new-password" : "current-password"} minLength={signup ? 12 : 1} value={password} onChange={(event) => setPassword(event.target.value)} required />
        {signup ? <p className="auth-hint" id="auth-password-help">至少 12 个字符。</p> : null}
        {error ? <p className="field-error" id="auth-error" ref={errorSummary} tabIndex={-1} role="alert">{error}</p> : null}
        <button className="button button--primary" disabled={busy} type="submit">{busy ? "请稍候…" : signup ? "创建账号" : "登录"}</button>
      </form>}
      {config.data?.signup_enabled ? <button className="auth-switch" type="button" onClick={() => { setSignup(!signup); setError(""); }}>{signup ? "已有账号？返回登录" : "没有账号？创建账号"}</button> : null}
    </section>
  </main>;
}
