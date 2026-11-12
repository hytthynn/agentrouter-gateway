/** Тосты (sonner-подобные, §12.4): контекст + хук useToast */

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

interface Toast {
  id: number;
  kind: "success" | "error" | "info";
  message: string;
}

const ToastContext = createContext<{
  toast: (message: string, kind?: Toast["kind"]) => void;
}>({ toast: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const idRef = useRef(1);

  const toast = useCallback((message: string, kind: Toast["kind"] = "info") => {
    const id = idRef.current++;
    setItems((prev) => [...prev.slice(-4), { id, kind, message }]);
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), 4200);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[100] flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            className={`card pointer-events-auto flex items-start gap-2 border-l-2 p-3 text-sm shadow-xl ${
              t.kind === "success"
                ? "border-l-accent"
                : t.kind === "error"
                  ? "border-l-danger"
                  : "border-l-text-muted"
            }`}
          >
            <span className="mt-0.5">{t.kind === "success" ? "✓" : t.kind === "error" ? "✕" : "ℹ"}</span>
            <span className="break-words">{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
