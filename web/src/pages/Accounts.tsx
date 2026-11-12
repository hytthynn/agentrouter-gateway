/** Аккаунты (FR-1): сетка карточек + модалка добавления (2 шага: форма → SSE-прогресс §9.5) */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import type { Account, TaskSnapshot } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, EmptyState, Field, Modal, Spinner, Toggle } from "@/components/ui";
import { fmtQuota, fmtRelative, fmtUsd } from "@/lib/format";
import { subscribeTask } from "@/lib/sse";
import { DiagnoseModal } from "@/components/DiagnoseModal";

export default function AccountsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: accounts, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api<Account[]>("/api/accounts"),
    refetchInterval: 20_000,
  });

  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);
  const [diagnoseTarget, setDiagnoseTarget] = useState<Account | null>(null);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["accounts"] });
    void qc.invalidateQueries({ queryKey: ["dashboard"] });
    void qc.invalidateQueries({ queryKey: ["models"] });
    void qc.invalidateQueries({ queryKey: ["provider-keys"] });
  };

  const act = useMutation({
    mutationFn: ({ id, action }: { id: number; action: string }) =>
      api<{ status?: string; message?: string }>(`/api/accounts/${id}/${action}`, {
        method: "POST",
      }),
    onSuccess: (data, vars) => {
      invalidate();
      if (vars.action === "checkin") {
        if (data.status === "already") toast("Чек-ин уже выполнен сегодня", "info");
        else if (data.status === "success") toast("Чек-ин успешен — баланс обновлён", "success");
        else if (data.status === "unavailable") toast(data.message ?? "Чек-ин недоступен у провайдера", "info");
        else if (data.status === "auth_error") toast("Токен отклонён — аккаунт помечен «нужен перелогин»", "error");
        else toast(`Чек-ин: ${data.status ?? "неизвестный статус"}`, "error");
      } else {
        toast("Готово", "success");
      }
    },
    onError: (e) => toast(e.message, "error"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/api/accounts/${id}`, { method: "PATCH", body: { enabled } }),
    onSuccess: invalidate,
    onError: (e) => toast(e.message, "error"),
  });

  const del = useMutation({
    mutationFn: ({ id, purge }: { id: number; purge: boolean }) =>
      api<{ provider_keys_deleted: number; errors: string[] }>(
        `/api/accounts/${id}?purge_provider_keys=${purge}`,
        { method: "DELETE" },
      ),
    onSuccess: (data) => {
      invalidate();
      toast(
        `Аккаунт удалён${data.provider_keys_deleted ? `, ключей у провайдера: ${data.provider_keys_deleted}` : ""}`,
        "success",
      );
    },
    onError: (e) => toast(e.message, "error"),
  });

  if (isLoading) return <Spinner />;

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <h1 className="text-lg font-semibold">Аккаунты AgentRouter</h1>
        <Button variant="primary" onClick={() => setAddOpen(true)}>
          + Добавить аккаунт
        </Button>
      </header>

      <div className="p-6">
        {!accounts || accounts.length === 0 ? (
          <EmptyState
            icon="👤"
            title="Аккаунтов пока нет"
            hint="Добавьте аккаунт по GitHub-креденшалам (логин, пароль, TOTP-секрет) и system access token. Автоматизация сама выполнит OAuth-вход и выпустит безлимитный ключ."
            action={
              <Button variant="primary" onClick={() => setAddOpen(true)}>
                + Добавить аккаунт
              </Button>
            }
          />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {accounts.map((a) => (
              <AccountCard
                key={a.id}
                account={a}
                onCheckin={() => act.mutate({ id: a.id, action: "checkin" })}
                onRefresh={() => act.mutate({ id: a.id, action: "refresh" })}
                onRelogin={() => act.mutate({ id: a.id, action: "relogin" })}
                onDiagnose={() => setDiagnoseTarget(a)}
                onToggle={(enabled) => toggle.mutate({ id: a.id, enabled })}
                onDelete={() => setDeleteTarget(a)}
              />
            ))}
          </div>
        )}
      </div>

      <AddAccountModal open={addOpen} onClose={() => setAddOpen(false)} onAdded={invalidate} />

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && del.mutate({ id: deleteTarget.id, purge: false })}
        title="Удалить аккаунт?"
        typeName={deleteTarget?.name}
      >
        Локальные данные (ключи, логи, сессия) будут удалены каскадно. Ключи у провайдера останутся —
        при желании удалите их вручную на странице «Ключи провайдера».
      </ConfirmDialog>

      {diagnoseTarget && (
        <DiagnoseModal account={diagnoseTarget} onClose={() => setDiagnoseTarget(null)} />
      )}
    </>
  );
}

function AccountCard({
  account,
  onCheckin,
  onRefresh,
  onRelogin,
  onDiagnose,
  onToggle,
  onDelete,
}: {
  account: Account;
  onCheckin: () => void;
  onRefresh: () => void;
  onRelogin: () => void;
  onDiagnose: () => void;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
}) {
  const probation = (account.group_name || "default") === "probation";
  const highlighted = account.status === "needs_relogin";
  return (
    <Card className={highlighted ? "border-warn/60" : ""}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold">{account.name}</div>
          <div className="mono truncate text-xs text-text-muted">
            {account.github_login} · id {account.provider_user_id ?? "—"}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Badge status={account.status} />
          {probation && <Badge status="warn">probation ×2</Badge>}
        </div>
      </div>

      <div className="mt-3 flex items-end justify-between">
        <div>
          <div className="text-2xl font-semibold text-accent">{fmtUsd(account.balance_usd)}</div>
          <div className="text-[11px] text-text-muted">
            {fmtQuota(account.balance_quota)} quota · потрачено {fmtUsd(account.used_quota !== null ? account.used_quota / 500000 : null)}
          </div>
        </div>
        <div className="text-right text-[11px] text-text-muted">
          <div>чек-ин: {fmtRelative(account.last_checkin_at)}</div>
          {account.last_checkin_reward_usd !== null && (
            <div className="text-accent">+{fmtUsd(account.last_checkin_reward_usd, true)}</div>
          )}
        </div>
      </div>

      {account.error_message && (
        <div className="mt-2 rounded-md bg-[#2a1414]/60 px-2.5 py-1.5 text-xs text-danger">
          {account.error_message}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
        <div className="flex gap-1.5">
          <Button size="sm" onClick={onCheckin} title="Чек-ин ($25/день)">
            ✓ чек-ин
          </Button>
          <Button size="sm" onClick={onRefresh} title="Обновить баланс">
            ⟳ баланс
          </Button>
          <Button
            size="sm"
            onClick={onRelogin}
            title="Перелогин GitHub OAuth"
            className={highlighted ? "border-warn/60 text-warn" : ""}
          >
            ↻ перелогин
          </Button>
          <Button size="sm" onClick={onDiagnose} title="Диагностика подключения">
            ⚡
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Toggle
            checked={account.status !== "disabled"}
            onChange={(v) => onToggle(v)}
            disabled={account.status === "needs_relogin"}
          />
          <Button size="sm" variant="ghost" onClick={onDelete} title="Удалить">
            🗑
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ------------------------------------------------- добавление аккаунта

function AddAccountModal({
  open,
  onClose,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: () => void;
}) {
  const { toast } = useToast();
  const [step, setStep] = useState<"form" | "progress">("form");
  const [form, setForm] = useState({
    name: "",
    github_login: "",
    github_password: "",
    totp_secret: "",
    system_token: "",
    base_url: "",
  });
  const [taskId, setTaskId] = useState<string | null>(null);
  const [snap, setSnap] = useState<TaskSnapshot | null>(null);

  const reset = () => {
    setStep("form");
    setTaskId(null);
    setSnap(null);
  };
  const close = () => {
    reset();
    onClose();
  };

  useEffect(() => {
    if (!taskId) return;
    const unsub = subscribeTask(
      taskId,
      (_ev, snapshot) => setSnap({ ...snapshot }),
      (state, error, result) => {
        if (state === "success") {
          onAdded();
          toast(`Аккаунт добавлен (id ${result?.account_id ?? ""})`, "success");
          setTimeout(close, 1200);
        } else if (state === "error") {
          toast(error ?? "Автоматизация не удалась", "error");
        }
      },
    );
    return unsub;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  const submit = async () => {
    try {
      const body: Record<string, string> = {
        github_login: form.github_login.trim(),
        github_password: form.github_password,
        totp_secret: form.totp_secret.replace(/\s+/g, "").toUpperCase(),
        system_token: form.system_token.trim(),
      };
      if (form.name.trim()) body.name = form.name.trim();
      if (form.base_url.trim()) body.base_url = form.base_url.trim();
      const data = await api<{ task_id: string }>("/api/accounts", { method: "POST", body });
      setTaskId(data.task_id);
      setStep("progress");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const running = snap?.state === "running" || snap?.state === "waiting_user_code";
  const codeNeeded = snap?.state === "waiting_user_code";
  const [code, setCode] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [snap?.logs.length]);

  return (
    <Modal open={open} onClose={close} title="Добавить аккаунт AgentRouter" wide>
      {step === "form" ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <div className="grid grid-cols-2 gap-x-4">
            <Field label="GitHub-логин">
              <input
                className="input"
                required
                value={form.github_login}
                onChange={(e) => setForm({ ...form, github_login: e.target.value })}
                placeholder="username"
              />
            </Field>
            <Field label="Пароль GitHub">
              <input
                className="input"
                type="password"
                required
                value={form.github_password}
                onChange={(e) => setForm({ ...form, github_password: e.target.value })}
              />
            </Field>
            <Field label="TOTP-секрет (base32)" hint="Настройки GitHub → Two-factor authentication">
              <input
                className="input mono"
                required
                value={form.totp_secret}
                onChange={(e) => setForm({ ...form, totp_secret: e.target.value })}
                placeholder="JBSWY3DPEHPK3PXP"
              />
            </Field>
            <Field label="System access token AgentRouter" hint="Консоль → Настройки → Системный токен доступа">
              <input
                className="input mono"
                required
                value={form.system_token}
                onChange={(e) => setForm({ ...form, system_token: e.target.value })}
              />
            </Field>
            <Field label="Имя (необязательно)">
              <input
                className="input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="по умолчанию — GitHub-логин"
              />
            </Field>
            <Field label="Base URL (необязательно)">
              <input
                className="input mono"
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://agentrouter.org"
              />
            </Field>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" variant="primary">
              Добавить → OAuth-вход
            </Button>
          </div>
        </form>
      ) : (
        <div>
          {/* Шаги */}
          <div className="mb-3 space-y-1.5">
            {(snap?.steps ?? []).map((s) => (
              <div key={s.key} className="flex items-center gap-2 text-[13px]">
                <span
                  className={
                    s.status === "done"
                      ? "text-accent"
                      : s.status === "error"
                        ? "text-danger"
                        : s.status === "running"
                          ? "text-warn"
                          : "text-text-muted"
                  }
                >
                  {s.status === "done" ? "✓" : s.status === "error" ? "✕" : s.status === "running" ? "◐" : "○"}
                </span>
                <span className={s.status === "pending" ? "text-text-muted" : ""}>{s.title}</span>
              </div>
            ))}
          </div>

          {/* Device verification (§9.5) */}
          {codeNeeded && (
            <div className="mb-3 rounded-lg border border-warn/50 bg-[#2a2410]/40 p-3">
              <div className="mb-2 text-sm text-warn">GitHub запрашивает код из письма (device verification)</div>
              <div className="flex gap-2">
                <input
                  className="input mono"
                  placeholder="123456"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  autoFocus
                />
                <Button
                  variant="primary"
                  onClick={() => {
                    if (taskId && code.trim()) {
                      void api(`/api/tasks/${taskId}/code`, { method: "POST", body: { code: code.trim() } });
                      setCode("");
                    }
                  }}
                >
                  Отправить
                </Button>
              </div>
            </div>
          )}

          {/* Live-лог (§12.4) */}
          <div
            ref={logRef}
            className="mono h-56 overflow-y-auto rounded-lg border border-border bg-[#0a0d0a] p-3 text-[12px] leading-5"
          >
            {(snap?.logs ?? []).map((line, i) => (
              <div key={i} className={line.startsWith("✓") ? "text-accent" : line.startsWith("⚠") || line.startsWith("⏳") ? "text-warn" : "text-text-dim"}>
                {line}
                {i === (snap?.logs.length ?? 0) - 1 && running && <span className="animate-pulse text-accent">▍</span>}
              </div>
            ))}
            {snap?.state === "success" && <div className="text-accent">Готово.</div>}
            {snap?.state === "error" && <div className="text-danger">Ошибка: {snap.error}</div>}
          </div>

          <div className="mt-3 flex justify-end gap-2">
            {(snap?.state === "error" || snap?.state === "success") && (
              <Button onClick={reset}>{snap.state === "error" ? "Повторить" : "Закрыть"}</Button>
            )}
            {running && (
              <Button variant="ghost" onClick={close}>
                Свернуть (задача продолжится)
              </Button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
