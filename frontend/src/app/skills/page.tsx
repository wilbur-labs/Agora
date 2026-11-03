"use client";

/**
 * Skills — mockup v2, screen 4, with nothing invented.
 *
 * The mockup shows eighteen skills in three tiers, grouped by whether they are
 * safe to publish. **Agora reads none of them.** `backend/agora/skills/` is an
 * empty package: the v0.1–0.4 extractor was deleted with the council-era code,
 * and no reader replaced it. The skills in that mockup are files in the user's
 * claude-workspace (`lessons/*.md`) that no Agora endpoint has ever opened.
 *
 * So this screen shows the tier model — which is a real decision, recorded — and
 * says plainly that the inventory is not read yet, where the files actually live,
 * and what building it would take. A page of plausible-looking rows would be the
 * one thing this project forbids: a product stating what its reads do not support.
 */

import { FileWarning, Library } from "lucide-react";

import { DeliveryShell } from "@/components/delivery-shell";
import { useI18n } from "@/lib/i18n";

export default function SkillsPage() {
  const { t } = useI18n();
  return (
    <DeliveryShell active="Skills">
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <header className="space-y-1">
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Library className="size-6" />
            {t("skills.title")}
          </h1>
          <p className="text-sm text-muted-foreground">{t("skills.subtitle")}</p>
        </header>

        <section className="rounded-xl border-2 border-dashed border-amber-500/40 bg-amber-500/5 p-5">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <FileWarning className="size-4 text-amber-600 dark:text-amber-400" />
            {t("skills.notRead.title")}
          </h2>
          <p className="mt-2 text-sm leading-relaxed">{t("skills.notRead.body")}</p>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {t("skills.notRead.where")}
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            {t("skills.notRead.whatItWouldTake")}
          </p>
        </section>

        {/* The decisions that survive from the mockup review, so they are not
            re-argued from scratch when the reader is built. */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            {t("skills.decisions.title")}
          </h2>
          {(["tiers", "publish", "usage", "noAutoExtract"] as const).map((key) => (
            <div key={key} className="rounded-lg border bg-card p-4">
              <h3 className="text-sm font-semibold">{t(`skills.decisions.${key}.title`)}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {t(`skills.decisions.${key}.body`)}
              </p>
            </div>
          ))}
        </section>

      </div>
    </DeliveryShell>
  );
}
