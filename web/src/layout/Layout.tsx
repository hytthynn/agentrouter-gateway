/** Layout: фиксированный сайдбар 220px + контент (§12.2), индикатор апстрима */

import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "@/api/client";
import type { Health } from "@/api/types";

const NAV = [
  { to: "/", label: "Дашборд", icon: "◈" },
  { to: "/accounts", label: "Аккаунты", icon: "👤" },
  { to: "/provider-keys", label: "Ключи провайдера", icon: "⚿" },
  { to: "/models", label: "Модели", icon: "⌘" },
  { to: "/gateway-keys", label: "Ключи шлюза", icon: "🖧" },
  { to: "/logs", label: "Логи", icon: "☰" },
  { to: "/settings", label: "Настройки", icon: "⚙" },
];

export function Layout() {
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/api/health"),
    refetchInterval: 60_000,
  });

  return (
    <div className="flex h-full">
      <aside className="fixed inset-y-0 left-0 z-40 flex w-[220px] flex-col border-r border-border bg-surface">
        <div className="flex items-center gap-2 px-4 py-4">
          <span className="text-lg text-accent">◈</span>
          <span className="text-[13px] font-semibold leading-tight">
            AgentRouter
            <br />
            Gateway
          </span>
        </div>
        <nav className="mt-1 flex flex-col gap-0.5 px-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition-colors ${
                  isActive
                    ? "bg-accent-dim/40 font-medium text-accent"
                    : "text-text-dim hover:bg-elevated hover:text-text"
                }`
              }
            >
              <span className="w-4 text-center text-[12px] opacity-80">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto px-4 py-4 text-xs">
          <div className="flex items-center gap-2">
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                health?.upstream_reachable ? "bg-accent shadow-[0_0_6px_rgba(163,230,53,0.6)]" : "bg-danger"
              }`}
            />
            <span className="text-text-dim">
              agentrouter.org{" "}
              {health?.upstream_reachable ? (
                <span className="text-text-muted">доступен</span>
              ) : (
                <span className="text-danger">недоступен</span>
              )}
            </span>
          </div>
          {health?.provider_version && (
            <div className="mono mt-1 text-[10px] text-text-muted">v {health.provider_version}</div>
          )}
          {health && (
            <div className="mono mt-0.5 text-[10px] text-text-muted">agw {health.version}</div>
          )}
        </div>
      </aside>

      <main className="ml-[220px] flex min-h-full flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}

export function PageHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <header className="flex items-center justify-between gap-3 border-b border-border px-6 py-4">
      <h1 className="text-lg font-semibold">{title}</h1>
      <div className="flex items-center gap-2">{children}</div>
    </header>
  );
}
