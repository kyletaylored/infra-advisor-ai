import React, { useEffect } from "react";
import { Box } from "@chakra-ui/react";
import { Chat } from "./components/Chat";
import { initDatadogRum } from "./lib/datadog-rum";

export default function App() {
  useEffect(() => {
    initDatadogRum();
  }, []);

  return (
    <Box h="100vh" overflow="hidden">
      <Chat />
    </Box>
  );
}
