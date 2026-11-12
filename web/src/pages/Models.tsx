/** Каталог моделей (FR-4): поиск, переключатель enabled, бейджи доступности, синк */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import type { CatalogModel } from "@/api/types";
import { useToast } from "@/components/toast";
import { Badge, Button, EmptyState, Input, Spinner, Toggle } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";

export default function ModelsPage() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [search, setSearch] = useState("");

  const { data: models, isLoading } = useQuery({
    queryKey: ["models", search],
    queryFn: () => api<CatalogModel[]>("/api/models", { params: { search } }),
    refetchInterval: 60_000,
  });

  const sync = useMutation({
    mutationFn: () => api<{ task_id: string }>("/api/models/sync", { method: "POST" }),
    onSuccess: () => {
      toast("Синхронизация запущена", "info");
      setTimeout(() => void qc.invalidateQueries({ queryKey: ["models"] }), 4000);
    },
    onError: (e) => toast(e.message, "error"),
  });

  const toggle = useMutation({
    mutationFn: ({ model_id, enabled }: { model_id: string; enabled: boolean }) =>
      api(`/api/models/${encodeURIComponent(model_id)}`, { method: "PATCH", body: { enabled } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e) => toast(e.message, "error"),
  });

  const enabledCount = models?.filter((m) => m.enabled).length ?? 0;

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <h1 className="text-lg font-semibold">Каталог моделей</h1>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">
            {enabledCount} из {models?.length ?? 0} включено
          </span>
          <Button onClick={() => sync.mutate()} disabled={sync.isPending}>
            ⟳ Синхронизировать
          </Button>
        </div>
      </header>

      <div className="p-6">
        <div className="mb-3 max-w-sm">
          <Input placeholder="Поиск по подстроке…" value={search} onChange={(v) => setSearch(v)} />
        </div>

        {isLoading ? (
          <Spinner />
        ) : !models || models.length === 0 ? (
          <EmptyState
            icon="⌘"
            title="Каталог пуст"
            hint="Модели синхронизируются с провайдера при добавлении аккаунта. Запустите синхронизацию вручную."
            action={<Button variant="primary" onClick={() => sync.mutate()}>⟳ Синхронизировать</Button>}
          />
        ) : (
          <div className="card overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>model_id</th>
                  <th>Доступность</th>
                  <th>Источник</th>
                  <th>Обновлено</th>
                  <th className="w-28">В шлюзе</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.model_id}>
                    <td className="mono">{m.model_id}</td>
                    <td>
                      {m.no_active_accounts ? (
                        <Badge status="disabled">нет активных аккаунтов</Badge>
                      ) : (
                        <Badge status="active">
                          {m.accounts_available}/{m.accounts_total} аккаунтов
                        </Badge>
                      )}
                    </td>
                    <td className="text-text-dim">{m.added_from ?? "—"}</td>
                    <td className="text-text-muted">{fmtDateTime(m.created_at)}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <Toggle checked={m.enabled} onChange={(v) => toggle.mutate({ model_id: m.model_id, enabled: v })} />
                        <span className="text-xs text-text-muted">{m.enabled ? "вкл" : "выкл"}</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
