"""A project's workflow selection: the `workflow:` block of its task.yaml (workspace CR-75
slice 4, design docs/CR75-slice4-workflow.md).

Six switches. Without the block every one takes its default — today's behaviour — so a
task that never chose reads as ``default``. The defaults differ by workspace only in
``aidlc`` (off for claude-workspace, on for kiro-workspace: ``defaults_for``).

The claude-workspace SessionStart hook reads the same block (`scripts/session-start.sh`,
`workflow_parse`) and tells the agent which sections do not apply; a kiro agent reads it
from the task.yaml it loads, by the rules in kiro-workspace's
`.kiro/steering/workflow-selection.md`. The reading rules here and there must agree, so a
block one side accepts the other must not read differently:

- only the block form (``workflow:`` alone on its line, switches indented below it);
- keys from ``FEATURES`` only, values YAML 1.1 booleans only (``on``/``off``/``true``…);
- anything else is ``unreadable`` — never ``default``: a typo in ``review: of`` must not
  quietly read as on, nor as off.

The edit writes all six switches, verified by parsing the result, and refuses a file
whose current block it cannot read or that changed since the person looked at it.
Nothing is committed (same reasons as ``agora/work/shelf.py``).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import yaml

FEATURES: tuple[str, ...] = ("requirements", "breakdown", "aidlc", "deviation", "review", "records")
DEFAULTS: dict[str, bool] = {k: k != "aidlc" for k in FEATURES}
#: A kiro-workspace task (CR-75 slice 4, kiro): AI-DLC is on there today — kiro proposes it
#: by the shape of the request — so a block without `aidlc`, and a task without the block,
#: mean on. Read by the kiro steering file `.kiro/steering/workflow-selection.md`.
KIRO_DEFAULTS: dict[str, bool] = {k: True for k in FEATURES}


def defaults_for(workspace: str) -> dict[str, bool]:
    """The switches' defaults for a ``claude`` or ``kiro`` workspace."""
    return KIRO_DEFAULTS if workspace == "kiro" else DEFAULTS

# The hook's line grammar (claude-workspace scripts/session-start.sh, workflow_parse),
# reproduced exactly: PyYAML accepts more (`{review: off}` indented, quoted keys, tags),
# and a block the hook cannot read must not be shown here as a setting the sessions
# follow (codex review round 1).
_BLOCK_LINE = re.compile(r"^workflow:([ \t]+#.*)?[ \t]*$")
_TOP_LEVEL = re.compile(r"^[^ \t#]")
_SKIP = re.compile(r"^[ \t]*(#.*)?$")
_SWITCH = re.compile(r"^( +)([A-Za-z_]+):( |$)")
_TRUE = {"on", "On", "ON", "true", "True", "TRUE", "yes", "Yes", "YES"}
_FALSE = {"off", "Off", "OFF", "false", "False", "FALSE", "no", "No", "NO"}


class WorkflowRefused(Exception):
    """The change was not made; ``str(self)`` says why, for the person."""


class WorkflowChanged(WorkflowRefused):
    """task.yaml is no longer the text the person was looking at."""


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkflowRead:
    #: "default" (no block) | "set" | "unreadable"
    state: str
    #: The effective switches, defaults filled in; None when unreadable.
    features: dict[str, bool] | None
    reason: str | None
    #: sha256 of the task.yaml text this was read from: the edit's ``base``.
    base: str

    def payload(self) -> dict[str, Any]:
        return {"state": self.state, "features": self.features, "reason": self.reason, "base": self.base}


def _block_lines(text: str) -> list[int]:
    return [i for i, line in enumerate(text.splitlines()) if line.startswith("workflow:")]


_NOT_A_BLOCK = "workflow is not written as a block (switches indented on the lines below it)"


def _hook_reading(text: str) -> dict[str, bool] | str | None:
    """What the SessionStart hook makes of ``text``: None (no block), the switches as
    written, or why it cannot read them."""
    lines = [line.rstrip("\r") for line in text.splitlines()]
    starts = _block_lines(text)
    if not starts:
        return None
    if len(starts) > 1:
        return "workflow is written twice"
    if not _BLOCK_LINE.match(lines[starts[0]]):
        return _NOT_A_BLOCK
    got: dict[str, bool] = {}
    indent: int | None = None
    for line in lines[starts[0] + 1:]:
        if _TOP_LEVEL.match(line):
            break
        if _SKIP.match(line):
            continue
        line = re.sub(r"[ \t]+#.*$", "", line)
        m = _SWITCH.match(line)
        if not m:
            return f"a line that cannot be read: {line.strip()}"
        if indent is None:
            indent = len(m.group(1))
        elif len(m.group(1)) != indent:
            return f"inconsistent indentation: {line.strip()}"
        key = m.group(2)
        value = line.split(":", 1)[1].strip(" \t")
        if key not in FEATURES:
            return f"unknown switch: {key}"
        if key in got:
            return f"switch written twice: {key}"
        if value in _TRUE:
            got[key] = True
        elif value in _FALSE:
            got[key] = False
        else:
            return f"{key} is not on/off"
    return got


