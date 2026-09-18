/**
 * What a project-nav item is allowed to say about itself.
 *
 * The left column of the project workbench lists eight resources (overview, runs,
 * goals, change requests, decisions, known defects, skills, files) and the mockup
 * draws a count beside most of them. Three of those counts have nothing behind them
 * today: skills is an empty package left over from v0.4, files has no read at all,
 * and the known defects live as prose in a workspace RESUME that this repository
 * cannot see. Drawing a number there would be inventing one; drawing a greyed-out
 * row with no explanation would be the same product saying "cannot read", "do not
 * know" and "does not exist" in one voice, which is the one thing the constitution
 * forbids without exception.
 *
 * So a nav item does not carry a number. It carries one of these, and each variant
 * is a different sentence:
 *
 *   count       — a read succeeded and this is the answer
 *   none        — a read succeeded and the answer is genuinely nothing
 *   unbuilt     — the feature does not exist, so there is nothing to read
 *   noReader    — the thing exists, but this product cannot read it yet
 *   unreadable  — a read was attempted and failed, and this is why
 *   unreported  — this server never sent the field: a fact about the server
 *
 * `none` is separate from `count: 0` on purpose, following the rule the backend
 * payloads already follow — never carry a zero you do not mean. `unreported` is
 * separate from `unreadable` for the reason lib/control-plane.ts states about its
 * optional dashboard fields: a server older than this UI reports nothing, and that
 * must not be dressed up as a fact about the project.
 *
 * This module maps the *existing* judgements onto those variants; it does not
 * re-decide anything. `lib/cr-log.ts` already classifies a CR Log
 * (`CRLogView`), and the decisions payload already reports its own reach. Adding a
 * second opinion about the same reads is how two answers to one question appear,
 * which this codebase has paid for before.
 *
 * Deliberately free of React and of "@/" path aliases: `node --test` executes the
 * companion test file (tests/nav-capability.test.mts) directly.
 */
import type { CRLogView } from "./cr-log.ts";

/** Why a read that was attempted could not answer. Same vocabulary the reads use. */
export type NavUnreadableReason = "too_large" | "unreadable" | "malformed";

/**
 * Something that qualifies a count without contradicting it.
 *
 * A count with rows the reader could not classify is still a count, but it is not
 * the whole story, and the number of unclassified rows is the reason. Kept beside
 * the count rather than folded into it — the same reason `lib/cr-log.ts` keeps
 * `unrecognised` out of `open`, and the reason the decisions payload refuses to
 * report a total without the number of records it could not open.
 */
export interface NavCaveat {
  /** Rows read but not classifiable (CR Log statuses outside the known set). */
  kind: "unclassified" | "unopenable";
  count: number;
}

/** Why this product has no read path for something that does exist. */
export type NavNoReadReason =
  /** The reader was never built (e.g. a project's files are on disk, unread). */
  | "reader_unbuilt"
  /** The facts exist, but not in a form this repository can read (e.g. the known
   *  defects live as prose in a workspace RESUME, with no structured source here). */
  | "no_structured_source";

export type NavCapability =
  | { kind: "count"; value: number; caveat: NavCaveat | null }
  | { kind: "none" }
  /** `milestone` names where it is planned — "not built" is easier to accept when the
   *  product can say when it will be. Required: every unbuilt item this UI shows has a
   *  milestone, and an optional field with no producer is an invitation to invent one. */
  | { kind: "unbuilt"; milestone: string }
  /** Carries its reason: two items can both be unreadable for different reasons, and a
   *  shared variant would merge them again by the time the key is computed. */
  | { kind: "noReader"; reason: NavNoReadReason }
  | { kind: "unreadable"; reason: NavUnreadableReason }
  | { kind: "unreported" };

/**
 * The decisions payload, restated with only the fields this judgement reads.
 *
 * Restated rather than imported from lib/control-plane.ts because that module
 * imports through the "@/" alias, which `node --test` cannot resolve — the same
 * constraint that made lib/cr-log.ts restate its own inputs. Keep in step with
 * `ProjectDecisions` there.
 *
 * Every field is required, matching that contract: decisions are their own request, so
 * unlike the dashboard's optional fields there is no "a server older than this UI sends
 * nothing" case to model. That matters — an optional field here would invite a `?? 0`,
 * and the safe reading of a missing number is not zero.
 */
export interface DecisionsLike {
  available: boolean;
  /** "absent" | "unreadable" when unavailable; null when the server named no reason. */
  reason: string | null;
  /** null — not [] — when unavailable. */
  records: readonly unknown[] | null;
  /** Records found but not openable. null when unavailable; travels with `records` so a
   *  count can never be shown without it. */
  unreadable_count: number | null;
}

