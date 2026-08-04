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

const workflow = [
  "Project",
  "Task",
  "Stage",
  "Run",
  "Artifact / Evidence",
  "Gate",
  "Handoff / Done",
];

const guarantees = [
  {
    icon: ShieldCheck,
    title: "Agora owns workflow state",
    text: "Native runtime output is evidence or a proposal. Only Agora may change cross-runtime Task, Stage, and Gate state.",
  },
  {
    icon: FileCheck2,
    title: "Evidence-bound quality gates",
    text: "Process exit, transport, schema validity, and semantic results stay separate. Exit code zero is never enough.",
  },
  {
    icon: Hand,
    title: "Explicit human decisions",
    text: "Consultation produces one advisory candidate. Adoption, rejection, approval, and escalation remain explicit actions.",
  },
];

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <Link href="/" className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground">
            <Workflow className="size-5" />
          </span>
          <span>
            <span className="block text-lg font-bold leading-tight">Agora</span>
            <span className="block text-xs text-muted-foreground">Delivery Control Plane</span>
          </span>
        </Link>
        <Link href="/portfolio" className="text-sm text-muted-foreground hover:text-foreground">
          Portfolio
        </Link>
      </nav>

      <section className="mx-auto max-w-5xl px-6 pb-14 pt-20 text-center">
        <p className="mb-4 text-sm font-medium uppercase tracking-[0.24em] text-primary">
          Durable AI-assisted delivery
        </p>
        <h1 className="text-balance text-4xl font-bold tracking-tight md:text-6xl">
          One authoritative workflow from Task to done
        </h1>
        <p className="mx-auto mt-6 max-w-3xl text-lg leading-8 text-muted-foreground">
          Agora coordinates Codex, Claude Code, and Kiro through versioned Stages,
          bounded Context and Handoff Packs, truthful budgets, independent review,
          and evidence-backed Gates. It does not run an autonomous AI debate.
        </p>
        <div className="mt-9 flex flex-wrap justify-center gap-3">
          <Link href="/control-plane" className={buttonVariants({ size: "lg" })}>
            Open Control Plane <ArrowRight className="size-4" />
          </Link>
          <Link
            href="/requirements"
            className={buttonVariants({ size: "lg", variant: "outline" })}
          >
            Capture requirements
          </Link>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 pb-16">
        <div className="rounded-2xl border bg-card p-6 shadow-sm md:p-8">
          <div className="mb-5 flex items-center gap-2 text-sm font-semibold">
            <GitBranch className="size-4 text-primary" /> Authoritative product mainline
          </div>
          <ol className="grid gap-3 md:grid-cols-7">
            {workflow.map((step, index) => (
              <li key={step} className="relative rounded-xl border bg-background px-3 py-4 text-center text-sm font-medium">
                <span className="mb-2 block text-xs text-muted-foreground">{index + 1}</span>
                {step}
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-4 px-6 pb-20 md:grid-cols-3">
        {guarantees.map(({ icon: Icon, title, text }) => (
          <article key={title} className="rounded-2xl border bg-card p-6">
            <span className="mb-4 grid size-10 place-items-center rounded-xl bg-primary/10 text-primary">
              <Icon className="size-5" />
            </span>
            <h2 className="font-semibold">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{text}</p>
          </article>
        ))}
      </section>

      <footer className="border-t py-8 text-center text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-2">
          <CheckCircle2 className="size-4" /> Local-first, inspectable, and resumable
        </span>
      </footer>
    </main>
  );
}
