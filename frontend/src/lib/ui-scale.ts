/**
 * Interface text size (CR-37: "文字太小了 看的费劲"). One choice scales the whole page —
 * every size is in rem — and the terminal's font with it. Kept per browser.
 */
export const UI_SCALE_KEY = "agora.uiScale";
export const UI_SCALES = { normal: 1, large: 1.125, xlarge: 1.25 } as const;
export type UiScale = keyof typeof UI_SCALES;
export const DEFAULT_UI_SCALE: UiScale = "large";

export function uiScaleOf(raw: string | null | undefined): UiScale {
  return raw === "normal" || raw === "large" || raw === "xlarge" ? raw : DEFAULT_UI_SCALE;
}

export function nextUiScale(current: UiScale): UiScale {
  return current === "normal" ? "large" : current === "large" ? "xlarge" : "normal";
}

/** The terminal's font size in px for a scale (xterm measures in px, not rem). At the
 *  default scale it is 14, Tabby's default (the owner's Tabby sets no size; 11-04: "终端里面
 *  的文字有点大"), and it grows and shrinks with the page from there. */
export function terminalFontSize(scale: UiScale): number {
  return Math.round(14 * UI_SCALES[scale] / UI_SCALES[DEFAULT_UI_SCALE]);
}

export function readUiScale(): UiScale {
  try { return uiScaleOf(window.localStorage.getItem(UI_SCALE_KEY)); } catch { return DEFAULT_UI_SCALE; }
}

export function applyUiScale(scale: UiScale): void {
  document.documentElement.style.setProperty("--ui-scale", String(UI_SCALES[scale]));
  try { window.localStorage.setItem(UI_SCALE_KEY, scale); } catch { /* the page still changes */ }
}
