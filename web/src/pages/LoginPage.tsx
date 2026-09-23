import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { getAuthConfig, login, register, type AuthIdentity } from "../api/auth";
import { ApiError } from "../api/documents";

export function LoginPage({ identity }: { identity: AuthIdentity | null }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signup, setSignup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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

  return <main className="auth-screen" id="main-content">
    <section className="auth-card" aria-labelledby="auth-title">
      <span className="eyebrow">Review Agent</span>
      <h1 id="auth-title">{signup ? "创建学习账号" : "登录学习空间"}</h1>
      <p>你的资料、问答和练习保存在专属空间。</p>
      <form onSubmit={(event) => void submit(event)}>
        <label htmlFor="auth-email">邮箱</label>
        <input id="auth-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        <label htmlFor="auth-password">密码</label>
        <input id="auth-password" type="password" autoComplete={signup ? "new-password" : "current-password"} minLength={signup ? 12 : 1} value={password} onChange={(event) => setPassword(event.target.value)} required />
        {signup ? <p className="auth-hint">至少 12 个字符。</p> : null}
        {error ? <p className="field-error" role="alert">{error}</p> : null}
        <button className="button button--primary" disabled={busy} type="submit">{busy ? "请稍候…" : signup ? "创建账号" : "登录"}</button>
      </form>
      {config.data?.signup_enabled ? <button className="auth-switch" type="button" onClick={() => { setSignup(!signup); setError(""); }}>{signup ? "已有账号？返回登录" : "没有账号？创建账号"}</button> : null}
    </section>
  </main>;
}
