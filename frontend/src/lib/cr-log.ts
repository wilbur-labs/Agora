// The project's CR Log, as the UI is allowed to talk about it.
//
// The record itself lives in the project's own repository, not in the control plane
// (tasks/agora/adr/0004 in claude-workspace). Backend reader: projects/cr_log.py.
//
// This module exists for one reason: a change log is a drift-prevention mechanism,
// and the whole of its force is that an OPEN row is impossible to miss. So the one
// sentence this UI must never say by accident is "nothing is open here". There are
// four different ways to arrive at that sentence wrongly —
//
//   the server is older than the field  -> unknown. Not "no CR Log".
//   the file is not there               -> this project keeps no CR Log. No counts.
//   the file could not be read whole    -> unknown. Not zero.
//   the rows were read, but some of
//     them carry a status this reader
//     does not understand               -> a count of open rows that does not
//                                          cover the whole log.
//
// The types make three of those unrepresentable: only the `rows` variant carries any
// number at all, so no other outcome can render a count. The fourth is what
// `allClearSayable` is for — a log with an unclassified row can be listed, but it
// cannot be summarised as clear.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/cr-log.test.mts) directly.

/** One row, as the backend reports it. Cells are kept as written. */
export interface ChangeRequestLike {
  id: string;
  date: string;
  source: string;
  description: string;
  kind: string;
  status: string;
  decision: string;
  propagated_to: string;
  /** "open" | "decided" | "done" | "unrecognised" */
  status_token: string;
  /** one of the six CR kinds, or "unrecognised" */
  kind_token: string;
}

/**
 * The log as the dashboard reports it (backend: `projects/cr_log.py`). `rows` and
 * both counts are `null` whenever `available` is false — the payload never carries a
 * zero it does not mean.
 */
export interface CRLogLike {
  available: boolean;
  /** "absent" | "too_large" | "unreadable" | "malformed" when unavailable. */
  reason?: string | null;
  rows?: ChangeRequestLike[] | null;
  open_count?: number | null;
  unrecognised_status_count?: number | null;
  malformed_ids?: string[] | null;
  source?: string | null;
}

export type CRLogUnknownReason = "unreported" | "too_large" | "unreadable" | "malformed";

export type CRLogView =
  /** Nothing can be said about this project's CR Log. Never rendered as a count. */
  | { kind: "unknown"; reason: CRLogUnknownReason; malformedIds: string[] }
  /** The project keeps no CR Log. A settled fact, and still not a count. */
  | { kind: "none" }
  /** Read. Only this variant carries numbers. */
  | {
      kind: "rows";
      rows: ChangeRequestLike[];
      /** Rows positively read as OPEN. */
      open: number;
      /** Rows whose status this reader could not classify. Never folded into `open`
       *  and never into "closed": they are the reason `open` may not be the whole
       *  story, which is why they travel in the same variant. */
      unrecognised: number;
    };

/** The backend's token for a status it could not classify. */
export const UNRECOGNISED_STATUS = "unrecognised";

/**
 * Which of the three a row is — the only place that question is answered.
 *
 * There were two answers to it before, and only one of them got fixed: the counting
 * in `crLogViewOf` was corrected to recognise "closed" positively, while
 * `attentionRowsOf` went on treating anything that was not literally "open" or
 * "unrecognised" as closed. A row with an unknown token was then counted as
 * unclassified and silently left out of the list, so the strip reported a number and
 * showed nothing to account for it. One rule, one implementation.
 *
 * "Closed" is the only class that must be earned. Everything else this UI has not
 * been taught is unclassified, never quietly finished.
 */
export function statusClassOf(row: ChangeRequestLike): "open" | "closed" | "unrecognised" {
  if (row.status_token === "open") return "open";
  if (row.status_token === "decided" || row.status_token === "done") return "closed";
  return "unrecognised";
}

const UNKNOWN_REASONS: Record<string, CRLogUnknownReason> = {
  too_large: "too_large",
  unreadable: "unreadable",
  malformed: "malformed",
};

/**
 * What may be shown for this project's CR Log.
 *
 * `undefined` is not `absent`. A server that predates this field sends nothing, and
 * reading that as "this project keeps no CR Log" would state a fact about the
 * project on the strength of a fact about the server.
 */
