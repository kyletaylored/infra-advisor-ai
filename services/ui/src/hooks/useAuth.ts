import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { datadogRum } from "@datadog/browser-rum";
import {
  User,
  clearToken,
  fetchMe,
  getToken,
  login as authLogin,
  register as authRegister,
  setToken,
} from "../lib/auth";

// ── Context shape ─────────────────────────────────────────────────────────────

interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

// ── Context ───────────────────────────────────────────────────────────────────

export const AuthContext = createContext<AuthContextValue | null>(null);

// ── Provider ──────────────────────────────────────────────────────────────────

import React from "react";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setTokenState] = useState<string | null>(getToken);
  const [loading, setLoading] = useState<boolean>(true);

  // On mount: hydrate user from stored token
  useEffect(() => {
    const stored = getToken();
    if (!stored) {
      setLoading(false);
      return;
    }

    fetchMe(stored)
      .then((u) => {
        setUser(u);
        setTokenState(stored);
        datadogRum.setUser({ id: u.id, email: u.email, name: u.email });
      })
      .catch(() => {
        // Token invalid or expired — clear it
        clearToken();
        setTokenState(null);
        setUser(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await authLogin(email, password);
    setToken(result.token);
    setTokenState(result.token);
    setUser(result.user);
    datadogRum.setUser({
      id: result.user.id,
      email: result.user.email,
      name: result.user.email,
    });
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    const result = await authRegister(email, password);
    setToken(result.token);
    setTokenState(result.token);
    setUser(result.user);
    datadogRum.setUser({
      id: result.user.id,
      email: result.user.email,
      name: result.user.email,
    });
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setUser(null);
    datadogRum.clearUser();
  }, []);

  const value: AuthContextValue = { user, token, loading, login, register, logout };

  return React.createElement(AuthContext.Provider, { value }, children);
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}
