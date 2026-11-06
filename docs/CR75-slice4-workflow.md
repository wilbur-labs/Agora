# CR-75 slice 4 — one workflow, chosen per project (first cut: claude)

Status: design, 2026-11-06. Owner decisions (2026-11-06, all three recommended options):

1. **claude first.** The selection is honoured by claude sessions opened with `cw open`
   (claude-workspace). kiro and codex read the same selection in a later slice; until then
   Agora says, for a kiro project, that kiro does not read it yet.
2. **Declared at session start, CLAUDE.md is not split.** The constitution stays whole; the
   SessionStart hook states which features this project has turned off, and the agent
   treats the matching sections as not applying.
3. **Stored in the project's `task.yaml`, editable from Agora.** A person ticks boxes on the
   overview card; Agora writes the `workflow:` block into that `task.yaml`, verified by
   re-parsing, uncommitted — exactly as pause/archive do (CR-80, `agora/work/shelf.py`).

## The six features

The mock-up's list (project detail, 2026-10-30), with the key stored in `task.yaml`, its
default, and what "off" means for a claude session:

| key | name (zh) | default | off means (sections of claude-workspace CLAUDE.md that do not apply) |
|---|---|---|---|
| `requirements` | 需求定义 | on | "New-task kickoff": an empty `goal` does not start `/kickoff`. |
| `breakdown` | 任务拆分 | on | No milestone / WORKFLOW step breakdown is required before work starts. |
| `aidlc` | 详细设计（AI-DLC） | **off** | — (see below: claude does not support it in this slice) |
| `deviation` | 偏离告知 | on | "Change management": creep/refine/clarify/cut capture and the stop-and-ask on creep. **The `defer` rule stays** (see "What off cannot turn off"). |
| `review` | commit 强制评审 | on | "Code review is outsourced to codex": no review is required before a commit. |
| `records` | ADR + lessons | on | "ADRs" and "Cross-task lessons", and Definition-of-Done items 2–3. |

The defaults are today's behaviour, so a `task.yaml` without the block — every existing
task — is unchanged, and the hook says nothing about it.

**`aidlc` for claude.** Nothing in claude-workspace runs AI-DLC today; the rules live in
kiro-workspace and are written for kiro's steering. This slice does not pretend otherwise:
the box can be ticked (the choice is the project's, and the kiro slice will read it), but
the card says "claude 侧暂不支持", and the hook, when it is on, says AI-DLC was selected and
is **not** being applied in this session. A sentence that claimed it was followed would be
the product stating what it does not do.

### What off cannot turn off

- **`defer`** stays in force whatever `deviation` says: it is the rule that a known hazard
  needs something that comes back on its own. Turning off scope tracking is a choice about
  ceremony; turning off this would make a recorded hazard invisible.
- **An installed review gate** (slice 2) keeps refusing unreviewed commits when `review`
  is off. The gate is per repository and git enforces it; the workflow switch is a
  statement to the agent. When `review` is off and the project's `primary_repo` has the
  gate installed, the hook says so ("关卡仍装着：提交仍需评审或写理由"), and so does the
  card. Uninstalling the gate stays an explicit command (`review-gate.sh uninstall`);
  Agora does not do it from a checkbox.
- **Tests.** No switch here turns off running the tests.

## A. The record (`task.yaml`)

A top-level block, only the keys that differ from the default need to be there, but Agora
always writes all six (so the file reads on its own):

```yaml
workflow:
  requirements: on
  breakdown: on
  aidlc: off
  deviation: on
  review: on
  records: off
```

Values are `on` / `off` (YAML 1.1 reads them as booleans; `true`/`false` are accepted
too). Reading rules, the same on both sides:

- block absent → **default** (nothing said by the hook; the card shows "默认").
- block present and every key known with a boolean value → **set**; missing keys take
  their default.
- anything else — an unknown key, a non-boolean value, `workflow:` not a mapping → 
  **unreadable**. The hook says "task.yaml 的 workflow 读不懂（…），本会话按默认全部执行",
  the card says "读不了" with the reason, and the edit refuses to rewrite it (a person
  fixes the file). Never read as "default": a typo in `review: of` must not quietly mean on.

## B. The SessionStart hook (claude-workspace `scripts/session-start.sh`)

A new section after the task's 路径 block, only when the block is present:

```
## 本项目的工作流（task.yaml workflow）
- 关：偏离告知（CLAUDE.md「Change management」不适用；defer 规则仍适用）、ADR + lessons
- 选了 AI-DLC 详细设计，但 claude 侧还不支持：本会话不按 AI-DLC 执行
- commit 强制评审已关，但 <repo> 装着评审关卡：提交仍需评审或写理由
```

Parsing in shell (sed/awk, as `cw.sh` reads `primary_repo`): the top-level `workflow:`
line, then the following indented `key: value` lines until the next non-indented line;
comments and blank lines skipped. Budget: the section is a few hundred characters at most
and goes through the hook's existing budget check (`check-session-start.sh`, which also
gets fixtures: absent / set / unreadable).

