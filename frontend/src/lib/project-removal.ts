/**
 * Removing a project from Agora (CR-56). User, 2026-10: "Agora 不应该再出现在项目里了".
 *
 * Removal is the registration only — the server deletes no file, not even the `.agora/`
 * folders registering made. It refuses, with its reason and the counts, when the project
 * is the last one, when tasks / runs / other rows still name it, or when the import file
 * would bring it back. This module holds the two decisions the screen has to make and
 * nothing else: what to say when it was refused, and what to forget once it succeeded.
 */

/** Per-viewer convenience: the project the workbench last showed (localStorage). Here
 *  rather than in the workbench page so a removal elsewhere can forget it. */
export const LAST_PROJECT_KEY = "agora.workspace.project";

/** What the server said, verbatim, when it refused (404 / 409 carry the reason and the
 *  counts — rewording them would lose the numbers). Anything else is the failure as it
 *  arrived: a network error is not a refusal and is not dressed up as one.
 *
 *  Read by shape (an `ApiError` has a numeric `status`), not `instanceof`, so this
 *  module stays importable by the node tests, which cannot load control-plane.ts. */
export function removalErrorOf(err: unknown): { refused: boolean; message: string } {
  if (err instanceof Error && typeof (err as { status?: unknown }).status === "number") {
    const status = (err as Error & { status: number }).status;
    return { refused: status === 404 || status === 409, message: err.message };
  }
  return { refused: false, message: err instanceof Error ? err.message : String(err) };
}

/** The slice of `Storage` this needs; a parameter so it can be tested without a DOM. */
export interface RememberedProjectStore {
  getItem(key: string): string | null;
  removeItem(key: string): void;
}

/**
 * After a removal: if the workbench would come back to the removed project, forget it,
 * so the next arrival falls back to the control plane's current project — which the
 * server has already moved off the removed one. Returns whether anything was forgotten.
 * Storage that throws (private mode, blocked site data) is a convenience lost, not an
 * error: the workbench then has nothing remembered and uses the same fallback.
 */
export function forgetRemovedProject(storage: RememberedProjectStore | null, removedId: string): boolean {
  if (!storage) return false;
  try {
    if (storage.getItem(LAST_PROJECT_KEY) !== removedId) return false;
    storage.removeItem(LAST_PROJECT_KEY);
    return true;
  } catch {
    return false;
  }
}
