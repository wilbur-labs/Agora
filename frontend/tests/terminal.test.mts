import assert from "node:assert/strict";
import { test } from "node:test";

import { launchChoicesOf, launchCodeOf, readTerminalToken, terminalStateOf, terminalStreamUrl } from "../src/lib/terminal.ts";

test("the stream address follows the page's own server and scheme", () => {
  assert.equal(terminalStreamUrl("ab1", { protocol: "http:", host: "127.0.0.1:8000" }, ""), "ws://127.0.0.1:8000/api/terminals/ab1/stream");
  assert.equal(terminalStreamUrl("ab1", { protocol: "https:", host: "x" }, ""), "wss://x/api/terminals/ab1/stream");
  assert.equal(terminalStreamUrl("ab1", { protocol: "http:", host: "127.0.0.1:3000" }, "http://127.0.0.1:8001"), "ws://127.0.0.1:8001/api/terminals/ab1/stream");
});

test("an ended terminal with no exit code says so", () => {
  assert.equal(terminalStateOf({ alive: true, exit_code: null }), "running");
  assert.equal(terminalStateOf({ alive: false, exit_code: 0 }), "exited");
  assert.equal(terminalStateOf({ alive: false, exit_code: null }), "exitedUnknown");
});

test("tasks come first, and a task that cannot open is listed, not dropped", () => {
  const choices = launchChoicesOf({ project_id: "p", root: "r", tasks: [
    { source: "E:/Work", task: "work-chat", title: null, tool_dir: "E:/tool", reason: null },
    { source: "E:/Other", task: "x", title: null, tool_dir: null, reason: "no tool" },
  ] });
  assert.deepEqual(choices.slice(0, 2).map((c) => c.kind === "task" && c.openable), [true, false]);
  assert.deepEqual(choices.slice(2).map((c) => c.kind === "repo" && c.command), ["claude", "codex", "kiro", "shell"]);
  assert.equal(launchChoicesOf(null).length, 4);
});

test("the launch code is taken out of the address, everything else kept", () => {
  assert.deepEqual(launchCodeOf("http://127.0.0.1:8000/?terminal_code=abc"), { code: "abc", clean: "/" });
  assert.deepEqual(launchCodeOf("http://127.0.0.1:8000/workspace?project=wc&terminal_code=abc#x"), { code: "abc", clean: "/workspace?project=wc#x" });
  assert.deepEqual(launchCodeOf("http://127.0.0.1:8000/workspace"), { code: null, clean: "/workspace" });
});

test("no stored token reads as none, a broken store as none", () => {
  assert.equal(readTerminalToken({ getItem: () => null, setItem: () => undefined }), null);
  assert.equal(readTerminalToken({ getItem: () => { throw new Error("blocked"); }, setItem: () => undefined }), null);
  assert.equal(readTerminalToken(null), null);
});
