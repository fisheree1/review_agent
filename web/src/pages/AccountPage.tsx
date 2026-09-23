import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { changePassword, logout, type AuthIdentity } from "../api/auth";
import { ApiError } from "../api/documents";
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
    <main className="account-page" id="main-content">
      <span className="eyebrow">账号与安全</span>
      <h1>我的账号</h1>
      <p>当前登录：{identity.email}</p>
      <button className="button button--secondary" disabled={busy} onClick={() => void signOut()} type="button">退出登录</button>
      <section className="account-password" aria-labelledby="change-password-title">
        <h2 id="change-password-title">修改密码</h2>
        <p>修改后需要使用新密码重新登录。</p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="current-password">当前密码</label>
          <input id="current-password" autoComplete="current-password" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required />
          <label htmlFor="new-password">新密码</label>
          <input id="new-password" autoComplete="new-password" type="password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
          {error ? <p className="field-error" role="alert">{error}</p> : null}
          <button className="button button--primary" disabled={busy} type="submit">保存新密码</button>
        </form>
      </section>
    </main>
  </div>;
}
