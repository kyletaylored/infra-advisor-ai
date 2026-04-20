import React, { FormEvent, useState } from "react";
import {
  Box,
  Button,
  Flex,
  Input,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";
import { useAuth } from "../hooks/useAuth";

export function LoginPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setSubmitting(false);
    }
  }

  function toggleMode() {
    setMode((m) => (m === "login" ? "register" : "login"));
    setError(null);
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
            Infrastructure advisory platform
          </Text>
        </VStack>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <VStack gap={4}>
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
                _focus={{
                  borderColor: "blue.500",
                  boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)",
                }}
              />
            </Box>

            <Box w="full">
              <Text fontSize="xs" fontWeight="medium" color="gray.600" mb={1}>
                Password
              </Text>
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
                _focus={{
                  borderColor: "blue.500",
                  boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)",
                }}
              />
            </Box>

            {/* Error display */}
            {error && (
              <Text fontSize="sm" color="red.500" w="full">
                {error}
              </Text>
            )}

            {/* Register hint */}
            {!isLogin && (
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
              disabled={submitting}
              fontSize="sm"
            >
              {submitting ? (
                <Spinner size="xs" />
              ) : isLogin ? (
                "Sign in"
              ) : (
                "Create account"
              )}
            </Button>
          </VStack>
        </form>

        {/* Toggle mode */}
        <Flex justify="center" mt={5} gap={1}>
          <Text fontSize="sm" color="gray.400">
            {isLogin ? "Don't have an account?" : "Already have an account?"}
          </Text>
          <button
            type="button"
            onClick={toggleMode}
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
        </Flex>
      </Box>
    </Flex>
  );
}
