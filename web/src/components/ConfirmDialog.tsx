/** ConfirmDialog: подтверждение удаления; опц. ввод имени аккаунта (FR-1.4) */

import { useEffect, useState, type ReactNode } from "react";
import { Button, Modal } from "./ui";

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  children,
  confirmLabel = "Удалить",
  typeName,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  children: ReactNode;
  confirmLabel?: string;
  typeName?: string;
}) {
  const [input, setInput] = useState("");

  useEffect(() => {
    if (open) setInput("");
  }, [open]);

  const blocked = typeName !== undefined && input !== typeName;

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="text-sm text-text-dim">{children}</div>
      {typeName !== undefined && (
        <div className="mt-3">
          <label className="label">
            Введите имя <span className="mono text-warn">{typeName}</span> для подтверждения
          </label>
          <input className="input mono" value={input} onChange={(e) => setInput(e.target.value)} autoFocus />
        </div>
      )}
      <div className="mt-5 flex justify-end gap-2">
        <Button onClick={onClose}>Отмена</Button>
        <Button variant="danger" disabled={blocked} onClick={() => { onConfirm(); onClose(); }}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
