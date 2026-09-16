/**
 * Which attention item a "needs you" event in the conversation refers to, and whether
 * it is still waiting — so the conversation can show the item and take the answer
 * where the person is looking (CR-74).
 *
 * The failure this replaces: a reviewer's 3,500-character verdict sat in an attention
 * item whose only rendering was the Attention page, while the conversation printed the
 * two words "needs you" with nothing behind them. The answer existed, was readable, and
 * was one page away from the person asking for it.
 *
 * Pure. The component renders the variant; nothing here is inferred from the event's
 * presence alone. Six answers, because they are six different sentences:
 *
 *   - `notAGate`   — the event does not open an item; nothing to show.
 *   - `loading`    — the open-items read has not landed.
 *   - `unreadable` — the read failed. Not "settled": an item may well be waiting.
 *   - `unknown`    — the read was cut at the page limit and the item is not in the page,
 *                    or the event names no item. "Not in a page that may be truncated"
 *                    is not "not there".
 *   - `settled`    — the read is whole and the item is not among the open ones, so it
 *                    was answered, cancelled or expired. The later event says which.
 *   - `open`       — the item, still waiting, with everything needed to answer it.
 */
import { ATTENTION_RAISE_EVENTS } from "./timeline.ts";
import type { AttentionLike, TaskAttentionView } from "./task-attention.ts";

export interface GateEventLike {
  event_type: string;
  payload?: Record<string, unknown> | null;
}

/** What is needed here, plus what `TaskAttentionView` needs of its items. */
export interface GateItemLike extends AttentionLike {
  item_id: string;
  kind: string;
}

export type InlineGate<T extends GateItemLike> =
  | { kind: "notAGate" }
  | { kind: "loading" }
  | { kind: "unreadable" }
  | { kind: "unknown" }
  | { kind: "settled" }
  | { kind: "open"; item: T };

export function inlineGateOf<T extends GateItemLike>(
  event: GateEventLike,
  view: TaskAttentionView<T>,
): InlineGate<T> {
  if (!ATTENTION_RAISE_EVENTS.includes(event.event_type)) return { kind: "notAGate" };
  const raw = (event.payload ?? {})["item_id"];
  const itemId = typeof raw === "string" && raw !== "" ? raw : null;
  if (view.kind === "loading") return { kind: "loading" };
  if (view.kind === "unreadable") return { kind: "unreadable" };
  // Without an id there is nothing to match on, whatever the read returned.
  if (itemId === null) return { kind: "unknown" };
  if (view.kind === "none") return { kind: "settled" };
  const item = view.items.find((candidate) => candidate.item_id === itemId);
  if (item !== undefined) return { kind: "open", item };
  return view.kind === "partial" ? { kind: "unknown" } : { kind: "settled" };
}

/** The action an item of this kind is answered with; approvals also allow "reject". */
export type GateAction = "approve" | "reject" | "answer";

export function defaultGateAction(kind: string): GateAction {
  return kind === "approval" ? "approve" : "answer";
}

/**
 * Whether a response may be sent: an approval needs no words; anything else is an
 * answer, and an empty answer would deliver nothing to the agent waiting on it.
 */
export function gateResponseReady(action: GateAction, response: string): boolean {
  return action !== "answer" || response.trim() !== "";
}
