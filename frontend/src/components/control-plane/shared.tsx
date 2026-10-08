// Shared presentational primitives and formatters for the Control Plane view.
// Pure/presentational only — no data fetching or lifecycle. i18n-aware helpers
// receive the `t` function from the caller so this file stays hook-free.

import type { ComponentType } from "react";
import { Activity, CircleDot } from "lucide-react";
import { ApiError } from "@/lib/control-plane";
import type { Translate } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function StatusBadge({ value, prominent = false, muted = false, tone, label }: { value: string; prominent?: boolean; muted?: boolean; tone?: "positive" | "negative"; label?: string }) {
  // `value` is always the raw (English) backend status and drives both tone
  // auto-detection and the color logic. `label` is the localized display text;
  // tone stays correct in any locale because it never reads `label`. Pass an
  // explicit `tone` only where `value` itself is already a translated fallback.
  const normalized = value.toLowerCase();
  const positive = tone === "positive" || (tone === undefined && ["active", "ready", "completed", "passed", "valid", "succeeded"].some((item) => normalized.includes(item)));
  const negative = tone === "negative" || (tone === undefined && ["blocked", "failed", "stale", "cancelled", "reconciliation", "unavailable", "not initialized"].some((item) => normalized.includes(item)));
  return <span className={cn("inline-flex w-fit items-center rounded-full border px-2 py-1 text-[0.75rem] font-medium capitalize", prominent && "px-3 py-1.5 text-sm", muted && "bg-muted/40 text-muted-foreground", !muted && positive && "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", !muted && negative && "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200", !muted && !positive && !negative && "bg-muted/50 text-foreground")}>{label ?? value.replaceAll("_", " ")}</span>;
}

export function EmptyState({ text, icon: Icon = CircleDot }: { text: string; icon?: typeof CircleDot }) {
  return <div className="p-7 text-center text-xs text-muted-foreground"><Icon className="mx-auto mb-2 size-5 opacity-40" />{text}</div>;
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block space-y-1.5 text-xs font-medium text-muted-foreground"><span>{label}</span>{children}</label>;
}

export function Fact({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 truncate font-medium capitalize text-foreground" title={value}>{value.replaceAll("_", " ")}</dd></div>;
}

export function Metric({ icon: Icon, label, value, detail }: { icon: ComponentType<{ className?: string }>; label: string; value: string; detail: string }) {
  return <article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</p><Icon className="size-4 text-muted-foreground" /></div><p className="mt-3 text-2xl font-bold">{value}</p><p className="mt-1 text-xs text-muted-foreground">{detail}</p></article>;
}

export function SectionHeader({ icon: Icon, title, meta }: { icon: ComponentType<{ className?: string }>; title: string; meta: string }) {
  return <div className="flex items-center justify-between gap-3 border-b px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold"><Icon className="size-4" />{title}</h2><span className="text-xs text-muted-foreground">{meta}</span></div>;
}

// Kept for parity with the original Metric icon typing.
export type MetricIcon = typeof Activity;

export function messageFor(error: unknown, t: Translate): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return t("controlPlane.errors.auth401");
    if (error.status === 403) return t("controlPlane.errors.auth403");
    if (error.status === 404) return t("controlPlane.errors.notFound404");
    if (error.status === 409) return t("controlPlane.errors.conflict409");
    if (error.status === 503) return t("controlPlane.errors.busy503");
  }
  return error instanceof Error ? error.message : t("controlPlane.errors.snapshot");
}

export function formatNumber(value: number | null, unmeasured: string): string {
  return value === null ? unmeasured : new Intl.NumberFormat().format(value);
}

export function formatCurrency(value: number | null, unmeasured: string): string {
  return value === null ? unmeasured : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(value);
}

export function routeSourceLabel(source: "control_plane_route" | "compatibility_plan" | null, t: Translate): string {
  if (source === "control_plane_route") return t("controlPlane.route.controlPlaneRoute");
  if (source === "compatibility_plan") return t("controlPlane.route.compatibilityPlan");
  return t("controlPlane.route.notRouted");
}

export function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function shortId(value: string): string {
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}
