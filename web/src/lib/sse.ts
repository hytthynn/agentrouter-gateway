/** SSE подписка на прогресс задач автоматизации (§7 /api/tasks/{id}/events) */

import type { TaskSnapshot } from "@/api/types";

export type { TaskSnapshot };
export type { TaskStep } from "@/api/types";

export interface TaskEvent {
  type: "snapshot" | "log" | "steps" | "state" | "ping" | "error";
  line?: string;
  steps?: TaskSnapshot["steps"];
  state?: TaskSnapshot["state"];
  error?: string;
  result?: Record<string, unknown> | null;
  logs?: string[];
}

export function subscribeTask(
  taskId: string,
  onEvent: (ev: TaskEvent, snapshot: TaskSnapshot) => void,
  onDone: (state: TaskSnapshot["state"], error: string | null, result: Record<string, unknown> | null) => void,
): () => void {
  const es = new EventSource(`/api/tasks/${taskId}/events`);
  let snapshot: TaskSnapshot = {
    task_id: taskId,
    kind: "",
    account_id: null,
    state: "running",
    steps: [],
    logs: [],
    error: null,
    result: null,
  };
  let finished = false;

  const apply = (ev: TaskEvent) => {
    if (ev.type === "snapshot") {
      snapshot = {
        ...snapshot,
        state: ev.state ?? snapshot.state,
        steps: ev.steps ?? snapshot.steps,
        logs: ev.logs ?? snapshot.logs,
        error: ev.error ?? snapshot.error,
        result: ev.result ?? snapshot.result,
      };
    } else if (ev.type === "log" && ev.line) {
      snapshot = { ...snapshot, logs: [...snapshot.logs, ev.line].slice(-400) };
    } else if (ev.type === "steps" && ev.steps) {
      snapshot = { ...snapshot, steps: ev.steps };
    } else if (ev.type === "state") {
      snapshot = { ...snapshot, state: ev.state ?? snapshot.state, error: ev.error ?? snapshot.error };
    }
    if (!finished) onEvent(ev, snapshot);
    if (snapshot.state === "success" || snapshot.state === "error") {
      if (!finished) {
        finished = true;
        onDone(snapshot.state, snapshot.error, snapshot.result);
      }
      es.close();
    }
  };

  es.onmessage = (msg) => {
    try {
      apply(JSON.parse(msg.data) as TaskEvent);
    } catch {
      /* игнорируем битые события */
    }
  };
  es.onerror = () => {
    es.close();
  };
  return () => es.close();
}
