export const AUTH_BASE = "/auth";
const TOKEN_KEY = "infra_advisor_token";

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

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Login failed (${res.status})`);
  }

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

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Registration failed (${res.status})`);
  }

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

// ── Admin API functions ───────────────────────────────────────────────────────

export async function listUsers(): Promise<User[]> {
  const res = await fetch(`${AUTH_BASE}/admin/users`, {
    headers: { ...authHeaders() },
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Failed to list users (${res.status})`);
  }

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

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Failed to create user (${res.status})`);
  }

  return res.json();
}

export async function deleteUser(id: string): Promise<void> {
  const res = await fetch(`${AUTH_BASE}/admin/users/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Failed to delete user (${res.status})`);
  }
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

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Failed to update user (${res.status})`);
  }

  return res.json();
}
