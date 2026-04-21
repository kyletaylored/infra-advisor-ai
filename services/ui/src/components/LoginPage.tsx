import React, { FormEvent, useEffect, useState } from "react";
import {
  Box,
  Button,
  Flex,
  Input,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";
import { forgotPassword, resetPassword } from "../lib/auth";
import { useAuth } from "../hooks/useAuth";

type Mode = "login" | "register" | "forgot" | "reset";

export function LoginPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [resetToken, setResetToken] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Detect ?reset_token= in URL and switch to reset mode
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("reset_token");
    if (token) {
      setResetToken(token);
      setMode("reset");
      // Remove token from URL without a page reload
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  function clearState() {
    setError(null);
    setSuccess(null);
    setPassword("");
    setNewPassword("");
    setConfirmPassword("");
  }

  function switchMode(next: Mode) {
    clearState();
    setMode(next);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    clearState();
    setSubmitting(true);

    try {
      if (mode === "login") {
        await login(email, password);
      } else if (mode === "register") {
        await register(email, password);
      } else if (mode === "forgot") {
        await forgotPassword(email);
        setSuccess("If that email is registered, a reset link has been sent. Check your inbox — or ask an admin to check the server logs if SMTP is not configured.");
      } else if (mode === "reset") {
        if (newPassword !== confirmPassword) {
          setError("Passwords do not match");
          return;
        }
        if (newPassword.length < 8) {
          setError("Password must be at least 8 characters");
          return;
        }
        const result = await resetPassword(resetToken!, newPassword);
        // Log the user in directly with the returned token
        const { setToken } = await import("../lib/auth");
        setToken(result.token);
        window.location.reload();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setSubmitting(false);
    }
  }

  const isLogin = mode === "login";

  return (
    <Flex h="100vh" bg="gray.50" align="center" justify="center" px={4}>
      <Box
        bg="white"
        borderWidth="1px"
        borderColor="gray.200"
        borderRadius="2xl"
        boxShadow="sm"
        p={8}
        w="full"
        maxW="sm"
      >
        {/* Logo + title */}
        <VStack gap={1} mb={8} textAlign="center">
          <img
            src="/favicon.svg"
            width={40}
            height={40}
            alt="InfraAdvisor AI"
            style={{ marginBottom: "8px" }}
          />
          <Text fontWeight="bold" fontSize="lg" color="gray.800">
            InfraAdvisor AI
          </Text>
          <Text fontSize="sm" color="gray.400">
            {mode === "forgot"
              ? "Reset your password"
              : mode === "reset"
              ? "Set a new password"
              : "Infrastructure advisory platform"}
          </Text>
        </VStack>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <VStack gap={4}>

            {/* Email — shown on login, register, forgot */}
            {mode !== "reset" && (
              <Box w="full">
                <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>
                  Email
                </Text>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@datadoghq.com"
                  required
                  disabled={submitting}
                  borderRadius="lg"
                  fontSize="sm"
                  borderColor="gray.300"
                  _focus={{ borderColor: "blue.500", boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)" }}
                />
              </Box>
            )}

            {/* Password — shown on login and register */}
            {(mode === "login" || mode === "register") && (
              <Box w="full">
                <Flex justify="space-between" align="center" mb={1}>
                  <Text fontSize="xs" fontWeight="medium" color="gray.600">
                    Password
                  </Text>
                  {mode === "login" && (
                    <button
                      type="button"
                      onClick={() => switchMode("forgot")}
                      style={{
                        background: "none",
                        border: "none",
                        padding: 0,
                        cursor: "pointer",
                        fontSize: "0.75rem",
                        color: "var(--chakra-colors-blue-500)",
                      }}
                    >
                      Forgot password?
                    </button>
                  )}
                </Flex>
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  disabled={submitting}
                  borderRadius="lg"
                  fontSize="sm"
                  borderColor="gray.300"
                  _focus={{ borderColor: "blue.500", boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)" }}
                />
              </Box>
            )}

            {/* New password fields — shown on reset */}
            {mode === "reset" && (
              <>
                <Box w="full">
                  <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>
                    New password
                  </Text>
                  <Input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="••••••••"
                    required
                    disabled={submitting}
                    borderRadius="lg"
                    fontSize="sm"
                    borderColor="gray.300"
                    _focus={{ borderColor: "blue.500", boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)" }}
                  />
                </Box>
                <Box w="full">
                  <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>
                    Confirm new password
                  </Text>
                  <Input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="••••••••"
                    required
                    disabled={submitting}
                    borderRadius="lg"
                    fontSize="sm"
                    borderColor="gray.300"
                    _focus={{ borderColor: "blue.500", boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)" }}
                  />
                </Box>
              </>
            )}

            {/* Error / success feedback */}
            {error && (
              <Text fontSize="sm" color="red.500" w="full">
                {error}
              </Text>
            )}
            {success && (
              <Text fontSize="sm" color="green.600" w="full">
                {success}
              </Text>
            )}

            {/* Domain hint for register */}
            {mode === "register" && (
              <Text fontSize="xs" color="gray.400" w="full">
                Self-registration is limited to @datadoghq.com email addresses.
                Contact an admin to provision other accounts.
              </Text>
            )}

            <Button
              type="submit"
              colorPalette="blue"
              w="full"
              borderRadius="lg"
              disabled={submitting || (mode === "forgot" && !!success)}
              fontSize="sm"
            >
              {submitting ? (
                <Spinner size="xs" />
              ) : mode === "login" ? (
                "Sign in"
              ) : mode === "register" ? (
                "Create account"
              ) : mode === "forgot" ? (
                "Send reset link"
              ) : (
                "Set new password"
              )}
            </Button>
          </VStack>
        </form>

        {/* Footer links */}
        <Flex justify="center" mt={5} gap={1} wrap="wrap">
          {(mode === "login" || mode === "register") && (
            <>
              <Text fontSize="sm" color="gray.400">
                {isLogin ? "Don't have an account?" : "Already have an account?"}
              </Text>
              <button
                type="button"
                onClick={() => switchMode(isLogin ? "register" : "login")}
                style={{
                  background: "none",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  fontSize: "0.875rem",
                  color: "var(--chakra-colors-blue-600)",
                  fontWeight: 500,
                }}
              >
                {isLogin ? "Create account" : "Sign in"}
              </button>
            </>
          )}

          {(mode === "forgot" || mode === "reset") && (
            <button
              type="button"
              onClick={() => switchMode("login")}
              style={{
                background: "none",
                border: "none",
                padding: 0,
                cursor: "pointer",
                fontSize: "0.875rem",
                color: "var(--chakra-colors-blue-600)",
                fontWeight: 500,
              }}
            >
              ← Back to sign in
            </button>
          )}
        </Flex>
      </Box>
    </Flex>
  );
}