/** This server did not report the field at all. */
export const NAV_UNREPORTED: NavCapability = { kind: "unreported" };

/**
 * Skills: `backend/agora/skills/` is an empty package, a remnant of the retired
 * v0.4 self-learning design. There is no registry to count, by construction rather
 * than by failure, and building one is milestone W5.
 */
export const NAV_SKILLS_UNBUILT: NavCapability = { kind: "unbuilt", milestone: "W5" };

/** Files: the project's files are on disk; this product has no read for them yet. */
export const NAV_FILES_NO_READER: NavCapability = { kind: "noReader", reason: "reader_unbuilt" };

/**
 * Known defects: they are real — `.agora/development/PROGRESS.md` records them as
 * D-1..D-6 in a workspace RESUME — but no structured source exists in this
 * repository yet, so there is nothing here to read. Not `unbuilt`: the product intends
 * to show these and the data exists; what is missing is the source in this repo (W9
 * step B). Its own reason, so it never reads as "we never built this".
 */
export const NAV_DEFECTS_NO_SOURCE: NavCapability = {
  kind: "noReader",
  reason: "no_structured_source",
};

/** A read that was attempted and failed. */
export function navReadFailed(reason: NavUnreadableReason = "unreadable"): NavCapability {
  return { kind: "unreadable", reason };
}

/**
 * A CR Log's nav capability, from the view lib/cr-log.ts already produced.
 *
 * The count is the number of rows positively read as OPEN, not the row total: the
 * nav is an action signal, and a closed CR asks nothing of anyone. Rows whose status
 * could not be classified ride along as a caveat instead of being counted as open,
 * because they are precisely the reason the open count may not be the whole story.
 */
export function navFromCRLog(view: CRLogView): NavCapability {
  switch (view.kind) {
    case "rows":
      return {
        kind: "count",
        value: view.open,
        caveat: view.unrecognised > 0 ? { kind: "unclassified", count: view.unrecognised } : null,
      };
    case "none":
      return { kind: "none" };
    case "unknown":
      // "unreported" is the server not sending the field; the other three are reads
      // that were made and failed. Different sentences, so different variants.
      return view.reason === "unreported" ? NAV_UNREPORTED : navReadFailed(view.reason);
  }
}

/**
 * A decision record set's nav capability.
 *
 * `available: false` carries a reason of "absent" — the project keeps no decisions
 * directory — which is a settled nothing, not a failure. Anything else that comes
 * back unavailable is a read that did not work.
 *
 * When records are readable, the count is how many were opened, and records that
 * could not be opened travel as a caveat: the payload deliberately refuses to report
 * a total without them, so neither does this.
 */
export function navFromDecisions(payload: DecisionsLike): NavCapability {
  if (!payload.available) {
    // Only "absent" is a settled nothing. A missing reason is not: it means nobody said
    // why, and reading that as "the project keeps no decisions" would turn our ignorance
    // into a fact about the project.
    return payload.reason === "absent" ? { kind: "none" } : navReadFailed();
  }
  const { records, unreadable_count: unopenable } = payload;
  // Available, so the contract says both of these travel together and are non-null. A
  // payload that contradicts its own contract is a failed read — deliberately not
  // `unopenable ?? 0`, because the safe reading of a missing number is not zero, and a
  // zero here would let a count be shown as if its completeness had been confirmed.
  if (records == null || unopenable == null) {
    return navReadFailed();
  }
  if (records.length === 0 && unopenable === 0) return { kind: "none" };
  return {
    kind: "count",
    value: records.length,
    caveat: unopenable > 0 ? { kind: "unopenable", count: unopenable } : null,
  };
}

/**
 * The defects payload, restated with only the fields this judgement reads.
 *
 * Restated rather than imported from lib/control-plane.ts for the same reason as
 * DecisionsLike: that module imports through the "@/" alias, which `node --test`
 * cannot resolve. Keep in step with `ProjectDefects` there. Every field required —
 * defects are their own request, so there is no "a server older than this UI sends
 * nothing" case, and an optional field would invite a `?? 0` whose safe reading is
 * not zero.
 */
export interface DefectsLike {
  available: boolean;
  /** "absent" | "unreadable" | ... when unavailable; null when the server named no reason. */
  reason: string | null;
  /** null — not [] — when unavailable. */
  records: readonly unknown[] | null;
  /** How many read positively as open. null when unavailable; the nav signal. */
  open_count: number | null;
  /** Entries whose status word was outside the vocabulary. Travels with the count. */
  unrecognised_count: number | null;
  /** Entries that could not be read at all (missing a required field, or not a mapping).
   *  A file of only these is not "no defects" — it is defects the reader could not read. */
  unreadable_count: number | null;
}

