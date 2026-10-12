import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { applyTheme, initialTheme } from "./theme";
import "./styles.css";
import "./workspace-theme.css";

applyTheme(initialTheme());
try { document.documentElement.dataset.sidebarCollapsed = localStorage.getItem("review-agent-sidebar-v1") ?? "false"; }
catch { document.documentElement.dataset.sidebarCollapsed = "false"; }

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
