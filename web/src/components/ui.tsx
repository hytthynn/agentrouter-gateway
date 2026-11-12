/** UI-примитивы: Button, Badge, Card, Modal, Toggle, EmptyState, Spinner, Code */

import { type ReactNode, useEffect, useRef, useState } from "react";

// ---------- Button ----------
export function Button({
  children,
  onClick,
  variant = "default",
  size,
  disabled,
  title,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  size?: "sm";
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
  className?: string;
}) {
  const cls = ["btn", variant === "default" ? "" : `btn-${variant}`, size === "sm" ? "btn-sm" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type} className={cls} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

// ---------- Badge ----------
const BADGE_STYLES: Record<string, string> = {
  active: "bg-[#1d2a12] text-accent",
  success: "bg-[#1d2a12] text-accent",
  needs_relogin: "bg-[#2a2410] text-warn",
  warn: "bg-[#2a2410] text-warn",
  error: "bg-[#2a1414] text-danger",
  client_abort: "bg-[#2a2410] text-warn",
  disabled: "bg-[#1a1d1a] text-text-muted",
  default: "bg-[#1a1d1a] text-text-dim",
};

const BADGE_LABELS: Record<string, string> = {
  active: "активен",
  needs_relogin: "нужен перелогин",
  error: "ошибка",
  disabled: "выключен",
  success: "успех",
  client_abort: "обрыв клиента",
};

export function Badge({ status, children }: { status: string; children?: ReactNode }) {
  const style = BADGE_STYLES[status] ?? BADGE_STYLES.default;
  return <span className={`badge ${style}`}>{children ?? BADGE_LABELS[status] ?? status}</span>;
}

// ---------- Card ----------
export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card p-4 ${className}`}>{children}</div>;
}

// ---------- Modal ----------
export function Modal({
  open,
  onClose,
  title,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-8"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className={`card w-full ${wide ? "max-w-3xl" : "max-w-lg"} p-5 shadow-2xl`}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="btn-ghost btn btn-sm" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ---------- Toggle ----------
export function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40 ${
        checked ? "bg-accent" : "bg-[#242b23]"
      }`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-[#0a0c09] transition-all ${
          checked ? "left-[18px]" : "left-0.5"
        }`}
      />
    </button>
  );
}

// ---------- Field ----------
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="mb-3">
      <label className="label">{label}</label>
      {children}
      {hint && <div className="mt-1 text-xs text-text-muted">{hint}</div>}
    </div>
  );
}

// ---------- Input ----------
export function Input({
  value,
  onChange,
  placeholder,
  type = "text",
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  className?: string;
}) {
  return (
    <input
      className={`input ${className}`}
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

// ---------- EmptyState ----------
export function EmptyState({ icon, title, hint, action }: { icon: string; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-14 text-center">
      <div className="text-4xl opacity-40">{icon}</div>
      <div className="text-sm font-medium text-text-dim">{title}</div>
      {hint && <div className="max-w-sm text-xs text-text-muted">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

// ---------- Spinner ----------
export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-text-dim">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      {label ?? "Загрузка…"}
    </div>
  );
}

// ---------- Code ----------
export function Code({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <code className={`mono rounded bg-[#0d100d] px-1.5 py-0.5 text-[12px] text-accent ${className}`}>
      {children}
    </code>
  );
}

// ---------- CopyButton ----------
export function CopyButton({ text, small = true }: { text: string | null | undefined; small?: boolean }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <Button
      size={small ? "sm" : undefined}
      variant="ghost"
      disabled={!text}
      onClick={() => {
        if (!text) return;
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          timer.current = setTimeout(() => setCopied(false), 1500);
        });
      }}
      title="Скопировать"
    >
      {copied ? "✓ скопировано" : "⧉ копировать"}
    </Button>
  );
}
