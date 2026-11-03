/**
 * The app shell's top bar — what it may list, and what it may say about the CLIs.
 *
 * Mockup v2 draws one bar over every screen: four tabs (Today · 项目工作台 · 环境 ·
 * 技能库) and, on the right, the current environment and the three agent CLIs with
 * their versions. The tabs are a decision recorded here once, so the shell and its test
 * read the same list; the version line is a judgement, because a probe of a CLI has
 * more outcomes than "a version": not installed, installed but not inspectable,
 * installed with no version string, the probe itself failed, the probe not back yet.
 * Each is a different sentence, and none of them is an empty string or a "0".
 *
 * Deliberately free of React and of "@/" path aliases: `node --test` executes the
 * companion test file (tests/shell.test.mts) directly.
 */

/** One of the bar's screens. `label` is the stable English name pages pass as `active`. */
export interface ShellTab {
  href: string;
  /** Key under `common.nav.*`. */
  key: string;
  label: string;
}

/** The bar's tabs, in order. The projects inventory and Settings
 *  are reached from the bar's right-hand cluster, where the mockup's design note for
 *  Today puts the inventory ("另开「项目」页") without giving it a tab. */
export const SHELL_TABS: readonly ShellTab[] = [
  // Workspace CR-75 slice 1 (10-30): the work page — one terminal per project, the mockup
  // at claude.ai/artifact/NeG3BPvLtvhAK5QipyvTar. Since CR-79 (11-03) it is also the front
  // page: Today and the project workbench were removed at the owner's word.
  { href: "/", key: "work", label: "Work" },
  { href: "/environments", key: "environments", label: "Environments" },
  { href: "/skills", key: "skills", label: "Skills" },
];

/** The fields the version line needs from one probed adapter; a structural subset. */
export interface CliProbeLike {
  adapter: string;
  installation_status: "installed" | "not_found" | "uninspectable";
  version: string | null;
}

/** What the bar may print beside one CLI's name. */
export type CliVersionLine =
  | { kind: "version"; version: string }
  | { kind: "notFound" }
  | { kind: "uninspectable" }
  /** Installed, and the probe returned no version string. Not "0", not blank. */
  | { kind: "unknownVersion" };

export function cliVersionLineOf(probe: CliProbeLike): CliVersionLine {
  if (probe.installation_status === "not_found") return { kind: "notFound" };
  if (probe.installation_status === "uninspectable") return { kind: "uninspectable" };
  const version = (probe.version ?? "").trim();
  return version.length > 0 ? { kind: "version", version } : { kind: "unknownVersion" };
}

/** The probe's own outcome, before any adapter is looked at. */
export type CliStrip =
  | { kind: "pending" }
  | { kind: "failed" }
  | { kind: "probed"; lines: readonly { adapter: string; line: CliVersionLine }[] };

export function cliStripOf(
  probe: { adapters: readonly CliProbeLike[] } | null,
  failed: boolean,
): CliStrip {
  if (probe === null) return failed ? { kind: "failed" } : { kind: "pending" };
  return {
    kind: "probed",
    lines: probe.adapters.map((adapter) => ({ adapter: adapter.adapter, line: cliVersionLineOf(adapter) })),
  };
}

/**
 * The OS name the bar shows next to 本机. The probe reports Python's `sys.platform`,
 * which is `win32` on 64-bit Windows too — the name of the API, not the bitness — and
 * read as "32-bit" by the person looking at it (CR-55). Known names become the OS's
 * own; anything else is shown as reported rather than guessed at.
 */
export function platformLabelOf(platform: string): string {
  if (platform === "win32" || platform === "cygwin" || platform === "msys") return "Windows";
  if (platform === "darwin") return "macOS";
  if (platform === "linux" || platform.startsWith("linux")) return "Linux";
  return platform;
}
