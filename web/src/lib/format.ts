/** Форматтеры: деньги, quota, токены, даты, длительности */

export function fmtUsd(v: number | null | undefined, sign = false): string {
  if (v === null || v === undefined) return "—";
  const s = Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(2);
  return (sign && v > 0 ? "+" : v < 0 ? "−$" : "$") + s.replace("-", "");
}

export function fmtQuota(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("ru-RU").format(Math.round(v));
}

export function fmtTokens(v: number | null | undefined): string {
  if (v === null || v === undefined) return "0";
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "k";
  return String(v);
}

export function fmtNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("ru-RU").format(v);
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "никогда";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return `${Math.floor(diff / 86400)} дн назад`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} мс`;
  return `${(ms / 1000).toFixed(1)} с`;
}

export function fmtQuotaToUsd(quota: number | null | undefined): number | null {
  if (quota === null || quota === undefined) return null;
  return quota / 500_000;
}
