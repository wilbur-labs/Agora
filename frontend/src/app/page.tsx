"use client";

import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  FileCheck2,
  GitBranch,
  Hand,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

const workflowSteps = [
  "project",
  "task",
  "stage",
  "run",
  "artifact",
  "gate",
  "handoff",
] as const;

const guaranteeItems = [
  { key: "state", icon: ShieldCheck },
  { key: "evidence", icon: FileCheck2 },
  { key: "human", icon: Hand },
] as const;

export default function LandingPage() {
  const { t } = useI18n();
  return (
    <main className="min-h-screen bg-background text-foreground">
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <Link href="/" className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground">
            <Workflow className="size-5" />
          </span>
          <span>
            <span className="block text-lg font-bold leading-tight">{t("common.brand.name")}</span>
            <span className="block text-xs text-muted-foreground">{t("common.brand.tagline")}</span>
          </span>
        </Link>
        <Link href="/portfolio" className="text-sm text-muted-foreground hover:text-foreground">
          {t("home.nav.portfolio")}
        </Link>
      </nav>

      <section className="mx-auto max-w-5xl px-6 pb-14 pt-20 text-center">
        <p className="mb-4 text-sm font-medium uppercase tracking-[0.24em] text-primary">
          {t("home.hero.eyebrow")}
        </p>
        <h1 className="text-balance text-4xl font-bold tracking-tight md:text-6xl">
          {t("home.hero.title")}
        </h1>
        <p className="mx-auto mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">
          {t("home.hero.subtitle")}
        </p>
        <div className="mt-9 flex flex-wrap justify-center gap-3">
          <Link href="/control-plane" className={buttonVariants({ size: "lg" })}>
            {t("home.hero.openControlPlane")} <ArrowRight className="size-4" />
          </Link>
          <Link
            href="/requirements"
            className={buttonVariants({ size: "lg", variant: "outline" })}
          >
            {t("home.hero.captureRequirements")}
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 pb-16">
        <div className="rounded-2xl border bg-card p-6 shadow-sm md:p-8">
          <div className="mb-5 flex items-center gap-2 text-sm font-semibold">
            <GitBranch className="size-4 text-primary" /> {t("home.mainline.title")}
          </div>
          <ol className="grid gap-3 md:grid-cols-7">
            {workflowSteps.map((step, index) => (
              <li key={step} className="relative rounded-xl border bg-background px-3 py-4 text-center text-sm font-medium">
                <span className="mb-2 block text-xs text-muted-foreground">{index + 1}</span>
                {t(`home.mainline.steps.${step}`)}
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-4 px-6 pb-20 md:grid-cols-3">
        {guaranteeItems.map(({ key, icon: Icon }) => (
          <article key={key} className="rounded-2xl border bg-card p-6">
            <span className="mb-4 grid size-10 place-items-center rounded-xl bg-primary/10 text-primary">
              <Icon className="size-5" />
            </span>
            <h2 className="font-semibold">{t(`home.guarantees.${key}.title`)}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{t(`home.guarantees.${key}.text`)}</p>
          </article>
        ))}
      </section>

      <footer className="border-t py-8 text-center text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-2">
          <CheckCircle2 className="size-4" /> {t("home.footer")}
        </span>
      </footer>
    </main>
  );
}
