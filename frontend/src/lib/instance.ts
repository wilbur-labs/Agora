/**
 * Which Agora this page is talking to (CR-31). The user runs a stable install every day
 * while a development checkout changes underneath; the bar must never let one pass for
 * the other. Only what /health says counts: "stable" is shown only when the launcher
 * found the code exactly on a release tag, and anything unreadable says so.
 */
export type AgoraHealth = { status: string; version?: string; instance?: string };

export type InstanceBadge =
  | { kind: "pending" }
  | { kind: "failed" }
  | { kind: "stable" | "dev" | "unknown"; version: string | null };

export function instanceBadgeOf(health: AgoraHealth | null | "failed"): InstanceBadge {
  if (health === null) return { kind: "pending" };
  if (health === "failed") return { kind: "failed" };
  const version = typeof health.version === "string" && health.version ? health.version : null;
  const kind = health.instance === "stable" || health.instance === "dev" ? health.instance : "unknown";
  return { kind, version };
}
