import React, { useEffect } from "react";
import { Box, Flex, Spinner } from "@chakra-ui/react";
import { Chat } from "./components/Chat";
import { LoginPage } from "./components/LoginPage";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { initDatadogRum } from "./lib/datadog-rum";

function AppInner() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <Flex h="100vh" align="center" justify="center" bg="gray.50">
        <Spinner size="lg" color="blue.500" />
      </Flex>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  return (
    <Box h="100vh" overflow="hidden">
      <Chat />
    </Box>
  );
}

export default function App() {
  useEffect(() => {
    initDatadogRum();
  }, []);

  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}
