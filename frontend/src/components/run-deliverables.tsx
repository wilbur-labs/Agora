"use client";

/**
 * The files one run delivered, as a list that can be opened — and every way of saying
 * "there is no list here" kept apart, because they are different facts:
 *
 *   not asked     the caller has not requested the list (a button offers to)
 *   loading       requested, not answered
 *   failed        requested, the read failed — not "no files"
 *   none          the run ended without a snapshot; its files are unknown and are NOT
 *                 guessed from the worktree
 *   live          the worktree right now — only meaningful while the run is active
 *   snapshot      what the run actually delivered; may be truncated, may be unopenable
 *                 because the worktree is gone
 *
 * Shared by the conversation's turn cards and the right column's settled-run block so
 * one fact reads the same on both; `canOpenDeliverable` (lib/execution.ts) is the one
 * rule for whether a path can be opened.
 */

import { useState } from "react";

import type { Translate } from "@/lib/i18n";
import { ApiError } from "@/lib/control-plane";
import {
  canOpenDeliverable,
  deliverableRawUrl,
  getDeliverableContent,
  type Deliverable,
  type RunDeliverables,
} from "@/lib/execution";

export type DeliverablesRead = RunDeliverables | "loading" | "failed";

export function DeliverablesBlock({
  runId, delivered, onLoad, t, compact = false,
}: {
  runId: string;
  /** `null` when not requested yet; the caller decides when to ask. */
  delivered: DeliverablesRead | null;
  /** How to ask. A caller that always asks up front (the right column) passes none. */
  onLoad?: () => void;
  t: Translate;
  compact?: boolean;
}) {
  if (delivered === null) {
    return onLoad ? (
      <button onClick={onLoad} className="text-[0.75rem] font-medium text-primary hover:underline">
        {t("workspace.timeline.run.showFiles")}
      </button>
    ) : (
      <p className="text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.loadingFiles")}</p>
    );
  }
  if (delivered === "loading") {
    return <p className="text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.loadingFiles")}</p>;
  }
  if (delivered === "failed") {
    // A failed read, and the way out of it. Without the button a transient failure at
    // prefetch time was the answer for the rest of the session: nothing else asked again.
    return (
      <p className="flex flex-wrap items-center gap-2 text-[0.75rem] text-muted-foreground">
        {t("workspace.panels.unavailable")}
        {onLoad && (
          <button onClick={onLoad} className="font-medium text-primary hover:underline">
            {t("workspace.timeline.run.retryFiles")}
          </button>
        )}
      </p>
    );
  }
  if (delivered.source === "none") {
    // "Not recorded" is what the server said at the moment it was asked. A run that
    // settled a moment ago may still be having its snapshot written, so the way to ask
    // again is offered here too — a re-read is cheap, and a list frozen at "unrecorded"
    // for the rest of the session would hide files the run did deliver.
    return (
      <p className="flex flex-wrap items-center gap-2 text-[0.75rem] text-muted-foreground">
        {t("workspace.timeline.run.noSnapshot")}
        {onLoad && (
          <button onClick={onLoad} className="font-medium text-primary hover:underline">
            {t("workspace.timeline.run.retryFiles")}
          </button>
        )}
      </p>
    );
  }
  if (delivered.files.length === 0) {
    return <p className="text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.noFiles")}</p>;
  }
  return (
    <div>
      {!compact && (
        <p className="mb-1 text-[0.75rem] font-medium text-muted-foreground">
          {t("workspace.timeline.run.files", { count: delivered.files.length })}
          {delivered.source === "live" && (
            <span className="ml-2 font-normal">{t("workspace.timeline.run.liveFiles")}</span>
          )}
        </p>
      )}
      {/* A turn that scaffolded a project delivered forty files, and forty rows filled
          the whole conversation (user, 2026-09-20). The list keeps its own scroll
          instead: every row is still there, none of them is the page. */}
      <ul className="max-h-56 space-y-0.5 overflow-y-auto text-xs">
        {delivered.files.map((file) => (
          <FileRow key={file.path} runId={runId} file={file} openable={canOpenDeliverable(delivered, file)} t={t} />
        ))}
      </ul>
      {/* Only over a whole list: a capped snapshot holds "at least these", and printing
          its length as the total is the exact-number-from-a-partial-read this file
          exists to refuse. The cap's own sentence below says what is missing. */}
      {compact && !delivered.truncated && delivered.files.length > 1 && (
        <p className="mt-1 text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.files", { count: delivered.files.length })}</p>
      )}
      {compact && delivered.source === "live" && (
        <p className="mt-1 text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.liveFiles")}</p>
      )}
      {delivered.content_available === false && (
        <p className="mt-1 text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.gone")}</p>
      )}
      {delivered.truncated && (
        <p className="mt-1 text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.partial")}</p>
      )}
    </div>
  );
}

