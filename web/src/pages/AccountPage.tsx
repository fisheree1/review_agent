import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { changePassword, logout, type AuthIdentity } from "../api/auth";
import { ApiError } from "../api/documents";
import { PageHeading } from "../components/PageHeading";
import { AppHeader } from "../components/AppHeader";

export function AccountPage({ identity }: { identity: AuthIdentity }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  async function signOut() {
    setBusy(true);
    try {
      await logout();
      queryClient.clear();
      navigate("/login", { replace: true });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "退出失败，请重试");
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await changePassword(currentPassword, newPassword);
      queryClient.clear();
      navigate("/login", { replace: true });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "修改失败，请重试");
      setBusy(false);
    }
  }

  return <div className="app-page">
    <AppHeader />
    <main className="account-page" id="main-content" tabIndex={-1}>
      <PageHeading title="我的账号" kicker="专属学习空间" description="管理你的账号与登录信息。" />
      <section className="learning-card account-identity"><div><h2>登录信息</h2><p>当前登录：{identity.login_mode === "portal" ? "门户管理员" : identity.email}</p></div>
      {identity.login_mode === "portal" ? <a className="button button--secondary" href="/">返回门户管理登录</a> : <button className="button button--secondary" disabled={busy} onClick={() => void signOut()} type="button">退出登录</button>}</section>
      {identity.login_mode === "portal" ? <section className="learning-card"><p>登录和密码由门户统一管理，退出门户后此项目也会停止访问。</p></section> : <section className="account-password learning-card" aria-labelledby="change-password-title">
        <h2 id="change-password-title">修改密码</h2>
        <p>修改后需要使用新密码重新登录。</p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="current-password">当前密码</label>
          <input id="current-password" name="current-password" aria-describedby={error ? "password-error" : undefined} autoComplete="current-password" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required />
          <label htmlFor="new-password">新密码</label>
          <input id="new-password" name="new-password" aria-describedby={error ? "password-error" : undefined} autoComplete="new-password" type="password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
          {error ? <p className="field-error" id="password-error" role="alert">{error}</p> : null}
          <button className="button button--primary" disabled={busy} type="submit">保存新密码</button>
        </form>
      </section>}
    </main>
  </div>;
}