CLAUDE.md gets one paragraph (in "Startup flow"): a task's `workflow:` can turn sections
off; the hook says which; follow it, and the three things off cannot turn off.

## C. Agora — read

`agora/projects/workspace_tasks.py`: `WorkspaceTask.workflow` =
`{state: "default" | "set" | "unreadable", features: {key: bool} | None, reason}` from the
same parsed manifest (PyYAML, which already reads it). `features` is the effective value
(defaults filled in) for `default` and `set`, `None` for `unreadable`.

Also `gate_installed_while_review_off` is **not** computed separately: the card already has
`review_gate.state` (slice 2), and the line is derived in the frontend.

kiro-workspace sources: the same field is read and shown, with "kiro 还不读这个设置".

## D. Agora — write

`POST /api/work/projects/{key}/workflow` with `{features: {key: bool} × 6, base: <sha256 of
the task.yaml text the card was rendered from>}`:

1. Resolve `key` as shelve does (404 when not an active work project).
2. Read `task.yaml`; if its sha256 ≠ `base` → 409 `changed` ("这个文件在你打开后被改过，
   重新打开再改"). An agent session may be writing the same file (its RESUME/`/save`
   flow rarely touches task.yaml, but `cw` and the pause/archive edit do).
3. Current block unreadable → 409 `refused` (never rewrite what we could not read).
4. Replace the top-level `workflow:` block (all its indented lines) with the six lines, or
   append it at the end when absent. Same newline style, trailing newline kept.
5. Verify by parsing: the new `workflow` equals the request, and the document with
   `workflow` removed equals the one before. Otherwise 409 `refused`, nothing written.
6. Atomic write (temp + `os.replace`), not committed — the same reasons as `shelf.py`.
   The response carries the new sha256.

No terminal-open refusal (unlike archive): the file is read at the next session start or
`/clear`, so editing while a session runs is harmless; the card says "下次开会话或 /clear
后生效".

The LocalOriginGuard already covers this POST (CR-34).

## E. Agora — the card

On the overview card, next to 暂停 / 归档: a "工作流" button opening an inline panel:
six checkboxes with the one-line meaning, the AI-DLC note, the gate note when it applies,
保存 / 取消, and the "takes effect" line. A collapsed one-line summary on the card only
when the state is not default ("工作流：关 2 项" / "工作流：读不了").

## Out of scope (this slice)

- kiro / codex reading the selection (next slice: generate the matching kiro steering note,
  shown to the person before writing — never a silent change to `.kiro/`).
- Settings page "新项目默认勾选" — `cw new` keeps writing no block (= default).
- Moving AI-DLC into claude-workspace.
- Splitting CLAUDE.md into modules (rejected by owner decision 2).

## Tests

- claude-workspace: `check-session-start.sh` fixtures — absent (no section), set (lists the
  offs), unreadable (says so), `aidlc: on` (says not applied), `review: off` with the gate
  installed (says the gate still applies); budget held.
- Agora backend: read states (absent/set/partial/unknown key/bad value/not a mapping);
  edit — append, replace, CRLF, comments elsewhere preserved, verify-by-parse refusal,
  sha mismatch 409, unreadable 409, unknown key 404.
- Agora frontend: summary line and panel state derivation in `overview.ts`.

## Second cut — kiro (2026-11-06)

- **Steering, one file for all tasks**: kiro-workspace `.kiro/steering/workflow-selection.md`
  (text approved by the owner before it was written). A kiro agent loads its task's
  `task.yaml` as a resource, so the block is in its context; the steering maps each
  switch to the kiro steering files / skills that stop applying, repeats the reading
  rules (unreadable → say so, apply everything) and what no switch turns off (tests, a
  row for a known hazard left unfixed, an installed review gate).
- **AI-DLC defaults to on for kiro** (owner, 2026-11-06): kiro proposes AI-DLC today, so
  a task without the block — or a save that changed nothing — must not switch it off.
  The panel shows no "not supported" note on a kiro card.
- Agora reads the block from kiro task.yaml (another schema; the manifest reader marks it
  `unknown_schema` and still carries `workflow`) and writes it with the kiro defaults.
- **codex**: Agora opens no codex session per project, so nothing reads the selection on
  that side; codex is the reviewer, which the switches do not configure.
