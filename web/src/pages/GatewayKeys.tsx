/** Ключи шлюза (FR-5): таблица, создание с лимитом токенов, «Подключить IDE» (FR-5.5) */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, revealKey } from "@/api/client";
import type { CatalogModel, GatewayKey } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/toast";
import {
  Badge,
  Button,
  Code,
  CopyButton,
  EmptyState,
  Field,
  Modal,
  Spinner,
  Toggle,
} from "@/components/ui";
import { fmtDateTime, fmtNum, fmtTokens } from "@/lib/format";

export default function GatewayKeysPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: keys, isLoading } = useQuery({
    queryKey: ["gateway-keys"],
    queryFn: () => api<GatewayKey[]>("/api/keys"),
    refetchInterval: 30_000,
  });
  const { data: catalog } = useQuery({
    queryKey: ["models"],
    queryFn: () => api<CatalogModel[]>("/api/models"),
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [ideKey, setIdeKey] = useState<GatewayKey | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<GatewayKey | null>(null);
  const [revealed, setRevealed] = useState<Record<number, string>>({});

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["gateway-keys"] });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/api/keys/${id}`, { method: "PATCH", body: { enabled } }),
    onSuccess: invalidate,
    onError: (e) => toast(e.message, "error"),
  });
  const del = useMutation({
    mutationFn: (id: number) => api(`/api/keys/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      toast("Ключ удалён", "success");
    },
    onError: (e) => toast(e.message, "error"),
  });
  const reveal = useMutation({
    mutationFn: (id: number) => revealKey("keys", id),
    onSuccess: (key, id) => setRevealed((r) => ({ ...r, [id]: key })),
    onError: (e) => toast(e.message, "error"),
  });

  if (isLoading) return <Spinner />;

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <h1 className="text-lg font-semibold">Ключи шлюза</h1>
        <Button variant="primary" onClick={() => setCreateOpen(true)}>
          + Создать ключ
        </Button>
      </header>

      <div className="p-6">
        {!keys || keys.length === 0 ? (
          <EmptyState
            icon="🖧"
            title="Ключей шлюза нет"
            hint="Ключ шлюза agw-… используется в IDE (Cline, Cursor, Codex CLI…) как обычный OpenAI API-ключ с base_url http://127.0.0.1:8787/v1."
            action={
              <Button variant="primary" onClick={() => setCreateOpen(true)}>
                + Создать ключ
              </Button>
            }
          />
        ) : (
          <div className="card overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Имя</th>
                  <th>Ключ</th>
                  <th>Лимит токенов</th>
                  <th>Использовано</th>
                  <th>Запросы</th>
                  <th>Модели</th>
                  <th>Срок</th>
                  <th>Статус</th>
                  <th className="w-44">Действия</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => {
                  const overLimit = k.token_limit !== null && k.tokens_used >= k.token_limit;
                  return (
                    <tr key={k.id}>
                      <td>{k.name}</td>
                      <td className="mono">
                        {revealed[k.id] ?? k.key_masked}
                        {revealed[k.id] && <CopyButton text={revealed[k.id]} />}
                      </td>
                      <td className={overLimit ? "text-danger" : ""}>
                        {k.token_limit === null ? <span className="text-accent">безлимит</span> : fmtNum(k.token_limit)}
                      </td>
                      <td className="mono">
                        {fmtTokens(k.tokens_used)}
                        {k.token_limit !== null && (
                          <span className="text-text-muted"> / {fmtTokens(k.token_limit)}</span>
                        )}
                      </td>
                      <td className="mono">{fmtNum(k.request_count)}</td>
                      <td className="text-text-dim">{k.allowed_models.length ? k.allowed_models.length : "все"}</td>
                      <td className="text-text-muted">
                        {k.expires_at ? fmtDateTime(k.expires_at) : "бессрочно"}
                      </td>
                      <td>
                        <Badge status={overLimit && k.status === "active" ? "warn" : k.status}>
                          {overLimit ? "лимит исчерпан" : undefined}
                        </Badge>
                      </td>
                      <td>
                        <div className="flex items-center gap-1.5">
                          <Toggle checked={k.status === "active"} onChange={(v) => toggle.mutate({ id: k.id, enabled: v })} />
                          <Button size="sm" variant="ghost" onClick={() => setIdeKey(k)} title="Подключить IDE">
                            ⇥ IDE
                          </Button>
                          {!revealed[k.id] && (
                            <Button size="sm" variant="ghost" onClick={() => reveal.mutate(k.id)} title="Показать ключ">
                              👁
                            </Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(k)} title="Удалить">
                            🗑
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <CreateGatewayKeyModal open={createOpen} onClose={() => setCreateOpen(false)} catalog={catalog ?? []} />

      {ideKey && <IdeModal key_={ideKey} onClose={() => setIdeKey(null)} />}

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && del.mutate(deleteTarget.id)}
        title="Удалить ключ шлюза?"
      >
        Ключ <span className="mono">{deleteTarget?.key_masked}</span> перестанет работать во всех
        подключённых IDE.
      </ConfirmDialog>
    </>
  );
}

function CreateGatewayKeyModal({
  open,
  onClose,
  catalog,
}: {
  open: boolean;
  onClose: () => void;
  catalog: CatalogModel[];
}) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: "",
    unlimited: true,
    token_limit: 1_000_000,
    never_expires: true,
    expired_date: "",
    all_models: true,
    models: [] as string[],
  });
  const [created, setCreated] = useState<GatewayKey | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api<GatewayKey>("/api/keys", {
        method: "POST",
        body: {
          name: form.name || "ключ шлюза",
          token_limit: form.unlimited ? null : form.token_limit,
          expires_at:
            form.never_expires || !form.expired_date
              ? null
              : new Date(form.expired_date).toISOString(),
          allowed_models: form.all_models ? null : form.models,
        },
      }),
    onSuccess: (data) => {
      setCreated(data);
      void qc.invalidateQueries({ queryKey: ["gateway-keys"] });
    },
    onError: (e) => toast(e.message, "error"),
  });

  const close = () => {
    setCreated(null);
    onClose();
  };

  return (
    <Modal open={open} onClose={close} title="Создать ключ шлюза">
      {created ? (
        <div>
          <div className="mb-3 rounded-lg border border-accent/30 bg-accent/5 p-3">
            <div className="mb-1.5 text-sm font-medium text-accent">
              Ключ показывается полностью один раз — сохраните его:
            </div>
            <div className="mono break-all rounded bg-[#0d100d] p-2.5 text-[13px] text-accent">
              {created.full_key}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <CopyButton text={created.full_key} small={false} />
            <Button variant="primary" onClick={close}>
              Готово
            </Button>
          </div>
        </div>
      ) : (
        <div>
          <Field label="Имя">
            <input
              className="input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="мой ноутбук"
              autoFocus
            />
          </Field>
          <div className="mb-3 flex items-center gap-3">
            <Toggle checked={form.unlimited} onChange={(v) => setForm({ ...form, unlimited: v })} />
            <span className="text-sm">Безлимит токенов</span>
            {!form.unlimited && (
              <input
                className="input mono ml-auto w-44"
                type="number"
                value={form.token_limit}
                onChange={(e) => setForm({ ...form, token_limit: Number(e.target.value) })}
              />
            )}
          </div>
          <div className="mb-3 flex items-center gap-3">
            <Toggle checked={form.never_expires} onChange={(v) => setForm({ ...form, never_expires: v })} />
            <span className="text-sm">Бессрочно</span>
            {!form.never_expires && (
              <input
                className="input ml-auto w-44"
                type="datetime-local"
                value={form.expired_date}
                onChange={(e) => setForm({ ...form, expired_date: e.target.value })}
              />
            )}
          </div>
          <div className="mb-3 flex items-center gap-3">
            <Toggle checked={form.all_models} onChange={(v) => setForm({ ...form, all_models: v })} />
            <span className="text-sm">Все модели каталога</span>
          </div>
          {!form.all_models && (
            <Field label="Разрешённые модели">
              <select
                className="select"
                multiple
                size={5}
                value={form.models}
                onChange={(e) => setForm({ ...form, models: [...e.target.selectedOptions].map((o) => o.value) })}
              >
                {catalog.map((m) => (
                  <option key={m.model_id} value={m.model_id}>
                    {m.model_id}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <div className="flex justify-end gap-2">
            <Button onClick={close}>Отмена</Button>
            <Button variant="primary" disabled={create.isPending} onClick={() => create.mutate()}>
              Создать
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** FR-5.5: сниппеты подключения IDE */
function IdeModal({ key_, onClose }: { key_: GatewayKey; onClose: () => void }) {
  const [full, setFull] = useState<string | null>(null);
  const [tab, setTab] = useState<"openai" | "cline" | "cursor" | "codex">("openai");

  const fetchFull = () => {
    void revealKey("keys", key_.id).then(setFull);
  };

  const keyText = full ?? key_.key_masked;
  const snippets: Record<string, { title: string; body: React.ReactNode }> = {
    openai: {
      title: "Любой OpenAI-клиент",
      body: (
        <EnvSnippet
          lines={[
            `OPENAI_API_KEY=${keyText}`,
            "OPENAI_BASE_URL=http://127.0.0.1:8787/v1",
          ]}
        />
      ),
    },
    cline: {
      title: "Cline / Roo Code / Kilo Code",
      body: (
        <SettingsSnippet
          rows={[
            ["API Provider", "OpenAI Compatible"],
            ["Base URL", "http://127.0.0.1:8787/v1"],
            ["API Key", keyText],
            ["Model ID", "glm-4.6"],
          ]}
        />
      ),
    },
    cursor: {
      title: "Cursor",
      body: (
        <SettingsSnippet
          rows={[
            ["Override OpenAI Base URL", "http://127.0.0.1:8787/v1"],
            ["OpenAI API Key", keyText],
            ["Model", "glm-4.6 (Add model)"],
          ]}
        />
      ),
    },
    codex: {
      title: "Codex CLI",
      body: (
        <div className="space-y-2">
          <p className="text-xs text-text-muted">
            В <Code>~/.codex/config.toml</Code>:
          </p>
          <pre className="mono overflow-x-auto rounded-lg border border-border bg-[#0d100d] p-3 text-[12px] leading-5">
{`model = "glm-4.6"
model_provider = "agentrouter"

[model_providers.agentrouter]
name = "AgentRouter Gateway"
base_url = "http://127.0.0.1:8787/v1"
wire_api = "chat"
env_key = "AGW_API_KEY"`}
          </pre>
          <p className="text-xs text-text-muted">И переменная окружения:</p>
          <EnvSnippet lines={[`AGW_API_KEY=${keyText}`]} />
        </div>
      ),
    },
  };

  return (
    <Modal open onClose={onClose} title={`Подключить IDE — ${key_.name}`} wide>
      {full === null ? (
        <div className="py-4 text-center">
          <Button variant="primary" onClick={fetchFull}>
            Показать ключ для сниппетов
          </Button>
        </div>
      ) : (
        <>
          <div className="mb-3 flex gap-1.5">
            {Object.entries(snippets).map(([k, v]) => (
              <button
                key={k}
                onClick={() => setTab(k as typeof tab)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  tab === k ? "bg-accent-dim/40 text-accent" : "text-text-dim hover:bg-elevated"
                }`}
              >
                {v.title}
              </button>
            ))}
          </div>
          {snippets[tab].body}
        </>
      )}
    </Modal>
  );
}

function EnvSnippet({ lines }: { lines: string[] }) {
  const text = lines.join("\n");
  return (
    <div className="relative">
      <pre className="mono overflow-x-auto rounded-lg border border-border bg-[#0d100d] p-3 text-[12px] leading-5">
        {text}
      </pre>
      <div className="absolute right-2 top-2">
        <CopyButton text={text} />
      </div>
    </div>
  );
}

function SettingsSnippet({ rows }: { rows: [string, string][] }) {
  const text = rows.map(([k, v]) => `${k}: ${v}`).join("\n");
  return (
    <div className="relative">
      <table className="w-full text-[13px]">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td className="py-1 pr-3 text-text-muted">{k}</td>
              <td className="mono py-1">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1">
        <CopyButton text={text} />
      </div>
    </div>
  );
}