def read_workflow(document: dict[str, Any], text: str,
                  defaults: dict[str, bool] = DEFAULTS) -> WorkflowRead:
    """``document`` is ``text`` parsed (by the caller's strict loader). The hook's reading
    decides; the parsed document must agree with it, or the block reads as unreadable.
    ``defaults``: the workspace's (``defaults_for``); the reading rules are the same."""
    base = text_sha(text)

    def unreadable(reason: str) -> WorkflowRead:
        return WorkflowRead(state="unreadable", features=None, reason=reason, base=base)

    hook = _hook_reading(text)
    if hook is None and "workflow" not in document:
        return WorkflowRead(state="default", features=dict(defaults), reason=None, base=base)
    if isinstance(hook, str):
        return unreadable(hook)
    if hook is None or "workflow" not in document:
        # A `workflow` key the hook does not see (quoted, say), or a block line YAML did
        # not take as the key: the two would disagree, so neither reading is shown.
        return unreadable(_NOT_A_BLOCK)
    value = document["workflow"]
    if value is None:  # the block with nothing under it: every switch at its default
        return WorkflowRead(state="set", features=dict(defaults), reason=None, base=base)
    if not isinstance(value, dict):
        return unreadable("workflow is not a list of switches")
    features = dict(defaults)
    for key, flag in value.items():
        if key not in FEATURES:
            return unreadable(f"unknown switch: {key}")
        if not isinstance(flag, bool):
            return unreadable(f"{key} is not on/off")
        features[key] = flag
    if features != {**defaults, **hook}:
        return unreadable("workflow reads differently as YAML than the session hook reads it")
    return WorkflowRead(state="set", features=features, reason=None, base=base)


def edit_workflow(text: str, features: dict[str, bool], *, loader: type,
                  defaults: dict[str, bool] = DEFAULTS) -> str:
    """``text`` with its ``workflow:`` block replaced by ``features`` (or appended) —
    verified by parsing, or WorkflowRefused. ``loader``: the strict loader the reader uses;
    ``defaults``: the workspace's, as the reader was given them."""
    if set(features) != set(FEATURES) or not all(isinstance(v, bool) for v in features.values()):
        raise WorkflowRefused("all six switches, each on or off")
    try:
        before = yaml.load(text, Loader=loader)
    except yaml.YAMLError as exc:
        raise WorkflowRefused(f"task.yaml could not be parsed ({type(exc).__name__})") from None
    if not isinstance(before, dict):
        raise WorkflowRefused("task.yaml is not a mapping")
    if read_workflow(before, text, defaults).state == "unreadable":
        raise WorkflowRefused("the workflow block in task.yaml cannot be read; fix it by hand first")

    # Only the block's lines change: every other line keeps its own ending, so a file that
    # mixes CRLF and LF is not rewritten as a whole (kiro review, CR-75 slice 4).
    raw = text.splitlines(keepends=True)
    lines = text.splitlines()  # the same lines without their endings, for matching

    def ending(i: int) -> str:
        return raw[i][len(lines[i]):]

    def line_break(i: int) -> str:
        # For the new lines, only a break the session hook splits on (kiro review round 3):
        # a lone CR, NEL or U+2028 would leave `workflow:` on no line of its own there.
        found = ending(i)
        return found if found in ("\n", "\r\n") else ""

    default_nl = "\r\n" if "\r\n" in text else "\n"
    block = ["workflow:"] + [f"  {k}: {'on' if features[k] else 'off'}" for k in FEATURES]
    starts = _block_lines(text)
    if starts:
        start = starts[0]
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _TOP_LEVEL.match(lines[j]):
                end = j
                break
        # Comment and blank lines at the end of the old block belong to whatever follows.
        while end - 1 > start and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        nl = line_break(start) or default_nl
        last = line_break(end - 1) or (nl if ending(end - 1) else "")
        raw[start:end] = [line + nl for line in block[:-1]] + [block[-1] + last]
    else:
        trailing = not raw or bool(ending(len(raw) - 1))
        nl = (line_break(len(raw) - 1) if raw else "") or default_nl
        if raw and not trailing:
            raw[-1] += nl
        if lines and lines[-1].strip():
            raw.append(nl)
        raw.extend([line + nl for line in block[:-1]] + [block[-1] + (nl if trailing else "")])
    result = "".join(raw)

    try:
        after = yaml.load(result, Loader=loader)
    except yaml.YAMLError:
        raise WorkflowRefused("this task.yaml's layout is not one the edit can change safely") from None
    read = read_workflow(after, result, defaults) if isinstance(after, dict) else None
    rest_before = {k: v for k, v in before.items() if k != "workflow"}
    rest_after = {k: v for k, v in after.items() if k != "workflow"} if isinstance(after, dict) else None
    if read is None or read.features != features or rest_after != rest_before:
        raise WorkflowRefused("this task.yaml's layout is not one the edit can change safely")
    return result
