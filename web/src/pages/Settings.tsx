/** Настройки (FR-10): формы, hot-reload, проверка здоровья, client-профили (§10.2) */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { Health, Profile } from "@/api/types";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Field, Spinner, Toggle } from "@/components/ui";

interface SettingsData {
  [key: string]: string;
}

const SECTIONS: { title: string; hint?: string; keys: { key: string; label: string; type?: "text" | "number" | "select" | "toggle"; options?: string[]; hint?: string }[] }[] = [
  {
    title: "Маршрутизация",
    keys: [
      { key: "routing_strategy", label: "Стратегия", type: "select", options: ["round_robin", "least_used", "sequential"] },
      { key: "max_attempts", label: "Макс. попыток failover", type: "number" },
      { key: "gateway_concurrency", label: "Параллелизм шлюза", type: "number", hint: "одновременных запросов на шлюз (FR-6.7)" },
      { key: "account_concurrency", label: "Параллелизм аккаунта", type: "number", hint: "на один аккаунт" },
      { key: "upstream_timeout_s", label: "Таймаут апстрима, с", type: "number" },
      { key: "default_client_profile", label: "Client-профиль по умолчанию", type: "text", hint: "имперсонация для обхода WAF (§10)" },
    ],
  },
  {
    title: "Чек-ин",
    keys: [
      { key: "checkin_enabled", label: "Чек-ин включён", type: "toggle" },
      { key: "checkin_daily_at", label: "Время (локальное HH:MM)", type: "text", hint: "± джиттер 0–15 мин на аккаунт" },
      { key: "checkin_relogin_after", label: "Re-login после чек-ина", type: "toggle", hint: "нюанс зачисления бонуса (§2.3)" },
    ],
  },
  {
    title: "Фоновые задачи",
    keys: [
      { key: "balance_refresh_min", label: "Обновление балансов, мин", type: "number" },
      { key: "session_check_min", label: "Мониторинг сессий, мин", type: "number" },
      { key: "session_auto_relogin", label: "Авто-перелогин при 401", type: "toggle" },
      { key: "models_sync_min", label: "Синк каталога моделей, мин", type: "number" },
      { key: "log_retention_days", label: "Ретеншн логов запросов, дней", type: "number", hint: "системные — ×3" },
    ],
  },
  {
    title: "Провайдер",
    keys: [{ key: "provider_base_url", label: "Base URL", type: "text" }],
  },
];

