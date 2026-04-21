import { driver } from "driver.js";
import "driver.js/dist/driver.css";

const TOUR_KEY = "infra_advisor_tour_seen";

export function hasSeenTour(): boolean {
  return localStorage.getItem(TOUR_KEY) === "true";
}

export function markTourSeen(): void {
  localStorage.setItem(TOUR_KEY, "true");
}

export function resetTour(): void {
  localStorage.removeItem(TOUR_KEY);
}

export function startTour(): void {
  const driverObj = driver({
    showProgress: true,
    animate: true,
    allowClose: true,
    overlayColor: "rgba(15, 23, 42, 0.6)",
    stagePadding: 6,
    popoverClass: "infra-advisor-tour",
    onDestroyStarted: () => {
      markTourSeen();
      driverObj.destroy();
    },
    steps: [
      {
        popover: {
          title: "Welcome to InfraAdvisor AI",
          description:
            "Your AI-powered infrastructure advisory platform — backed by live government data from FHWA, FEMA, EIA, and the Texas Water Development Board. This tour takes about 60 seconds.",
          side: "over",
          align: "center",
        },
      },
      {
        element: "[data-tour='domain-tiles']",
        popover: {
          title: "Explore by domain",
          description:
            "Click any tile to instantly run a starter query for that infrastructure domain. Each tile is pre-loaded with a real query against live government data.",
          side: "top",
          align: "center",
        },
      },
      {
        element: "[data-testid='chat-input']",
        popover: {
          title: "Ask anything",
          description:
            "Type a natural-language question — the AI agent selects the right data tools, fetches live records, and synthesises a response. Press Enter to send.",
          side: "top",
          align: "start",
        },
      },
      {
        element: "[data-tour='recommendations']",
        popover: {
          title: "Smart follow-ups",
          description:
            "After each answer, context-aware follow-up suggestions appear here — first from the domain, then upgraded to LLM-generated ones. Click any pill to instantly run it.",
          side: "top",
          align: "start",
        },
      },
      {
        element: "[data-tour='citation-sidebar']",
        popover: {
          title: "Sources & citations",
          description:
            "Every answer is grounded in live data. Sources and document citations appear here — expand any entry to see the document type and relevance score.",
          side: "left",
          align: "center",
        },
      },
      {
        element: "[data-tour='sandbox-tab']",
        popover: {
          title: "API Sandbox",
          description:
            "Developers and power users can explore the raw MCP tool APIs here — run any of the 8 data tools directly, edit parameters, and inspect the JSON response.",
          side: "bottom",
          align: "center",
        },
      },
      {
        element: "[data-tour='tour-button']",
        popover: {
          title: "Retake this tour anytime",
          description:
            "Click this button in the header whenever you want to run through the tour again. You can also find it next to your account controls.",
          side: "bottom",
          align: "end",
        },
      },
    ],
  });

  driverObj.drive();
}
