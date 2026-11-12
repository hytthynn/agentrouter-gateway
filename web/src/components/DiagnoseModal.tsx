/** Диагностика подключения (§8.6): чек-лист зелёный/красный */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import type { Account } from "@/api/types";
import { useToast } from "@/components/toast";
import { Button, Modal, Spinner } from "@/components/ui";

interface DiagnoseResult {
  account_id: number;
  checks: { key: string; ok: boolean; detail: string }[];
}

const CHECK_TITLES: Record<string, string> = {
  system_token: "System access token (/api/user/self)",
  upstream_models: "Ключ провайдера → /v1/models (WAF, транспорт)",
  models_list: "Список моделей аккаунта",
};

export function DiagnoseModal({ account, onClose }: { account: Account; onClose: () => void }) {
  const { toast } = useToast();
  const [result, setResult] = useState<DiagnoseResult | null>(null);

  const run = useMutation({
    mutationFn: () => api<DiagnoseResult>(`/api/accounts/${account.id}/diagnose`, { method: "POST" }),
    onSuccess: (data) => setResult(data),
    onError: (e) => toast(e.message, "error"),
  });

  const start = () => {
    setResult(null);
    run.mutate();
  };

  return (
    <Modal open onClose={onClose} title={`Диагностика: ${account.name}`} wide>
      {!result && !run.isPending && (
        <div className="py-4 text-sm text-text-dim">
          Последовательные проверки: system token, транспорт к /v1/models, каждый client-профиль
          (WAF-вердикт), список моделей.
          <div className="mt-4 flex justify-end">
            <Button variant="primary" onClick={start}>
              Запустить диагностику
            </Button>
          </div>
        </div>
      )}
      {run.isPending && <Spinner label="Выполняю проверки…" />}
      {result && (
        <div className="space-y-2">
          {result.checks.map((c) => {
            const isProfile = c.key.startsWith("profile:");
            const title = isProfile ? `Профиль ${c.key.slice(8)}` : (CHECK_TITLES[c.key] ?? c.key);
            return (
              <div
                key={c.key}
                className={`flex items-start gap-2.5 rounded-lg border p-2.5 text-[13px] ${
                  c.ok ? "border-accent/25 bg-accent/5" : "border-danger/30 bg-danger/5"
                }`}
              >
                <span className={c.ok ? "text-accent" : "text-danger"}>{c.ok ? "✓" : "✕"}</span>
                <div>
                  <span className="font-medium">{title}</span>
                  <div className="text-xs text-text-dim">{c.detail}</div>
                </div>
              </div>
            );
          })}
          <div className="flex justify-between pt-2">
            <span className="text-xs text-text-muted">
              Если WAF-блок на профилях — смените client-профиль в настройках (§10.3)
            </span>
            <div className="flex gap-2">
              <Button onClick={start}>Повторить</Button>
              <Button variant="primary" onClick={onClose}>
                Закрыть
              </Button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
