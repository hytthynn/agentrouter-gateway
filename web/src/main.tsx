import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./layout/Layout";
import { ToastProvider } from "./components/toast";
import AccountsPage from "./pages/Accounts";
import DashboardPage from "./pages/Dashboard";
import GatewayKeysPage from "./pages/GatewayKeys";
import LogsPage from "./pages/Logs";
import ModelsPage from "./pages/Models";
import ProviderKeysPage from "./pages/ProviderKeys";
import SettingsPage from "./pages/Settings";
import "./styles/theme.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<DashboardPage />} />
              <Route path="/accounts" element={<AccountsPage />} />
              <Route path="/provider-keys" element={<ProviderKeysPage />} />
              <Route path="/models" element={<ModelsPage />} />
              <Route path="/gateway-keys" element={<GatewayKeysPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<div className="p-8 text-text-muted">Страница не найдена</div>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
