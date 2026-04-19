import React, { useEffect } from "react";
import { Chat } from "./components/Chat";
import { initDatadogRum } from "./lib/datadog-rum";

export default function App() {
  useEffect(() => {
    initDatadogRum();
  }, []);

  return <Chat />;
}
