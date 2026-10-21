"use client";

/**
 * A "needs you" item, answered where it is read (CR-74).
 *
 * The reviewer's verdict used to be two words in the conversation — "needs you" — with
 * the 3,500 characters behind them rendered on the Attention page and nowhere else, and
 * no control on this screen that led there. Now the item's title, its words and its
 * buttons sit under the event that raised it. What it answers with is the same
 * `respondAttention` the Attention page uses, with the same version check, so the two
 * cannot drift on what a response is.
 *
 * Every state of the read gets its own sentence (lib/inline-gate.ts decides which);
 * only `open` draws a form. `settled` draws nothing, because the event that settled it
 * is the next entry in the stream and says how.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/control-plane";
import { respondAttention, type AttentionItem } from "@/lib/attention";
import {
  defaultGateAction, gateReplyReachOf, gateResponseReady,
  type GateAction, type InlineGate,
} from "@/lib/inline-gate";
import type { Translate, TranslateEnum } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** A verdict this long is folded first; the whole of it is one click away. */
const BODY_FOLD = 700;

/**
 * What the person did to an item, handed up so the page can carry on without another
 * click (CR-70 / CR-83): an approval completes the step and moves to the next; a
 * rejection sends the reviewer's words back to the worker. `conflict` means the item
 * had already changed elsewhere — nothing was recorded here, only re-read.
 */
export interface GateOutcome {
  action: GateAction | "conflict";
  item: AttentionItem;
  response: string;
}

export function InlineGateBlock({ gate, t, te, onAnswered }: {
  gate: InlineGate<AttentionItem>;
  t: Translate;
  te: TranslateEnum;
  /** Told once a response was recorded (or found already changed), so the page re-reads. */
  onAnswered: (outcome: GateOutcome) => void;
}) {
  if (gate.kind === "notAGate" || gate.kind === "settled") return null;
  if (gate.kind === "open") return <OpenGate item={gate.item} t={t} te={te} onAnswered={onAnswered} />;
  return (
    <p className="mt-1 text-[0.75rem] leading-snug text-muted-foreground">
      {t(`workspace.timeline.gate.${gate.kind}`)}
      {gate.kind !== "loading" && (
        <>
          {" "}
          <Link href="/attention" className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline">
            {t("workspace.timeline.gate.openFull")} <ExternalLink className="size-3" />
          </Link>
        </>
      )}
    </p>
  );
}