/**
 * A defect set's nav capability.
 *
 * The count is the **open** count, not the record total: the nav is an action signal
 * and a fixed defect asks nothing of anyone — the same rule `navFromCRLog` follows for
 * a closed CR. Entries whose status could not be classified, and entries the reader
 * could not read at all, ride along as a caveat rather than being counted as open —
 * they are exactly the reason the open count may not be the whole story.
 *
 * `available: false` with reason "absent" is a settled nothing (the project keeps no
 * defect list); any other unavailable is a read that did not work. Deliberately not
 * `open_count ?? 0` on a contradictory payload — the safe reading of a missing number
 * is not zero. And a file whose entries the reader could only fail to read is not a
 * settled "nothing owed": the reader kept those rows precisely so the UI would not
 * claim there are none. So `unreadable_count` gates the settled-none answer too, the
 * same way the decisions reader refuses a total without its unopenable count.
 */
export function navFromDefects(payload: DefectsLike): NavCapability {
  if (!payload.available) {
    return payload.reason === "absent" ? { kind: "none" } : navReadFailed();
  }
  const {
    records, open_count: open, unrecognised_count: unrecognised,
    unreadable_count: unreadable,
  } = payload;
  // Available, so the contract says these travel together and are non-null. A payload
  // that contradicts its own contract is a failed read.
  if (records == null || open == null || unrecognised == null || unreadable == null) {
    return navReadFailed();
  }
  // A settled "nothing owed" requires all three to be zero: no open defect, none
  // unclassified, and — the case this gate exists for — none the reader could not read.
  // A file of only-fixed defects is `none`; a file of only-unreadable entries is not.
  if (open === 0 && unrecognised === 0 && unreadable === 0) return { kind: "none" };
  // One caveat slot, so surface the more severe qualifier: an entry the reader could
  // not open at all (unopenable, the word the decisions reader uses) outranks one whose
  // status word was merely outside the vocabulary (unclassified).
  const caveat: NavCaveat | null =
    unreadable > 0 ? { kind: "unopenable", count: unreadable }
    : unrecognised > 0 ? { kind: "unclassified", count: unrecognised }
    : null;
  return { kind: "count", value: open, caveat };
}

/**
 * Whether this capability may be rendered as a number.
 *
 * The one question a nav row asks before drawing a count. Only `count` may — and
 * note that `none` may not, which is the point: "no change requests" is a sentence,
 * not a zero.
 *
 * A type predicate, so a caller that asks the question also gets the narrowed variant.
 * Without that, every call site re-tests `kind === "count"` to reach `value`, which is the
 * judgement moving back out of this module one component at a time.
 */
export function countSayable(
  cap: NavCapability,
): cap is Extract<NavCapability, { kind: "count" }> {
  return cap.kind === "count";
}

/**
 * Whether this capability was derived from the project, as opposed to from the limits
 * of this product, this repository or this server.
 *
 * `count` and `none` came from the project. The other four came from us: we have not
 * built it, we cannot read it, the read failed, the server did not answer. Useful for
 * deciding whether a row is reporting on its subject at all — but note this does NOT
 * license an absence claim; see `supportsAbsenceClaim`.
 */
export function isProjectDerived(cap: NavCapability): boolean {
  return cap.kind === "count" || cap.kind === "none";
}

/**
 * Whether the product may say "there are none" on the strength of this.
 *
 * Stricter than `isProjectDerived` on purpose. A zero that arrives with a caveat is not
 * an absence: `count: 0` beside two records nobody could open means the decisions may
 * well exist and simply could not be read, and summarising that as "no decisions" is the
 * exact substitution the constitution forbids.
 */
export function supportsAbsenceClaim(cap: NavCapability): boolean {
  if (cap.kind === "none") return true;
  return cap.kind === "count" && cap.value === 0 && cap.caveat === null;
}

/**
 * The i18n key for the sentence this capability makes.
 *
 * Returned as a key rather than text so the mapping stays testable without a DOM
 * and without the i18n runtime. Callers interpolate `value` / `caveat.count`
 * themselves; the key names which sentence, not its numbers.
 */
export function navCapabilityKey(cap: NavCapability): string {
  const base = "workspace.nav.capability";
  switch (cap.kind) {
    case "count":
      return cap.caveat ? `${base}.countWithCaveat.${cap.caveat.kind}` : `${base}.count`;
    case "none":
      return `${base}.none`;
    case "unbuilt":
      return `${base}.unbuiltWithMilestone`;
    case "noReader":
      return `${base}.noReader.${cap.reason}`;
    case "unreadable":
      return `${base}.unreadable.${cap.reason}`;
    case "unreported":
      return `${base}.unreported`;
  }
}
