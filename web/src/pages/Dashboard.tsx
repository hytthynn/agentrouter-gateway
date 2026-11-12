/** Дашборд (FR-9): карточки-метрики, спарклайн 24ч, статусы аккаунтов, лента логов */

import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import type { Dashboard } from "@/api/types";
import { Badge, Card, Spinner } from "@/components/ui";
import { fmtDateTime, fmtNum, fmtQuota, fmtTokens, fmtUsd } from "@/lib/format";

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<Dashboard>("/api/dashboard"),
    refetchInterval: 30_000,
  });

  if (isLoading) return <Spinner />;
  if (error || !data) return <div className="p-6 text-danger">Не удалось загрузить дашборд</div>;

  return (
    <>
      <header className="border-b border-border px-6 py-4">
        <h1 className="text-lg font-semibold">Дашборд</h1>
      </header>
      <div className="space-y-5 p-6">
        {/* Метрики */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
          <Metric
            label="Баланс пула"
            value={fmtUsd(data.total_balance_usd)}
            sub={`${fmtQuota(data.total_balance_quota)} quota`}
            accent
          />
          <Metric
            label="Аккаунты"
            value={`${data.accounts_active}/${data.accounts_total}`}
            sub="активные / всего"
          />
          <Metric label="Запросы 24ч" value={fmtNum(data.requests_24h)} sub="за сутки" />
          <Metric label="Токены 24ч" value={fmtTokens(data.tokens_24h)} sub="за сутки" />
          <Metric
            label="Ошибки 24ч"
            value={fmtNum(data.errors_24h)}
            sub="ошибки + обрывы"
            danger={data.errors_24h > 0}
          />
          <Metric label="Ближайший чек-ин" value={nextCheckinTime(data.next_checkin)} sub="локальное время" />
        </div>

        {/* Спарклайн + статусы */}
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2">
            <div className="mb-2 text-sm font-medium text-text-dim">Запросы и токены по часам (24 ч)</div>
            <div className="h-44">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data.sparkline} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
                  <defs>
                    <linearGradient id="gReq" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#a3e635" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#a3e635" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="hour"
                    tickFormatter={(h: string) => new Date(h).getHours().toString().padStart(2, "0") + ":00"}
                    stroke="#66705f"
                    fontSize={10}
                    tickLine={false}
                    axisLine={{ stroke: "#242b23" }}
                    interval={3}
                  />
                  <YAxis stroke="#66705f" fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{
                      background: "#171c17",
                      border: "1px solid #242b23",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    labelFormatter={(h) => new Date(String(h)).toLocaleString("ru-RU")}
                    formatter={(value, name) =>
                      name === "requests"
                        ? [fmtNum(Number(value)), "запросы"]
                        : [fmtTokens(Number(value)), "токены"]
                    }
                  />
                  <Area
                    type="monotone"
                    dataKey="requests"
                    stroke="#a3e635"
                    strokeWidth={1.5}
                    fill="url(#gReq)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-text-dim">Статус аккаунтов</span>
              <Link to="/accounts" className="text-xs text-accent hover:underline">
                все →
              </Link>
            </div>
            {data.accounts.length === 0 ? (
              <div className="py-8 text-center text-xs text-text-muted">
                Нет аккаунтов.
                <br />
                <Link to="/accounts" className="text-accent hover:underline">
                  Добавить первый →
                </Link>
              </div>
            ) : (
              <div className="space-y-1.5">
                {data.accounts.slice(0, 8).map((a) => (
                  <div key={a.id} className="flex items-center justify-between text-[13px]">
                    <span className="truncate">{a.name}</span>
                    <span className="flex items-center gap-2">
                      <span className={`mono ${a.balance_usd !== null ? "text-accent" : "text-text-muted"}`}>
                        {fmtUsd(a.balance_usd)}
                      </span>
                      <Badge status={a.status} />
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* Лента логов */}
        <Card>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium text-text-dim">Последние события</span>
            <Link to="/logs" className="text-xs text-accent hover:underline">
              все логи →
            </Link>
          </div>
          {data.recent_logs.length === 0 ? (
            <div className="py-6 text-center text-xs text-text-muted">Событий пока нет</div>
          ) : (
            <div className="space-y-1">
              {data.recent_logs.map((l) => (
                <div key={l.id} className="flex items-start gap-3 text-[13px]">
                  <span className="mono shrink-0 text-text-muted">{fmtDateTime(l.ts)}</span>
                  <span
                    className={`shrink-0 ${
                      l.level === "error" ? "text-danger" : l.level === "warn" ? "text-warn" : "text-accent"
                    }`}
                  >
                    {l.category}
                  </span>
                  <span className="text-text-dim">{l.message}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function Metric({
  label,
  value,
  sub,
  accent,
  danger,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
  danger?: boolean;
}) {
  return (
    <Card>
      <div className="text-[11px] uppercase tracking-wide text-text-muted">{label}</div>
      <div
        className={`mt-1 text-2xl font-semibold ${
          danger ? "text-danger" : accent ? "text-accent" : "text-text"
        }`}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-text-muted">{sub}</div>}
    </Card>
  );
}

function nextCheckinTime(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const diff = (d.getTime() - Date.now()) / 60000;
  if (diff > 0 && diff < 60) return `через ${Math.ceil(diff)} мин`;
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