function OpenGate({ item, t, te, onAnswered }: {
  item: AttentionItem; t: Translate; te: TranslateEnum; onAnswered: (outcome: GateOutcome) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // For an approval, `reject` here means "the reason box is open" — approving is one
  // click and sends at once (the design: a single choice goes on being clicked;
  // only what needs words asks for them). Questions and blockers are always answers.
  const [action, setAction] = useState<GateAction>(defaultGateAction(item.kind));
  const [response, setResponse] = useState(item.options[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const long = item.body.length > BODY_FOLD;
  const shown = expanded || !long ? item.body : `${item.body.slice(0, BODY_FOLD)}…`;
  const ready = gateResponseReady(action, response);
  const approval = item.kind === "approval";

  const submit = async (chosen: GateAction = action) => {
    if (!gateResponseReady(chosen, response) || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await respondAttention(item.item_id, {
        action: chosen, response: response.trim(), actor: "user", expected_version: item.version,
      });
      if (!mounted.current) return;
      setNotice(t("workspace.timeline.gate.recorded"));
      onAnswered({ action: chosen, item, response: response.trim() });
    } catch (err) {
      if (!mounted.current) return;
      if (err instanceof ApiError && err.status === 409) {
        // Answered or changed elsewhere since this page read it. Say so and re-read;
        // the re-read decides whether a form is still due here.
        setNotice(t("workspace.timeline.gate.conflict"));
        onAnswered({ action: "conflict", item, response: response.trim() });
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return (
    <div className="mt-2 space-y-2 rounded-lg border bg-muted/30 p-2.5">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="rounded border px-1 text-[0.6875rem] uppercase tracking-wide text-muted-foreground">{te("attentionKind", item.kind)}</span>
        <span className="text-sm font-medium">{item.title}</span>
        <span className="text-[0.6875rem] text-muted-foreground">{te("urgency", item.urgency)}</span>
      </div>
      <p className="whitespace-pre-wrap text-xs leading-relaxed">{shown}</p>
      {long && (
        <button type="button" onClick={() => setExpanded((v) => !v)} className="flex items-center gap-1 text-[0.75rem] font-medium text-primary hover:underline">
          <ChevronDown className={cn("size-3 transition-transform", expanded && "rotate-180")} />
          {expanded ? t("workspace.timeline.gate.hideBody") : t("workspace.timeline.gate.showBody")}
        </button>
      )}
      {approval ? (
        <>
          <p className="text-[0.75rem] text-muted-foreground">{t("workspace.timeline.gate.approvalHint")}</p>
          <div className="flex flex-wrap items-center gap-2">
            {/* One click, sent at once. */}
            <Button size="sm" onClick={() => void submit("approve")} disabled={busy}>
              {busy && action === "approve" ? <RefreshCw className="size-3.5 animate-spin" /> : null}
              {t("attention.form.approve")}
            </Button>
            <Button
              size="sm"
              variant={action === "reject" ? "destructive" : "outline"}
              onClick={() => setAction(action === "reject" ? "approve" : "reject")}
              disabled={busy}
              aria-expanded={action === "reject"}
            >
              {t("attention.form.reject")}
            </Button>
          </div>
          {action === "reject" && (
            <>
              <textarea
                value={response}
                onChange={(e) => setResponse(e.target.value)}
                placeholder={t(`workspace.timeline.gate.rejectPlaceholder.${gateReplyReachOf(item, "reject")}`)}
                aria-label={t("attention.form.response")}
                disabled={busy}
                autoFocus
                className="min-h-16 w-full resize-y rounded-md border bg-background p-2 text-xs leading-relaxed outline-none focus:border-ring"
              />
              <Button size="sm" variant="destructive" onClick={() => void submit("reject")} disabled={busy}>
                {busy ? <RefreshCw className="size-3.5 animate-spin" /> : null}
                {t("workspace.timeline.gate.confirmReject")}
              </Button>
            </>
          )}
        </>
      ) : (
        <>
          {item.options.length > 0 && (
            <label className="block text-[0.75rem] text-muted-foreground">
              {t("attention.form.suggestedAnswer")}
              <select
                className="mt-1 block w-full rounded-md border bg-background p-1.5 text-xs"
                value={response}
                onChange={(e) => setResponse(e.target.value)}
                disabled={busy}
              >
                {item.options.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
          )}
          <p className="text-[0.75rem] text-muted-foreground">{t(`workspace.timeline.gate.answerHint.${gateReplyReachOf(item, "answer")}`)}</p>
          <textarea
            value={response}
            onChange={(e) => setResponse(e.target.value)}
            placeholder={t("workspace.timeline.gate.answerPlaceholder")}
            aria-label={t("attention.form.response")}
            disabled={busy}
            className="min-h-16 w-full resize-y rounded-md border bg-background p-2 text-xs leading-relaxed outline-none focus:border-ring"
          />
          <Button size="sm" onClick={() => void submit("answer")} disabled={busy || !ready}>
            {busy ? <RefreshCw className="size-3.5 animate-spin" /> : null}
            {t("attention.form.submit")}
          </Button>
        </>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/attention" className="inline-flex items-center gap-1 text-[0.75rem] font-medium text-primary hover:underline">
          {t("workspace.timeline.gate.openFull")} <ExternalLink className="size-3" />
        </Link>
        {notice && <span className="text-[0.75rem] text-muted-foreground" aria-live="polite">{notice}</span>}
        {error && <span className="text-[0.75rem] text-destructive" role="alert">{t("workspace.timeline.gate.failed", { reason: error })}</span>}
      </div>
    </div>
  );
}
