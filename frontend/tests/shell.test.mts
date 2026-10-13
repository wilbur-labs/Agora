import assert from "node:assert/strict";
import test from "node:test";

import { SHELL_TABS, cliStripOf, cliVersionLineOf } from "../src/lib/shell.ts";

test("the bar has the mockup's four tabs, in its order, and nothing else", () => {
  assert.deepEqual(SHELL_TABS.map((tab) => tab.href), ["/", "/workspace", "/environments", "/skills"]);
  assert.deepEqual(SHELL_TABS.map((tab) => tab.label), ["Today", "Workbench", "Environments", "Skills"]);
});

test("a CLI's version line is one of four sentences, never a blank or a zero", () => {
  assert.deepEqual(cliVersionLineOf({ adapter: "codex", installation_status: "installed", version: "0.149.1" }), { kind: "version", version: "0.149.1" });
  assert.deepEqual(cliVersionLineOf({ adapter: "claude", installation_status: "installed", version: null }), { kind: "unknownVersion" });
  assert.deepEqual(cliVersionLineOf({ adapter: "claude", installation_status: "installed", version: "  " }), { kind: "unknownVersion" });
  assert.deepEqual(cliVersionLineOf({ adapter: "kiro", installation_status: "not_found", version: null }), { kind: "notFound" });
  assert.deepEqual(cliVersionLineOf({ adapter: "kiro", installation_status: "uninspectable", version: "1.0" }), { kind: "uninspectable" });
});

test("the strip tells a probe that failed from one that has not answered, and lists what a landed one said", () => {
  assert.deepEqual(cliStripOf(null, false), { kind: "pending" });
  assert.deepEqual(cliStripOf(null, true), { kind: "failed" });
  const strip = cliStripOf({ adapters: [{ adapter: "codex", installation_status: "installed", version: "1" }] }, false);
  assert.equal(strip.kind, "probed");
  if (strip.kind === "probed") assert.deepEqual(strip.lines, [{ adapter: "codex", line: { kind: "version", version: "1" } }]);
});

test("the shell renders the tabs from the one list, keeps the inventory and Settings reachable, and the workbench carries the same bar", async () => {
  const { readFile } = await import("node:fs/promises");
  const shell = await readFile(new URL("../src/components/delivery-shell.tsx", import.meta.url), "utf8");
  const shellSource = shell.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(shellSource.includes("SHELL_TABS"), "the tabs come from lib/shell.ts");
  // The projects inventory left the tab row by the mockup's design, not the shell: it is
  // still one click away on every screen, as is Settings.
  assert.ok(/href:\s*"\/portfolio"/.test(shellSource), "the inventory stays reachable from the bar");
  assert.ok(/href:\s*"\/settings"/.test(shellSource), "Settings stays reachable from the bar");
  // The version line is drawn from the judgement, and a missing version is never printed
  // as an empty string.
  assert.ok(shellSource.includes("cliStripOf("), "the CLI strip comes from lib/shell.ts");
  assert.ok(!/version\s*\?\?\s*""/.test(shellSource), "a missing version is a sentence, not a blank");
  const page = await readFile(new URL("../src/app/workspace/page.tsx", import.meta.url), "utf8");
  const pageSource = page.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(pageSource.includes("<TopBar"), "the workbench carries the same top bar as every other screen");
});

test("the platform next to 本机 is the OS's own name, never Python's win32", async () => {
  const { platformLabelOf } = await import("../src/lib/shell.ts");
  assert.equal(platformLabelOf("win32"), "Windows");
  assert.equal(platformLabelOf("darwin"), "macOS");
  assert.equal(platformLabelOf("linux"), "Linux");
  assert.equal(platformLabelOf("freebsd14"), "freebsd14");
});
