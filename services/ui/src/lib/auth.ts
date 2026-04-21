export const AUTH_BASE = "/auth";
const TOKEN_KEY = "infra_advisor_token";

// ── Error parsing ─────────────────────────────────────────────────────────────

async function parseErrorMessage(res: Response, fallback: string): Promise<string> {
  const contentType = res.headers.get("content-type") ?? "";
  try {
    if (contentType.includes("application/json")) {
      const body = await res.json();
      return body.detail ?? body.message ?? JSON.stringify(body);
    }
    const text = await res.text();
    // Nginx/proxy error pages are HTML — show a clean message instead of raw markup
    if (text.trimStart().startsWith("<")) {
      return `${fallback} (${res.status})`;
    }
    return text || `${fallback} (${res.status})`;
  } catch {
    return `${fallback} (${res.status})`;
  }
}

export interface User {
  id: string;
  email: string;
  is_admin: boolean;
  is_service_account: boolean;
  created_at: string;
}

// ── Token helpers ─────────────────────────────────────────────────────────────

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// ── Auth headers ──────────────────────────────────────────────────────────────

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Auth API functions ────────────────────────────────────────────────────────

export async function login(
  email: string,
  password: string,
): Promise<{ token: string; user: User }> {
  const res = await fetch(`${AUTH_BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!res.ok) throw new Error(await parseErrorMessage(res, "Login failed"));
  return res.json();
}

export async function register(
  email: string,
  password: string,
): Promise<{ token: string; user: User }> {
  const res = await fetch(`${AUTH_BASE}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Registration failed"));
  return res.json();
}

export async function fetchMe(token: string): Promise<User> {
  const res = await fetch(`${AUTH_BASE}/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!res.ok) {
    throw new Error(`Unauthorized (${res.status})`);
  }

  return res.json();
}

// ── Password reset API functions ─────────────────────────────────────────────

export async function forgotPassword(email: string): Promise<void> {
  const res = await fetch(`${AUTH_BASE}/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Request failed"));
}

export async function resetPassword(
  token: string,
  newPassword: string,
): Promise<{ token: string; user: User }> {
  const res = await fetch(`${AUTH_BASE}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Reset failed"));
  return res.json();
}

// ── Admin API functions ───────────────────────────────────────────────────────

export async function listUsers(): Promise<User[]> {
  const res = await fetch(`${AUTH_BASE}/admin/users`, {
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Failed to list users"));
  return res.json();
}

export async function createUser(data: {
  email: string;
  password: string;
  is_admin: boolean;
  is_service_account: boolean;
}): Promise<User> {
  const res = await fetch(`${AUTH_BASE}/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Failed to create user"));
  return res.json();
}

export async function deleteUser(id: string): Promise<void> {
  const res = await fetch(`${AUTH_BASE}/admin/users/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });

  if (!res.ok) throw new Error(await parseErrorMessage(res, "Failed to delete user"));
}

export async function patchUser(
  id: string,
  data: { is_admin?: boolean; is_service_account?: boolean },
): Promise<User> {
  const res = await fetch(`${AUTH_BASE}/admin/users/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Failed to update user"));
  return res.json();
}
