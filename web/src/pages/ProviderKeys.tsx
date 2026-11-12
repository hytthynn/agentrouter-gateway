/** Ключи провайдера (FR-2): таблица по аккаунтам, маскированные ключи, reveal */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, revealKey } from "@/api/client";
import type { CatalogModel, ProviderKey } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/toast";
import { Badge, Button, CopyButton, EmptyState, Field, Modal, Spinner, Toggle } from "@/components/ui";
import { fmtDateTime, fmtQuota } from "@/lib/format";

export default function ProviderKeysPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: keys, isLoading } = useQuery({
    queryKey: ["provider-keys"],
    queryFn: () => api<ProviderKey[]>("/api/provider-keys"),
    refetchInterval: 30_000,
  });
  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api<{ id: number; name: string; status: string }[]>("/api/accounts"),
  });
  const { data: catalog } = useQuery({
    queryKey: ["models"],
    queryFn: () => api<CatalogModel[]>("/api/models"),
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ProviderKey | null>(null);
  const [revealed, setRevealed] = useState<Record<number, string>>({});

  const invalidate = () => void qc.invalidateQueries({ queryKey: ["provider-keys"] });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/api/provider-keys/${id}`, { method: "PATCH", body: { enabled } }),
    onSuccess: invalidate,
    onError: (e) => toast(e.message, "error"),
  });
  const del = useMutation({
    mutationFn: (id: number) => api(`/api/provider-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      toast("Ключ удалён у провайдера и локально", "success");
    },
    onError: (e) => toast(e.message, "error"),
  });
  const reveal = useMutation({
    mutationFn: (id: number) => revealKey("provider-keys", id),
    onSuccess: (key, id) => setRevealed((r) => ({ ...r, [id]: key })),
    onError: (e) => toast(e.message, "error"),
  });

  if (isLoading) return <Spinner />;

  // Группировка по аккаунтам (§12.3 п.3)
  const groups = new Map<number, ProviderKey[]>();
  for (const k of keys ?? []) {
    const list = groups.get(k.account_id) ?? [];
    list.push(k);
    groups.set(k.account_id, list);
  }

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <h1 className="text-lg font-semibold">Ключи провайдера</h1>
        <Button variant="primary" onClick={() => setCreateOpen(true)} disabled={!accounts?.length}>
          + Создать ключ
        </Button>
      </header>

      <div className="p-6">
        {!keys || keys.length === 0 ? (
          <EmptyState
            icon="⚿"
            title="Ключей провайдера нет"
            hint="Безлимитный ключ выпускается автоматически при добавлении аккаунта. Здесь можно создать дополнительные ключи."
            action={accounts?.length ? <Button variant="primary" onClick={() => setCreateOpen(true)}>+ Создать ключ</Button> : undefined}
          />
        ) : (
          <div className="space-y-5">
            {[...groups.entries()].map(([accountId, list]) => {
              const acc = accounts?.find((a) => a.id === accountId);
              return (
                <div key={accountId}>
                  <div className="mb-1.5 flex items-center gap-2 text-sm font-medium text-text-dim">
                    {acc?.name ?? `аккаунт #${accountId}`}
                    <span className="text-xs font-normal text-text-muted">
                      {list.length} ключ(ей)
                    </span>
                  </div>
                  <div className="card overflow-x-auto">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Имя</th>
                          <th>Ключ</th>
                          <th>Лимит</th>
                          <th>Расход (quota)</th>
                          <th>Срок</th>
                          <th>Статус</th>
                          <th className="w-40">Действия</th>
                        </tr>
                      </thead>
                      <tbody>
                        {list.map((k) => (
                          <tr key={k.id}>
                            <td>
                              {k.name}
                              {k.created_via === "auto" && (
                                <span className="ml-1.5 text-[10px] text-text-muted">авто</span>
                              )}
                            </td>
                            <td className="mono">
                              {revealed[k.id] ?? k.key_masked}
                              {revealed[k.id] && <CopyButton text={revealed[k.id]} />}
                            </td>
                            <td>
                              {k.unlimited_quota ? (
                                <span className="text-accent">безлимит</span>
                              ) : (
                                fmtQuota(k.remain_quota)
                              )}
                            </td>
                            <td className="mono">{fmtQuota(k.used_quota)}</td>
                            <td>{k.expired_time === -1 ? "бессрочно" : fmtDateTime(new Date(k.expired_time * 1000).toISOString())}</td>
                            <td>
                              <Badge status={k.status} />
                            </td>
                            <td>
                              <div className="flex items-center gap-1.5">
                                <Toggle checked={k.status === "active"} onChange={(v) => toggle.mutate({ id: k.id, enabled: v })} />
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
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <CreateKeyModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        accounts={accounts ?? []}
        catalog={catalog ?? []}
        onCreated={invalidate}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && del.mutate(deleteTarget.id)}
        title="Удалить ключ?"
      >
        Ключ <span className="mono text-text">{deleteTarget?.key_masked}</span> будет удалён у
        провайдера (DELETE /api/token/{`{id}`}) и локально.
      </ConfirmDialog>
    </>
  );
}

function CreateKeyModal({
  open,
  onClose,
  accounts,
  catalog,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  accounts: { id: number; name: string }[];
  catalog: CatalogModel[];
  onCreated: () => void;
}) {
  const { toast } = useToast();
  const [form, setForm] = useState({
    account_id: accounts[0]?.id ?? 0,
    name: "agw-manual",
    unlimited: true,
    remain_quota: 500_000,
    never_expires: true,
    expired_date: "",
    all_models: true,
    models: [] as string[],
  });

  const create = useMutation({
    mutationFn: () =>
      api<ProviderKey>("/api/provider-keys", {
        method: "POST",
        body: {
          account_id: form.account_id,
          name: form.name,
          unlimited_quota: form.unlimited,
          remain_quota: form.unlimited ? null : form.remain_quota,
          expired_time: form.never_expires ? -1 : Math.floor(new Date(form.expired_date).getTime() / 1000),
          model_limits_enabled: !form.all_models,
          model_limits: form.all_models ? null : form.models,
        },
      }),
    onSuccess: () => {
      onCreated();
      toast("Ключ создан у провайдера", "success");
      onClose();
    },
    onError: (e) => toast(e.message, "error"),
  });

  return (
    <Modal open={open} onClose={onClose} title="Создать ключ провайдера">
      <Field label="Аккаунт">
        <select
          className="select"
          value={form.account_id}
          onChange={(e) => setForm({ ...form, account_id: Number(e.target.value) })}
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Имя">
        <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </Field>
      <div className="mb-3 flex items-center gap-3">
        <Toggle checked={form.unlimited} onChange={(v) => setForm({ ...form, unlimited: v })} />
        <span className="text-sm">Безлимитный</span>
        {!form.unlimited && (
          <input
            className="input mono ml-auto w-40"
            type="number"
            value={form.remain_quota}
            onChange={(e) => setForm({ ...form, remain_quota: Number(e.target.value) })}
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
        <span className="text-sm">Все модели</span>
      </div>
      {!form.all_models && (
        <Field label="Модели">
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
        <Button onClick={onClose}>Отмена</Button>
        <Button variant="primary" disabled={create.isPending} onClick={() => create.mutate()}>
          Создать
        </Button>
      </div>
    </Modal>
  );
}