export function FileRow(
  { runId, file, openable = true, t }:
  { runId: string; file: Deliverable; openable?: boolean; t: Translate },
) {
  // What the preview holds: not asked, in flight, the text (and whether the server cut
  // it), the server's refusal because the file no longer holds what this run delivered
  // (a 409 — a later run rewrote it), or any other failure. The last two are different
  // sentences, and only the last is worth asking again: a rewritten file stays rewritten.
  type Preview =
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "text"; content: string; truncated: boolean }
    | { kind: "changedSince" }
    | { kind: "failed" };
  const [preview, setPreview] = useState<Preview>({ kind: "idle" });
  const [open, setOpen] = useState(false);

  const load = async () => {
    setPreview({ kind: "loading" });
    try {
      const got = await getDeliverableContent(runId, file.path);
      setPreview({ kind: "text", content: got.content, truncated: got.truncated });
    } catch (error) {
      setPreview(error instanceof ApiError && error.status === 409 ? { kind: "changedSince" } : { kind: "failed" });
    }
  };

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    // Idle, or a failure that is worth another try: a transient failure must not be the
    // preview's answer for the rest of the session.
    if (preview.kind === "idle" || preview.kind === "failed") await load();
  };

  return (
    <li className="rounded bg-muted/40">
      <div className="flex items-center gap-2 px-2 py-0.5 font-mono">
        <span className="shrink-0 text-[0.6875rem] uppercase text-muted-foreground">{file.status}</span>
        {openable ? (
          <>
            <button onClick={() => void toggle()} className="min-w-0 flex-1 truncate text-left hover:underline">{file.path}</button>
            <button onClick={() => void toggle()} className="shrink-0 font-sans text-[0.75rem] font-medium text-primary hover:underline">{open ? t("workspace.timeline.run.hideFile") : t("workspace.timeline.run.viewFile")}</button>
            <a href={deliverableRawUrl(runId, file.path)} target="_blank" rel="noopener noreferrer" className="shrink-0 font-sans text-[0.75rem] font-medium text-primary hover:underline">{t("workspace.timeline.run.openRaw")}</a>
          </>
        ) : (
          // The worktree is gone: the path is still what the run delivered, but
          // offering a link that can only 404 would be a lie.
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{file.path}</span>
        )}
      </div>
      {open && (
        preview.kind === "loading" || preview.kind === "idle"
          ? <p className="px-2 py-1 text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.loadingFile")}</p>
          : preview.kind === "changedSince"
            ? <p className="px-2 py-1 text-[0.75rem] text-muted-foreground">{t("workspace.timeline.run.changedSince")}</p>
            : preview.kind === "failed"
              ? (
                <p className="flex flex-wrap items-center gap-2 px-2 py-1 text-[0.75rem] text-muted-foreground">
                  {t("workspace.timeline.run.previewUnavailable")}
                  <button onClick={() => void load()} className="font-medium text-primary hover:underline">
                    {t("workspace.timeline.run.retryFiles")}
                  </button>
                </p>
              )
              : (
                <>
                  <pre className="max-h-72 overflow-auto whitespace-pre rounded-b bg-zinc-950 p-3 text-[0.75rem] leading-relaxed text-zinc-100">{preview.content}</pre>
                  {/* The server cut the preview at its limit. Said, so the half on screen
                      is not approved as the whole file. */}
                  {preview.truncated && (
                    <p className="px-2 py-1 text-[0.75rem] text-amber-700 dark:text-amber-400">{t("workspace.timeline.run.previewTruncated")}</p>
                  )}
                </>
              )
      )}
    </li>
  );
}
