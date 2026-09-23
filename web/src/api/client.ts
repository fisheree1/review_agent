let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(input, { ...init, credentials: "same-origin", headers });
  if (response.status === 401 && !input.startsWith("/api/v1/auth/me")) {
    window.dispatchEvent(new Event("review-agent-auth-expired"));
  }
  return response;
}