const JOBS = ["checkin", "balance_refresh", "session_monitor", "models_sync", "retention"];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("/api/settings") });
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: () => api<Profile[]>("/api/profiles") });
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health") });

  const [draft, setDraft] = useState<SettingsData>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (settings && !dirty) setDraft(settings);
  }, [settings, dirty]);

  const save = useMutation({
    mutationFn: (values: SettingsData) => api<SettingsData>("/api/settings", { method: "PUT", body: { values } }),
    onSuccess: (data) => {
      setDraft(data);
      setDirty(false);
      void qc.invalidateQueries({ queryKey: ["settings"] });
      toast("Настройки сохранены, расписания применены без рестарта (FR-10.2)", "success");
    },
    onError: (e) => toast(e.message, "error"),
  });

  const runJob = useMutation({
    mutationFn: (job: string) => api<Record<string, unknown>>(`/api/tasks/${job}/run`, { method: "POST" }),
    onSuccess: (data, job) => toast(`Джоба ${job}: ${JSON.stringify(data)}`, "success"),
    onError: (e) => toast(e.message, "error"),
  });

  const set = (key: string, value: string) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  };

  const setBool = (key: string, v: boolean) => set(key, v ? "true" : "false");

  if (!settings) return <Spinner />;

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <h1 className="text-lg font-semibold">Настройки</h1>
        <div className="flex items-center gap-2">
          {dirty && <span className="text-xs text-warn">есть изменения</span>}
          <Button variant="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate(draft)}>
            Сохранить
          </Button>
        </div>
      </header>

      <div className="grid gap-5 p-6 xl:grid-cols-2">
        <div className="space-y-5">
          {SECTIONS.map((section) => (
            <Card key={section.title}>
              <div className="mb-3 text-sm font-semibold">{section.title}</div>
              {section.keys.map((k) => (
                <div key={k.key}>
                  {k.type === "toggle" ? (
                    <div className="mb-3 flex items-center justify-between">
                      <div>
                        <span className="text-sm">{k.label}</span>
                        {k.hint && <div className="text-[11px] text-text-muted">{k.hint}</div>}
                      </div>
                      <Toggle checked={draft[k.key] === "true"} onChange={(v) => setBool(k.key, v)} />
                    </div>
                  ) : (
                    <Field label={k.label} hint={k.hint}>
                      {k.type === "select" ? (
                        <select className="select" value={draft[k.key] ?? ""} onChange={(e) => set(k.key, e.target.value)}>
                          {k.options?.map((o) => (
                            <option key={o} value={o}>
                              {o}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className={`input ${k.type === "number" ? "mono" : ""}`}
                          type={k.type === "number" ? "number" : "text"}
                          value={draft[k.key] ?? ""}
                          onChange={(e) => set(k.key, e.target.value)}
                        />
                      )}
                    </Field>
                  )}
                </div>
              ))}
            </Card>
          ))}
        </div>

        <div className="space-y-5">
          {/* Проверка здоровья (FR-10.3) */}
          <Card>
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-semibold">Состояние системы</span>
              {health && (
                <Badge status={health.db === "ok" && health.encryption === "ok" ? "active" : "error"}>
                  {health.db === "ok" && health.encryption === "ok" ? "здоров" : "есть проблемы"}
                </Badge>
              )}
            </div>
            {health && (
              <div className="space-y-1.5 text-[13px]">
                <HealthRow label="БД (SQLite WAL)" ok={health.db === "ok"} detail={health.db} />
                <HealthRow label="Шифрование AES-256-GCM" ok={health.encryption === "ok"} detail={health.encryption} />
                <HealthRow
                  label="Апстрим agentrouter.org"
                  ok={health.upstream_reachable}
                  detail={health.upstream_reachable ? `версия ${health.provider_version ?? "?"}` : (health.upstream_error ?? "недоступен")}
                />
                <HealthRow label="Мастер-ключ" ok detail={health.master_key_source === "env" ? "из env AGW_MASTER_KEY" : "файл data/.master_key"} />
              </div>
            )}
          </Card>

          {/* Ручной запуск джоб (§11) */}
          <Card>
            <div className="mb-3 text-sm font-semibold">Фоновые задачи — ручной запуск</div>
            <div className="flex flex-wrap gap-2">
              {JOBS.map((job) => (
                <Button key={job} size="sm" disabled={runJob.isPending} onClick={() => runJob.mutate(job)}>
                  ▶ {job}
                </Button>
              ))}
            </div>
          </Card>

          {/* Client-профили (§10.2) */}
          <Card>
            <div className="mb-1 text-sm font-semibold">Client-профили имперсонации</div>
            <p className="mb-3 text-[11px] text-text-muted">
              UA-строки калибруются через диагностику аккаунта; правятся без пересборки (§10.2).
            </p>
            <div className="space-y-3">
              {(profiles ?? []).map((p) => (
                <ProfileEditor key={p.name} profile={p} />
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}

function HealthRow({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-text-dim">{label}</span>
      <span className={ok ? "text-accent" : "text-danger"}>{detail}</span>
    </div>
  );
}

function ProfileEditor({ profile }: { profile: Profile }) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [ua, setUa] = useState(profile.user_agent);

  const save = useMutation({
    mutationFn: () =>
      api("/api/profiles/" + encodeURIComponent(profile.name), {
        method: "PUT",
        body: { user_agent: ua, extra_headers: profile.extra_headers },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["profiles"] });
      toast(`Профиль ${profile.name} обновлён`, "success");
    },
    onError: (e) => toast(e.message, "error"),
  });

  return (
    <div className="rounded-lg border border-border p-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="mono text-xs text-accent">
          {profile.name}
          {profile.is_default && <span className="ml-1.5 text-text-muted">· по умолчанию</span>}
        </span>
        {ua !== profile.user_agent && (
          <Button size="sm" onClick={() => save.mutate()}>
            Сохранить UA
          </Button>
        )}
      </div>
      <textarea
        className="textarea mono text-[11px]"
        rows={2}
        value={ua}
        onChange={(e) => setUa(e.target.value)}
      />
    </div>
  );
}