export function crLogViewOf(log: CRLogLike | null | undefined): CRLogView {
  if (log == null) return { kind: "unknown", reason: "unreported", malformedIds: [] };
  if (!log.available) {
    if (log.reason === "absent") return { kind: "none" };
    const reason = UNKNOWN_REASONS[log.reason ?? ""] ?? "unreadable";
    return { kind: "unknown", reason, malformedIds: [...(log.malformed_ids ?? [])] };
  }
  // Available, so the backend sent counts. Guard anyway: a count that arrived as
  // null on an available payload is a server disagreeing with its own contract, and
  // the safe reading of a missing number is not zero.
  const rows = log.rows;
  if (!Array.isArray(rows) || typeof log.open_count !== "number"
      || typeof log.unrecognised_status_count !== "number") {
    return { kind: "unknown", reason: "unreadable", malformedIds: [] };
  }
  // The numbers this view reports are counted from the rows it is about to show, not
  // taken from the payload. Two earlier versions of this guard were each narrower
  // than the claim made for them, in the same way and for the same reason:
  //
  //   "the counts are numbers"      let `{rows: [], open_count: 1}` through, and the
  //                                 caller said the project had raised nothing.
  //   "and they are not too large"  let a row with status_token "open" through beside
  //                                 `open_count: 0`, because 0 + 0 <= 1. The screen
  //                                 then displayed an OPEN row under the words "no
  //                                 open change requests".
  //
  // A bound on a number is not agreement with it. Counting the rows removes the class
  // of defect rather than the two instances of it: what is summarised and what is
  // listed are now the same data, so they cannot contradict each other.
  // Every row is classified into exactly one of the three, and the third one is
  // everything this UI does not know — not only the token the backend happens to
  // spell "unrecognised" today. Counting `=== UNRECOGNISED_STATUS` was the same
  // mistake a third time: a row whose status_token is anything else at all (a token
  // a later backend adds, a typo, "pending") fell into neither bucket and was
  // therefore treated as closed, and one such row produced "no open change
  // requests". A closed row must be *positively* recognised as closed.
  const open = rows.filter((r) => statusClassOf(r) === "open").length;
  const unrecognised = rows.filter((r) => statusClassOf(r) === "unrecognised").length;
  // The payload's own counts are kept as a cross-check. A server that disagrees with
  // its own rows is a server this UI cannot summarise, and the safe reading of that
  // disagreement is not the reassuring half of it.
  if (log.open_count !== open || log.unrecognised_status_count !== unrecognised) {
    return { kind: "unknown", reason: "unreadable", malformedIds: [] };
  }
  return { kind: "rows", rows, open, unrecognised };
}

/**
 * Whether the UI may say "nothing is open on this project".
 *
 * True only when the log was read *and* every row in it was classified. An
 * unclassified row makes `open` a floor, and a floor stated as "all clear" is the
 * failure this whole layer is built to prevent — a claim wider than the check
 * behind it, which then reads as proof.
 */
export function allClearSayable(view: CRLogView): boolean {
  return view.kind === "rows" && view.open === 0 && view.unrecognised === 0;
}

/** What the strip says, and how loudly. Rendered by ProjectChangeLog and by nothing else. */
export interface CRLogHeadline {
  /** i18n key under `portfolio.cockpit.crLog`. */
  key: string;
  /** Substitutions for that message. */
  params: Record<string, string>;
  tone: "clear" | "attention" | "unknown";
}

/**
 * The sentence to show for this view.
 *
 * Extracted from the component on purpose. While the branching lived in JSX it could
 * not be executed by `node --test`, and the tests written for it passed with the
 * component's own logic reverted — a test that cannot fail, guarding the one claim in
 * this feature that must never be made wrongly. The component now renders what this
 * returns and decides nothing.
 *
 * Both reassuring sentences are behind `allClearSayable`, and there is exactly one
 * branch that can produce them, so the gate cannot be walked around by a future edit
 * adding a sibling condition above it.
 */
export function crLogHeadlineOf(view: CRLogView, path: string): CRLogHeadline {
  if (view.kind === "none") {
    return { key: "none", params: { path }, tone: "unknown" };
  }
  if (view.kind === "unknown") {
    const key =
      view.reason === "too_large" ? "tooLarge"
      : view.reason === "malformed" ? "malformed"
      : view.reason === "unreported" ? "unreported"
      : "unreadable";
    return { key, params: { ids: view.malformedIds.join(", ") }, tone: "unknown" };
  }
  if (allClearSayable(view)) {
    return {
      key: view.rows.length === 0 ? "empty" : "allClear",
      params: {},
      tone: "clear",
    };
  }
  return {
    key: "open",
    params: { count: String(view.open) },
    tone: view.open > 0 ? "attention" : "unknown",
  };
}

/**
 * The rows worth putting in front of someone, most recently raised first.
 *
 * Open rows come first because they are the ones that block; unclassified rows come
 * next, because "I could not tell" is closer to open than to closed and burying it
 * under the closed rows is how it stops being seen.
 */
export function attentionRowsOf(view: CRLogView): ChangeRequestLike[] {
  if (view.kind !== "rows") return [];
  // Classified by `statusClassOf` and by nothing else, so this list and the counts
  // beside it can never describe different rows.
  const rank = (row: ChangeRequestLike) => {
    const cls = statusClassOf(row);
    return cls === "open" ? 0 : cls === "unrecognised" ? 1 : 2;
  };
  return view.rows.filter((row) => rank(row) < 2).sort((a, b) => rank(a) - rank(b));
}
