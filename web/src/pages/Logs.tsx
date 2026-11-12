/** Логи (FR-7): вкладки Запросы/Системные, фильтры-чипсы, раскрытие деталей (attempts/JSON) */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import type { RequestLog, SystemLog } from "@/api/types";
import { Badge, Button, EmptyState, Spinner } from "@/components/ui";
import { fmtDateTime, fmtMs, fmtNum, fmtTokens, fmtUsd } from "@/lib/format";

const PAGE_SIZE = 50;

export default function LogsPage() {
  const [tab, setTab] = useState<"requests" | "system">("requests");

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <h1 className="text-lg font-semibold">Логи</h1>
        <div className="flex gap-1.5">
          {(["requests", "system"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                tab === t ? "bg-accent-dim/40 text-accent" : "text-text-dim hover:bg-elevated"
              }`}
            >
              {t === "requests" ? "Запросы" : "Системные"}
            </button>
          ))}
        </div>
      </header>
      <div className="p-6">{tab === "requests" ? <RequestLogs /> : <SystemLogs />}</div>
    </>
  );
}

// ------------------------------------------------------------- запросы

function RequestLogs() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [model, setModel] = useState("");
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["request-logs", page, status, model, search],
    queryFn: () =>
      api<{ items: RequestLog[]; total: number; page: number; page_size: number }>(
        "/api/logs/requests",
        { params: { page, page_size: PAGE_SIZE, status, model, search } },
      ),
    refetchInterval: 15_000,
  });

  const statuses = ["", "success", "error", "client_abort"];
  const statusLabels: Record<string, string> = {
    "": "все",
    success: "успех",
    error: "ошибки",
    client_abort: "обрывы",
  };

  return (
    <div>
      {/* Фильтры-чипсы */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {statuses.map((s) => (
          <button
            key={s}
            onClick={() => {
              setStatus(s);
              setPage(1);
            }}
            className={`rounded-full border px-3 py-1 text-xs transition-colors ${
              status === s
                ? "border-accent/50 bg-accent-dim/30 text-accent"
                : "border-border text-text-dim hover:border-text-muted"
            }`}
          >
            {statusLabels[s]}
          </button>
        ))}
        <input
          className="input ml-auto w-48"
          placeholder="Модель…"
          value={model}
          onChange={(e) => {
            setModel(e.target.value);
            setPage(1);
          }}
        />
        <input
          className="input w-56"
          placeholder="Поиск по ошибке…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
      </div>

      {isLoading ? (
        <Spinner />
      ) : !data || data.items.length === 0 ? (
        <EmptyState icon="☰" title="Запросов пока нет" hint="Подключите IDE ключом шлюза — здесь появятся все запросы с токенами, стоимостью и латентностью." />
      ) : (
        <>
          <div className="card overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Модель</th>
                  <th>Ключ</th>
                  <th>Аккаунт</th>
                  <th>Токены</th>
                  <th>Стоимость</th>
                  <th>TTFT</th>
                  <th>Всего</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <LogRow key={r.id} row={r} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center justify-between text-xs text-text-muted">
            <span>
              всего {fmtNum(data.total)} · страница {data.page} из {Math.max(1, Math.ceil(data.total / data.page_size))}
            </span>
            <div className="flex gap-2">
              <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                ← назад
              </Button>
              <Button size="sm" disabled={page >= Math.ceil(data.total / data.page_size)} onClick={() => setPage(page + 1)}>
                вперёд →
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function LogRow({ row }: { row: RequestLog }) {
  const [open, setOpen] = useState(false);
  const hasAttempts = row.attempts && row.attempts.length > 0;
  return (
    <>
      <tr className="cursor-pointer" onClick={() => hasAttempts && setOpen(!open)}>
        <td className="mono whitespace-nowrap text-text-muted">{fmtDateTime(row.ts)}</td>
        <td className="mono">
          {row.model}
          {row.stream && <span className="ml-1 text-[10px] text-text-muted">SSE</span>}
        </td>
        <td className="text-text-dim">{row.gateway_key_id ? `#${row.gateway_key_id}` : "—"}</td>
        <td className="text-text-dim">{row.account_id ? `#${row.account_id}` : "—"}</td>
        <td className="mono">
          {fmtTokens(row.total_tokens)}
          {row.estimated && <span className="ml-1 text-[10px] text-text-muted">~</span>}
        </td>
        <td className="mono">{row.cost_usd !== null ? fmtUsd(row.cost_usd) : "—"}</td>
        <td className="mono">{fmtMs(row.first_token_ms)}</td>
        <td className="mono">{fmtMs(row.duration_ms)}</td>
        <td>
          <Badge status={row.status} />
          {row.http_status && <span className="ml-1 text-[10px] text-text-muted">{row.http_status}</span>}
        </td>
      </tr>
      {open && hasAttempts && (
        <tr>
          <td colSpan={9} className="bg-[#0d100d]">
            <div className="mono p-2 text-[12px] leading-6">
              <span className="text-text-muted">trace failover:</span>
              {row.attempts!.map((a, i) => (
                <div key={i}>
                  → аккаунт #{a.account_id}: {a.status} <span className="text-warn">{a.code}</span>
                </div>
              ))}
              {row.error_message && <div className="mt-1 text-danger">{row.error_message}</div>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ------------------------------------------------------------- системные

const CATEGORIES = ["", "oauth", "checkin", "balance", "sync", "gateway", "scheduler", "security"];
const CATEGORY_LABELS: Record<string, string> = {
  "": "все",
  oauth: "oauth",
  checkin: "чек-ин",
  balance: "баланс",
  sync: "синк",
  gateway: "шлюз",
  scheduler: "планировщик",
  security: "безопасность",
};

function SystemLogs() {
  const [page, setPage] = useState(1);
  const [level, setLevel] = useState("");
  const [category, setCategory] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["system-logs", page, level, category],
    queryFn: () =>
      api<{ items: SystemLog[]; total: number; page: number; page_size: number }>(
        "/api/logs/system",
        { params: { page, page_size: PAGE_SIZE, level, category } },
      ),
    refetchInterval: 15_000,
  });

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {(["", "info", "warn", "error"] as const).map((l) => (
          <button
            key={l}
            onClick={() => {
              setLevel(l);
              setPage(1);
            }}
            className={`rounded-full border px-3 py-1 text-xs transition-colors ${
              level === l
                ? "border-accent/50 bg-accent-dim/30 text-accent"
                : "border-border text-text-dim hover:border-text-muted"
            }`}
          >
            {l === "" ? "все уровни" : l}
          </button>
        ))}
        <span className="mx-1 text-border">|</span>
        {CATEGORIES.map((c) => (
          <button
            key={c}
            onClick={() => {
              setCategory(c);
              setPage(1);
            }}
            className={`rounded-full border px-3 py-1 text-xs transition-colors ${
              category === c
                ? "border-accent/50 bg-accent-dim/30 text-accent"
                : "border-border text-text-dim hover:border-text-muted"
            }`}
          >
            {CATEGORY_LABELS[c]}
          </button>
        ))}
      </div>

      {isLoading ? (
        <Spinner />
      ) : !data || data.items.length === 0 ? (
        <EmptyState icon="☰" title="Событий нет" />
      ) : (
        <>
          <div className="card overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Уровень</th>
                  <th>Категория</th>
                  <th>Аккаунт</th>
                  <th>Сообщение</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((l) => (
                  <SystemLogRow key={l.id} row={l} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center justify-between text-xs text-text-muted">
            <span>
              всего {fmtNum(data.total)} · страница {data.page}
            </span>
            <div className="flex gap-2">
              <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                ← назад
              </Button>
              <Button size="sm" disabled={page >= Math.ceil(data.total / data.page_size)} onClick={() => setPage(page + 1)}>
                вперёд →
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SystemLogRow({ row }: { row: SystemLog }) {
  const [open, setOpen] = useState(false);
  const levelColor = row.level === "error" ? "text-danger" : row.level === "warn" ? "text-warn" : "text-accent";
  return (
    <>
      <tr className="cursor-pointer" onClick={() => row.details && setOpen(!open)}>
        <td className="mono whitespace-nowrap text-text-muted">{fmtDateTime(row.ts)}</td>
        <td className={`text-xs font-semibold uppercase ${levelColor}`}>{row.level}</td>
        <td className="text-text-dim">{row.category}</td>
        <td className="text-text-dim">{row.account_id ? `#${row.account_id}` : "—"}</td>
        <td>
          {row.message}
          {row.details && (
            <span className="ml-2 text-[10px] text-text-muted">{open ? "▾" : "▸"} details</span>
          )}
        </td>
      </tr>
      {open && row.details && (
        <tr>
          <td colSpan={5} className="bg-[#0d100d]">
            <pre className="mono max-h-48 overflow-auto p-2 text-[12px] leading-5 text-text-dim">
              {JSON.stringify(row.details, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}
