import { apiFetch, setCsrfToken } from "./client";
import { parseResponse } from "./documents";

export interface AuthIdentity {
  user_id: string;
  workspace_id: string;
  email: string;
  csrf_token: string;
}

export async function getAuthConfig(): Promise<{ signup_enabled: boolean }> {
  return parseResponse(await fetch("/api/v1/auth/config", { credentials: "same-origin" }));
}

export async function getCurrentUser(): Promise<AuthIdentity> {
  const identity = await parseResponse<AuthIdentity>(await apiFetch("/api/v1/auth/me"));
  setCsrfToken(identity.csrf_token);
  return identity;
}

async function credentialsRequest(path: string, email: string, password: string): Promise<AuthIdentity> {
  const identity = await parseResponse<AuthIdentity>(await fetch(`/api/v1/auth/${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }));
  setCsrfToken(identity.csrf_token);
  return identity;
}

export const login = (email: string, password: string) => credentialsRequest("login", email, password);
export const register = (email: string, password: string) => credentialsRequest("register", email, password);

export async function logout(): Promise<void> {
  const response = await apiFetch("/api/v1/auth/logout", { method: "POST" });
  if (!response.ok) await parseResponse(response);
  setCsrfToken(null);
}

export async function changePassword(current_password: string, new_password: string): Promise<void> {
  const response = await apiFetch("/api/v1/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password, new_password }),
  });
  if (!response.ok) await parseResponse(response);
  setCsrfToken(null);
}
