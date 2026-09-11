/**
 * The one live probe of the agent CLIs, shared by every surface that shows it.
 *
 * `GET /api/runtime-capabilities` spawns each CLI for `--version` and again for its
 * sign-in status — six child processes per call. Three surfaces read it (the top bar on
 * every screen, Environments, Settings), and each asking on its own meant twelve
 * processes on one page load and two answers that could disagree on the same screen
 * when one set timed out and the other did not. So the probe lives here, once:
 *
 *   - a call while one is in flight joins it;
 *   - a fresh answer is reused for `TTL_MS`, so navigating between screens does not
 *     re-spawn the CLIs, while a CLI installed a minute ago is seen without a reload;
 *   - `force` asks again now (the Settings refresh button), and every surface that
 *     subscribed hears the new answer — the bar does not keep saying "not installed"
 *     beside a Settings page that has just shown the version;
 *   - a failed probe is not kept: the next caller asks again.
 *
 * Module state on purpose (a static export has no server to hold it), and a client
 * module: it is not exercised by `node --test`, which is why the *judgement* about what
 * a probe result may say lives in lib/shell.ts instead.
 */

import { getRuntimeCapabilities, type RuntimeCapabilities } from "@/lib/control-plane";

const TTL_MS = 60_000;

/** What subscribers hear: an answer, or that the latest ask failed. */
export type ProbeOutcome =
  | { ok: true; value: RuntimeCapabilities }
  | { ok: false };

/**
 * Thrown to a caller whose ask was overtaken by a forced one before it answered. Its
 * answer is older than one the person explicitly demanded, so it is not handed back —
 * handing it back let a slower, earlier ask overwrite the newer answer on screen. The
 * caller has nothing to show for it and nothing to report: the newer ask speaks for both.
 */
export class ProbeSupersededError extends Error {
  constructor() {
    super("runtime probe superseded by a newer ask");
    this.name = "ProbeSupersededError";
  }
}

export function isProbeSuperseded(error: unknown): boolean {
  return error instanceof ProbeSupersededError;
}

let inFlight: Promise<RuntimeCapabilities> | null = null;
let last: { at: number; value: RuntimeCapabilities } | null = null;
// Which ask is the current one. A forced ask while another is in flight starts a new
// probe and takes this number; the older probe's answer, arriving later, is neither
// cached nor broadcast — it would be older than the one the person just demanded.
let generation = 0;
const listeners = new Set<(outcome: ProbeOutcome) => void>();

export function probeRuntimes({ force = false }: { force?: boolean } = {}): Promise<RuntimeCapabilities> {
  if (!force && last !== null && Date.now() - last.at < TTL_MS) return Promise.resolve(last.value);
  if (!force && inFlight !== null) return inFlight;
  // A forced probe is a statement that the cached answer is not to be trusted. It is
  // dropped before asking, not after: if the new probe fails, the next ordinary call
  // must ask again rather than hand back the answer the person just refused.
  if (force) last = null;
  const mine = ++generation;
  const ask = getRuntimeCapabilities().then(
    (value) => {
      if (mine !== generation) throw new ProbeSupersededError();
      last = { at: Date.now(), value };
      inFlight = null;
      for (const listener of listeners) listener({ ok: true, value });
      return value;
    },
    (error: unknown) => {
      if (mine !== generation) throw new ProbeSupersededError();
      inFlight = null;
      // Told, not swallowed: a bar showing a version from before a failed forced
      // refresh would be showing an answer the cache has already disowned.
      for (const listener of listeners) listener({ ok: false });
      throw error;
    },
  );
  inFlight = ask;
  return ask;
}

/** Hear every outcome of the current ask that lands after subscribing; returns the unsubscribe. */
export function subscribeRuntimes(listener: (outcome: ProbeOutcome) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
