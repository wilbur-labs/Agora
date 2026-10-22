"use client";

/**
 * Test connection for one registered environment, on the environment page (CR-4 slice 2,
 * second half). Each step says what it established; the check stops where the next step
 * would mean trusting a host key not yet trusted. An unknown key under TOFU is shown with
 * its fingerprint, and recorded only when the person confirms that fingerprint.
 */
import { useState } from "react";
import { RefreshCw, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  checkStepsOf, testRemoteEnvironment, TerminalAuthRequired, trustRemoteHostKey, type ConnectionCheck,
} from "@/lib/terminal";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function ConnectionCheckButton({ environmentId }: { environmentId: string }) {
  const { t } = useI18n();
  const [result, setResult] = useState<ConnectionCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true); setError(null);
    try {
      setResult(await testRemoteEnvironment(environmentId));
    } catch (err) {
      setResult(null);
      setError(err instanceof TerminalAuthRequired ? t("environments.check.authRequired") : t("environments.check.failed", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  }

  async function trust(fingerprint: string) {
    setBusy(true); setError(null);
    try {
      await trustRemoteHostKey(environmentId, fingerprint);
      setResult(await testRemoteEnvironment(environmentId));
    } catch (err) {
      setError(err instanceof TerminalAuthRequired ? t("environments.check.authRequired") : t("environments.check.trustFailed", { reason: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  }

  const steps = result ? checkStepsOf(result) : [];
  const key = result?.host_key;
  return (
    <div className="mt-2">
      <Button variant="outline" size="sm" onClick={() => void run()} disabled={busy}>
        {busy ? <RefreshCw className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
        {t("environments.check.button")}
      </Button>
      {error && <p className="mt-1 text-xs text-destructive" role="alert">{error}</p>}
      {result && (
        <ul className="mt-2 space-y-1 text-xs">
          {steps.map((step) => (
            <li key={step.id} className={cn(step.tone === "bad" ? "text-destructive" : step.tone === "warn" ? "text-amber-700 dark:text-amber-400" : step.tone === "ok" ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground")}>
              {t(`environments.check.${step.key}`, step.vars)}
            </li>
          ))}
          {key && "fingerprint" in key && key.status === "unknown" && key.mode === "tofu" && (
            <li className="pt-1">
              <span className="block font-mono text-[0.6875rem]">{key.algorithm} {key.fingerprint}</span>
              <span className="block text-muted-foreground">{t("environments.check.confirmHint")}</span>
              <Button size="sm" className="mt-1" onClick={() => void trust(key.fingerprint)} disabled={busy}>
                {t("environments.check.trust")}
              </Button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
