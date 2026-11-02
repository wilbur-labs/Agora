/**
 * How the embedded terminal looks. The default is the owner's own Tabby setup (10-30:
 * "默认一个我当前 tabby 的样式"): the Grape colour scheme and JetBrains Mono Medium, so a
 * project's terminal in Agora reads like the terminal they already work in. Choosing
 * another style belongs to the settings page (CR-75), not here.
 *
 * Free of React and "@/" aliases so `node --test` can read it.
 */

/** xterm.js's theme fields that a scheme sets. */
export interface TerminalTheme {
  foreground: string;
  background: string;
  cursor: string;
  cursorAccent: string;
  selectionBackground: string;
  black: string; red: string; green: string; yellow: string;
  blue: string; magenta: string; cyan: string; white: string;
  brightBlack: string; brightRed: string; brightGreen: string; brightYellow: string;
  brightBlue: string; brightMagenta: string; brightCyan: string; brightWhite: string;
}

/** Tabby's "Grape" scheme, colour for colour. */
export const GRAPE: TerminalTheme = {
  foreground: "#9f9fa1",
  background: "#171423",
  cursor: "#a288f7",
  cursorAccent: "#171423",
  selectionBackground: "#59516a",
  black: "#2d283f", red: "#ed2261", green: "#1fa91b", yellow: "#8ddc20",
  blue: "#487df4", magenta: "#8d35c9", cyan: "#3bdeed", white: "#9e9ea0",
  brightBlack: "#59516a", brightRed: "#f0729a", brightGreen: "#53aa5e", brightYellow: "#b2dc87",
  brightBlue: "#a9bcec", brightMagenta: "#ad81c2", brightCyan: "#9de3eb", brightWhite: "#a288f7",
};

/** The chrome around the terminal (title bar, empty states), drawn from the same scheme. */
export const TERMINAL_CHROME = {
  bar: "#1f1b2e",
  border: "#2d283f",
  text: "#c9c7d1",
  muted: "#7f7a8f",
} as const;

/**
 * JetBrains Mono first, as in Tabby, then every common monospace face, then the CJK
 * faces (CR-55: CJK must not fall through to the OS default, MS Gothic).
 */
export const TERMINAL_FONT_FAMILY =
  "'JetBrains Mono', 'Cascadia Mono', Consolas, Menlo, 'DejaVu Sans Mono', 'Liberation Mono', " +
  "'Noto Sans SC Variable', 'Noto Sans JP Variable', 'Microsoft YaHei UI', monospace";

/** Tabby's "JetBrains Mono Medium". */
export const TERMINAL_FONT_WEIGHT = 500;
