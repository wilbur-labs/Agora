"""Project kickoff: the agent reads the project once, asks what only the person can
answer, and — after the answers — proposes the charter and the first tasks.

This is the product form of the workspace's ``/kickoff`` interview (W10, CR-54). The
mockup names it on Today's card for a project without a goal: "接入 Agora 时会跑一次项目
初始化，读 README + 目录 + git 历史，生成一份初稿给你确认。让 agent 读一遍 →".

Three deliberate shapes, each with a reason:

1. **The context is gathered here, in Python, and handed to the agent in the prompt.**
   README head, top-level tree, recent git log. Not "let the agent explore": a one-shot
   ``claude -p`` with tools may prompt for permissions it cannot get, and what the agent
   *read* must be recorded next to what it *concluded* — the questions and the draft cite
   the context, and a part that could not be read is named as unread, never guessed.
2. **Two agent passes, not a conversation.** Pass one (``generate``) takes the brief and
   the context and returns a charter draft plus 4–6 questions only the person can answer.
   Pass two (``refine``) takes the answers and returns the charter to adopt and the first
   tasks. A single JSON answer per pass, parsed fail-closed like ``tasks/clarify.py``.
   No automatic follow-up rounds: a person who wants another round asks again.
3. **The record lives in the project repository**, ``<root>/.agora/kickoff.json`` — the
   same rule as the CR Log and the decisions (adr/0004): the definition of a project is
   the project's, versioned with it. Nothing is written to the charter or the task
   store until the person adopts the proposal; until then the file is a draft they can
   redo. (The charter itself still lives where it lives today — adr/0005's migration is
   separate work — so adoption goes through the existing ``set_charter``.)

Every read of the record reports one of: absent (never started), present, unreadable
(the file is there and could not be read), malformed (read fine, not a record). The UI
renders each as its own sentence; none of them is rendered as another.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import shutil
import stat as statmod
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.fsprobe import probe_path
from agora.orchestration.runtime import RuntimeLaunchError, resolve_runtime_command

KICKOFF_RELATIVE_PATH = Path(".agora") / "kickoff.json"
KICKOFF_TIMEOUT = 240
#: The prompts ask for 4–6 questions and 3–6 tasks; these are the bounds outside which
#: the answer is *refused*. The two are different on purpose: the band is what a good
#: answer looks like, the bounds are where an answer stops being the interview at all —
#: three questions over a one-line brief is usable and the person sees all three; two is
#: not an interview, and nine is the agent not choosing. Same for tasks: one task is a
#: proposal the person can extend in the editor; none is not a proposal.
MIN_QUESTIONS = 3
MAX_QUESTIONS = 8
MIN_TASKS = 1
MAX_TASKS = 8
#: Field caps. Over the cap the answer is refused, not cut: a title cut at 300 or a
#: description cut at 20,000 characters would reach the task store as if that were all
#: the agent said, and the acceptance line at the end of it would be gone without a trace.
TITLE_CHARS = 300
DESCRIPTION_CHARS = 20_000
#: The most a record file is read, and the most one is allowed to be written: eight
#: answers and eight tasks of ANSWER_CHARS / DESCRIPTION_CHARS each plus the rest stays
#: well under it, and the write refuses anything over it rather than produce a file the
#: read would call malformed.
RECORD_CHARS = 4_000_000
#: One answer, in characters; the request model holds the person to it.
ANSWER_CHARS = 20_000
#: One question id; the parser and the answers route hold the same line.
QUESTION_ID_CHARS = 128
#: The charter's caps are the existing charter editor's (`SetCharterRequest`): the goal
#: 2,000 characters, each list item 500. Parser, adopt route and record agree with it, so a
#: charter that came through the kickoff can always be saved again through the editor.
CHARTER_FIELD_CHARS = 2_000
CHARTER_ITEM_CHARS = 500
#: Items per charter list, and approved tasks per adoption — the adopt route's counts,
#: which the sealed record is held to as well.
CHARTER_LIST_ITEMS = 20
APPROVED_TASKS = 20
README_LINES = 120
README_CHARS = 12_000
TREE_ENTRIES = 40
GIT_LOG_ENTRIES = 15

#: Where the mockup task comes from when the project has a UI. Named once so the
#: router, the tests and the UI agree on which proposed task is "the mockup".
MOCKUP_TASK_KIND = "mockup"


class KickoffError(RuntimeError):
    pass


class RecordTooLarge(KickoffError):
    """The record would be larger than the read takes back."""


class ProjectRootMissing(KickoffError):
    """The project's root directory is not there."""


class PathUnsafe(KickoffError):
    """The record's path is a link, or could not be inspected."""


# --- what the agent is shown ------------------------------------------------------------

#: The one line the tree listing appends when it is cut — matched whole, so a real file
#: whose name begins with "…" (ordinary in Japanese) is never mistaken for it.
_TREE_CUT = re.compile(r"^… \(\d+ more\)$")


@dataclass(frozen=True)
class RepoContext:
    """What was read from the project root, and what could not be."""
    readme: str | None          # None: no README found or unreadable (see `unread`)
    tree: list[str]
    git_log: list[str]
    unread: list[str] = field(default_factory=list)  # sentences naming what was not read

    def as_prompt_text(self) -> str:
        parts: list[str] = []
        parts.append("Top-level entries:\n" + ("\n".join(f"  {e}" for e in self.tree) if self.tree else "  (none)"))
        parts.append("Recent git log:\n" + ("\n".join(f"  {e}" for e in self.git_log) if self.git_log else "  (none)"))
        parts.append("README (head):\n" + (self.readme if self.readme else "  (none)"))
        if self.unread:
            parts.append("Could NOT be read (do not guess about these):\n" + "\n".join(f"  - {u}" for u in self.unread))
        return "\n\n".join(parts)

    def summary(self) -> dict[str, Any]:
        """What goes into the record: the counts, and the *text the agent was shown*, so
        the draft and the questions can be checked against what they were drawn from
        after the README or the history has moved on. Bounded by the same caps as the
        prompt (README_LINES / README_CHARS / TREE_ENTRIES / GIT_LOG_ENTRIES)."""
        return {
            "readme": self.readme is not None,
            "tree_entries": len(self.tree),
            "git_log_entries": len(self.git_log),
            "unread": list(self.unread),
            "text": self.as_prompt_text(),
        }

    @property
    def is_empty(self) -> bool:
        """Nothing for the agent to read: no README, no history, and no entry but Agora's
        own `.agora/`. An empty repository plus an empty brief is a prompt with zero facts,
        and its questions would be guesses dressed as analysis."""
        substantive = [e for e in self.tree if e not in {".agora/", ".gitignore"} and not _TREE_CUT.match(e)]
        return self.readme is None and not self.git_log and not substantive


def gather_context(root: Path) -> RepoContext:
    """Read the project root without guessing. Each failure becomes a named `unread`."""
    unread: list[str] = []

    tree: list[str] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        # The filtered names first, then the cut: "(N more)" must count what was left out
        # of *this* list, not the raw directory — `.git` and `node_modules` are not "more".
        shown = [e.name + ("/" if e.is_dir() else "") for e in entries if e.name not in {".git", "node_modules", "__pycache__", ".venv"}]
        tree = shown[:TREE_ENTRIES]
        if len(shown) > TREE_ENTRIES:
            tree.append(f"… ({len(shown) - TREE_ENTRIES} more)")
    except OSError as exc:
        unread.append(f"top-level directory listing ({type(exc).__name__})")

    readme: str | None = None
    # The README is read without following a symlink, and only as a regular file: a
    # link named README.md pointing outside the project would otherwise put whatever it
    # points at into the prompt sent to the agent. Named as unread, not silently skipped.
    # Every candidate is tried until one reads: a README.md that is a directory or a link
    # is named as unread *and* the next name is still looked at — otherwise a readable
    # README.txt behind it would be missed and the record would only mention the first.
    # Named by what is in the directory, matched without regard to case — `Readme.md` is
    # ordinary, and on a case-sensitive filesystem a fixed spelling would miss it while
    # the tree listing still showed it (and so would have called the repository
    # non-empty). `.md` first, then bare, `.txt`, `.rst`; within a name, the listing's order.
    readme_names: list[str] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError as exc:
        # A listing that failed is not "no README": named, so "(none)" is never said of a
        # directory that could not be looked at.
        entries = []
        unread.append(f"README lookup (directory listing {type(exc).__name__})")
    for suffix in (".md", "", ".txt", ".rst"):
        readme_names.extend(e for e in entries if e.lower() == f"readme{suffix}")
    for name in readme_names:
        candidate = root / name
        probe = probe_path(candidate, follow_symlinks=False)
        if probe.absent:
            continue
        if probe.kind != "file":
            unread.append(f"{name} (not a regular file — a link or a directory; not followed)")
            continue
        try:
            text, cut = _read_plain_text(candidate, README_CHARS * 4)
        except OSError as exc:
            unread.append(f"{name} ({type(exc).__name__}: {exc})")
            continue
        lines = text.splitlines()
        readme = "\n".join(lines[:README_LINES])
        # The head is capped first, then the notes are appended, so a note is never what
        # the cap cuts away. A line count is stated only when the whole file was read; over
        # a cut read the count would be of the prefix, a wrong fact handed to the agent.
        if len(readme) > README_CHARS:
            readme = readme[:README_CHARS] + "\n… (cut at the size limit)"
        if cut:
            readme += "\n… (the file goes on past the read limit; the rest was not read)"
        elif len(lines) > README_LINES:
            readme += f"\n… ({len(lines) - README_LINES} more lines)"
        break

    git_log: list[str] = []
    git_dir = root / ".git"
    git_probe = probe_path(git_dir, follow_symlinks=False)
    git_reachable = False
    if git_probe.unknown:
        # A `.git` that could not be inspected (an ACL, a share that dropped) is not "no
        # history" — the agent would draft on that as a fact. Named as unread instead.
        unread.append("git history (.git could not be inspected)")
    elif git_probe.present:
        # The same boundary as the README and the record: history is read from *this*
        # project only. A `.git` that is a link, or a gitfile whose `gitdir:` points
        # outside the project, would have `git log` read another repository's commits
        # into the prompt. Named as unread, not followed.
        try:
            linked = _is_link(git_dir)
        except OSError:
            linked = None
        if linked is None:
            unread.append("git history (.git could not be inspected)")
        elif linked:
            unread.append("git history (.git is a link; not followed)")
        elif git_probe.kind == "file":
            try:
                pointer, _cut = _read_plain_text(git_dir, 4096)
            except OSError as exc:
                pointer = None
                unread.append(f"git history (.git gitfile {type(exc).__name__})")
            if pointer is not None:
                target = pointer.strip()
                target = target[len("gitdir:"):].strip() if target.startswith("gitdir:") else ""
                try:
                    resolved = (root / target).resolve() if target else None
                    inside = resolved is not None and _is_within(resolved, root.resolve())
                except (OSError, RuntimeError) as exc:
                    # A target on a share that dropped, behind an ACL, or in a symlink
                    # loop: `resolve` raises, and that is "could not be read", not a 500.
                    inside = False
                    unread.append(f"git history (.git gitfile target could not be resolved: {type(exc).__name__})")
                else:
                    if not inside:
                        unread.append("git history (.git is a gitfile pointing outside the project; not followed)")
                if inside:
                    git_reachable = True
        else:
            git_reachable = True
    if git_reachable:
        try:
            # A scrubbed environment: GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE … inherited
            # from the shell or service that started Agora would point `git log` at some
            # other repository after this one's `.git` had passed every check.
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
            result = subprocess.run(
                ["git", "log", "--oneline", f"-{GIT_LOG_ENTRIES}"],
                cwd=str(root), capture_output=True, timeout=20, check=False, env=env,
            )
            if result.returncode == 0:
                try:
                    git_log = [line for line in result.stdout.decode("utf-8").splitlines() if line.strip()]
                except UnicodeDecodeError:
                    # A repository whose log output is not UTF-8 (i18n.logOutputEncoding)
                    # is named as unread, not handed over with "�" where the words were.
                    unread.append("git log (not UTF-8 text)")
            else:
                unread.append("git log (git exited non-zero)")
        except (OSError, subprocess.TimeoutExpired) as exc:
            unread.append(f"git log ({type(exc).__name__})")
    elif git_probe.absent:
        unread.append("git history (not a git repository)")

    return RepoContext(readme=readme, tree=tree, git_log=git_log, unread=unread)


# --- the two prompts and their parsers --------------------------------------------------

_GENERATE_PROMPT = (
    "You are a senior product/engineering advisor starting a new project with its owner. "
    "Read the owner's brief and what could be read from the repository below. Produce ONLY "
    "a JSON object (no prose, no markdown fence) with keys:\n"
    "  \"charter_draft\": {\"goal\": one-sentence goal (Simplified Chinese), "
    "\"non_goals\": [2-4 short items], \"success_criteria\": [2-4 short, checkable items]},\n"
    "  \"questions\": [4-6 objects {\"id\": kebab-case slug, \"question\": (Chinese), "
    "\"why\": one short reason (Chinese), \"suggestions\": [2-4 short candidate answers (Chinese)]}] "
    "— ONLY questions the owner must answer; do not ask what the repository already answers,\n"
    "  \"needs_mockup\": true if the project has a user interface of any kind, else false,\n"
    "  \"mockup_note\": one sentence (Chinese) on what the mockup must settle, or \"\" if none,\n"
    "  \"read_notes\": [short sentences (Chinese) naming what you relied on from the repository, "
    "and what was NOT readable — never guess about unread parts].\n\n"
    "Project name: {name}\nOwner's brief:\n{brief}\n\nRepository:\n{context}"
)

_REFINE_PROMPT = (
    "You are a senior product/engineering advisor. The owner has answered the kickoff "
    "questions. Produce ONLY a JSON object (no prose, no markdown fence) with keys:\n"
    "  \"charter\": {\"goal\": one sentence (Simplified Chinese), \"non_goals\": [2-5 items], "
    "\"success_criteria\": [3-6 checkable items]},\n"
    "  \"tasks\": [3-6 objects {\"title\": (Chinese, imperative), \"description\": 2-4 sentences "
    "(Chinese) incl. what done looks like, \"kind\": one of \"mockup\", \"scaffold\", \"feature\", "
    "\"research\", \"custom\"}] in the order to do them; if the project has a user interface the "
    "FIRST task must be kind \"mockup\": draw the screens as an HTML mockup under docs/mockups/ "
    "and get the owner's sign-off before any UI code — every later acceptance line will point at it,\n"
    "  \"needs_mockup\": true/false.\n\n"
    "Project name: {name}\nOwner's brief:\n{brief}\n\nRepository:\n{context}\n\n"
    "Charter draft from the first pass:\n{draft}\n\nQuestions and the owner's answers:\n{qa}"
)


_IDEA_PROMPT = (
    "You are the advisor who ran this project's kickoff, still in the same conversation. "
    "The owner has just said something new. Decide what it is against the charter they "
    "adopted and the development order they already have. Produce ONLY a JSON object (no "
    "prose, no markdown fence) with keys:\n"
    "  \"verdict\": \"task\" if it is work to do now, \"later\" if it belongs to a second "
    "version (outside the charter's scope), \"note\" if it is a remark, a question or a "
    "constraint rather than a piece of work,\n"
    "  \"echo\": one sentence (Simplified Chinese) restating what they said, as a boundary. "
    "It is shown after the words 「我记成：」, so write only the part after that,\n"
    "  \"why\": one short sentence (Chinese) saying why it is that verdict, naming the "
    "charter or an existing task where that helps,\n"
    "  \"task\": {\"title\": (Chinese, imperative), \"description\": 2-4 sentences (Chinese) "
    "incl. what done looks like, \"kind\": one of \"mockup\", \"scaffold\", \"feature\", "
    "\"research\", \"custom\"} when the verdict is \"task\", otherwise null,\n"
    "  \"position\": where it goes in the development order when the verdict is \"task\" — "
    "1 is first, {append} is last, after everything listed; otherwise null.\n\n"
    "Project name: {name}\n\nThe adopted charter:\n{charter}\n\n"
    "The development order as it stands ({count} tasks, the first one is next):\n{order}\n\n"
    "What the owner just said:\n{idea}"
)


class FileTooLarge(OSError):
    """The file goes past the limit and the caller asked for whole-or-nothing."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_plain_text(path: Path, limit: int, *, whole: bool = False) -> tuple[str, bool]:
    """Read a regular file without following a link — checked on the *open descriptor*,
    not on a probe taken a moment earlier. `probe_path(..., follow_symlinks=False)` and
    then `read_text()` was two looks with a gap, and a file swapped for a symlink in the
    gap would have been followed. Here the file is opened (without following where the
    platform can say so), then the descriptor's identity is compared with `lstat` of the
    path: a link at the path, or a different file behind the descriptor, is refused."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        at_path = os.lstat(path)
        if statmod.S_ISLNK(at_path.st_mode) or _is_link(path):
            raise OSError("is a link")
        if not statmod.S_ISREG(opened.st_mode):
            raise OSError("not a regular file")
        if (opened.st_ino, opened.st_dev) != (at_path.st_ino, at_path.st_dev):
            raise OSError("the file changed between open and check")
        # Until the limit or EOF — one `os.read` may return short of both on a network
        # share, and a short read taken for the end would be a partial README sent as
        # whole, or a whole record called malformed.
        # One byte past the limit is asked for, so "the file is longer than the limit" is
        # known rather than a prefix being taken for the whole; with `whole` that is a
        # refusal (a record must be read entire or not at all), without it the text is cut
        # at the limit (the README head is a head by design).
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    over = len(data) > limit
    if over and whole:
        raise FileTooLarge(f"{path.name} is longer than {limit} bytes")
    data = data[:limit]
    # An incremental decoder holds back a multibyte sequence cut by the read limit instead
    # of calling it an error; a genuinely invalid byte inside the data still raises.
    try:
        return codecs.getincrementaldecoder("utf-8")().decode(data, final=not over), over
    except UnicodeDecodeError as exc:
        # Not guessed at: a Shift-JIS README decoded as UTF-8 with replacement characters
        # would reach the agent as a "read" README full of holes. Named as unread instead.
        raise OSError(f"not UTF-8 text (byte {exc.start})") from None


def _fill(template: str, **values: str) -> str:
    """Substitute `{name}` tokens by name, in one pass. Not `str.format`: the templates
    describe JSON and are full of braces that are not placeholders. One pass, not one
    `replace` per token: a brief that contains the text "{context}" must reach the agent
    as that text, not as the repository — what the record says the agent was shown and
    what it was shown would otherwise differ."""
    return re.sub(
        r"\{(" + "|".join(re.escape(k) for k in values) + r")\}",
        lambda m: values[m.group(1)],
        template,
    )


def _candidate_objects(text: str, *, must_have: str) -> list[dict[str, Any]]:
    """Every JSON object in the text that carries the contract's key (`questions` for
    pass one, `tasks` for pass two), in order; failing any, the first object that decodes
    at all, so the refusal names what is missing. Not "from the first brace to the last",
    and not "the first thing with the key": prose before the answer may hold an example
    object of its own — even one shaped like the answer — and the caller tries each
    candidate in turn, taking the first that parses."""
    decoder = json.JSONDecoder()
    last_error: str | None = None
    with_key: list[dict[str, Any]] = []
    first_object: dict[str, Any] | None = None
    start = text.find("{")
    while start != -1:
        try:
            raw, _end = decoder.raw_decode(text, start)
        except RecursionError:
            # Nesting past the recursion limit: not an answer, and not worth scanning on
            # from every inner brace (each attempt would blow the stack again — a hundred
            # thousand of them took over a minute). Refused as such, a 502, never a 500.
            raise KickoffError("agent output nests deeper than can be parsed") from None
        except ValueError as exc:
            # JSONDecodeError is a ValueError; so is an integer past the digit limit.
            last_error = f"{type(exc).__name__}: {exc}"[:200]
        else:
            if isinstance(raw, dict):
                if must_have in raw:
                    with_key.append(raw)
                elif first_object is None:
                    first_object = raw
        start = text.find("{", start + 1)
    if with_key:
        return with_key
    if first_object is not None:
        return [first_object]
    if last_error is None:
        raise KickoffError("agent output contained no JSON object")
    raise KickoffError(f"agent output was not valid JSON: {last_error}")


def _first_that_parses(text: str, *, must_have: str, build) -> dict[str, Any]:
    first_error: KickoffError | None = None
    for raw in _candidate_objects(text, must_have=must_have):
        try:
            return build(raw)
        except KickoffError as exc:
            first_error = first_error or exc
    assert first_error is not None
    raise first_error


def _text(value: Any, what: str, *, required: bool) -> str:
    """A string, or a refusal. `str()` on whatever came would turn `{"zh": "画原型"}` into
    the text of a Python dict and `123` into "123", and both would become real tasks."""
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise KickoffError(f"agent output's {what} was not text")
    text = value.strip()
    if required and not text:
        raise KickoffError(f"agent output's {what} was empty")
    return text


def _strings(value: Any, cap: int, what: str) -> list[str]:
    """A list of non-blank strings, or a refusal — not the good items out of a mixed list.
    A missing key is an empty list (the agent had nothing to say); a list with a number,
    a blank or an object in it, or more items than the contract allows, is an answer that
    did not follow the contract, and the person would otherwise see a trimmed version of it
    without knowing it was trimmed."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise KickoffError(f"agent output's {what} was not a list")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise KickoffError(f"agent output's {what} item {index + 1} was not text")
        out.append(item.strip())
    if len(out) > cap:
        raise KickoffError(f"agent output's {what} had {len(out)} items; at most {cap}")
    return out


def _charter(value: Any, *, final: bool) -> dict[str, Any]:
    """The draft (pass one) may come without criteria — it is a draft, and the questions
    are what settle them. The charter to adopt (pass two) must have at least one checkable
    success criterion: a charter with none is a goal with no way to know it was met, and
    adopting it would write exactly that into the project."""
    if not isinstance(value, dict):
        raise KickoffError("agent output had no charter object")
    goal = _text(value.get("goal"), "charter goal", required=True)
    criteria = _strings(value.get("success_criteria"), 8, "charter success_criteria")
    non_goals = _strings(value.get("non_goals"), 8, "charter non_goals")
    # The adopt route holds each charter field to CHARTER_FIELD_CHARS; a proposal the
    # person could not adopt as it stands is refused here, not shown.
    if len(goal) > CHARTER_FIELD_CHARS:
        raise KickoffError(f"agent output's charter goal is longer than {CHARTER_FIELD_CHARS} characters")
    for what, text in [*(("success_criteria", c) for c in criteria), *(("non_goals", n) for n in non_goals)]:
        if len(text) > CHARTER_ITEM_CHARS:
            raise KickoffError(f"agent output's charter {what} is longer than {CHARTER_ITEM_CHARS} characters")
    if final and not criteria:
        raise KickoffError("agent output's charter has no success_criteria")
    return {
        "goal": goal,
        "non_goals": non_goals,
        "success_criteria": criteria,
    }


def _flag(raw: dict[str, Any], key: str) -> bool:
    """A JSON boolean, or a refusal. `bool("false")` is True, and a UI flag read that way
    would demand a mockup of a project that has no screens."""
    value = raw.get(key)
    if not isinstance(value, bool):
        raise KickoffError(f"agent output's {key} was not a boolean")
    return value


def _questions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise KickoffError("agent output had no questions array")
    # Fail closed on the *list*, not item by item: an answer with one usable question out
    # of four is not "one question", it is an answer that did not follow the contract, and
    # a flow built on the survivor would be built on a fraction the person cannot see.
    if not MIN_QUESTIONS <= len(value) <= MAX_QUESTIONS:
        raise KickoffError(f"agent produced {len(value)} questions; {MIN_QUESTIONS}–{MAX_QUESTIONS} expected")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise KickoffError(f"agent output's question {index + 1} was not a question object")
        question = _text(item.get("question"), f"question {index + 1}", required=True)
        # Answers are keyed by id: two questions with one id would share one answer and the
        # second would be silently lost. A duplicate is renamed, never merged.
        base = _text(item.get("id"), f"question {index + 1} id", required=False) or f"q{index + 1}"
        if len(base) > QUESTION_ID_CHARS:
            # The answers route holds ids to this length; an interview whose ids it would
            # refuse could never be answered, so it is refused here, before it is shown.
            raise KickoffError(f"agent output's question {index + 1} id is longer than {QUESTION_ID_CHARS} characters")
        qid, n = base, 2
        while qid in seen:
            qid = f"{base}-{n}"
            n += 1
        if len(qid) > QUESTION_ID_CHARS:
            raise KickoffError(f"agent output's question {index + 1} id is longer than {QUESTION_ID_CHARS} characters once made unique")
        seen.add(qid)
        out.append({
            "id": qid,
            "question": question,
            "why": _text(item.get("why"), f"question {index + 1} why", required=False),
            "suggestions": _strings(item.get("suggestions"), 4, f"question {index + 1} suggestions"),
        })
    if not out:
        raise KickoffError("agent produced no usable questions")
    return out


def parse_generate(text: str) -> dict[str, Any]:
    return _first_that_parses(text, must_have="questions", build=_build_generate)


def _build_generate(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "charter_draft": _charter(raw.get("charter_draft"), final=False),
        "questions": _questions(raw.get("questions")),
        "needs_mockup": _flag(raw, "needs_mockup"),
        "mockup_note": _text(raw.get("mockup_note"), "mockup_note", required=False),
        "read_notes": _strings(raw.get("read_notes"), 10, "read_notes"),
    }


TASK_KINDS = frozenset({"mockup", "scaffold", "feature", "research", "custom"})
_TASK_KINDS = TASK_KINDS


def parse_refine(text: str) -> dict[str, Any]:
    return _first_that_parses(text, must_have="tasks", build=_build_refine)


def _build_refine(raw: dict[str, Any]) -> dict[str, Any]:
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list):
        raise KickoffError("agent output had no tasks array")
    if not MIN_TASKS <= len(tasks_raw) <= MAX_TASKS:
        raise KickoffError(f"agent produced {len(tasks_raw)} tasks; {MIN_TASKS}–{MAX_TASKS} expected")
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(tasks_raw):
        if not isinstance(item, dict):
            raise KickoffError(f"agent output's task {index + 1} was not a task object")
        # Kind and description are required, not defaulted: a task without a kind cannot
        # be the mockup task, and a task without a description has no "done looks like".
        kind = _text(item.get("kind"), f"task {index + 1} kind", required=True).lower()
        # A kind outside the contract is refused, not rounded: "mock-up" filed as "custom"
        # would be a UI project whose mockup task was quietly turned into something else,
        # and then reported as missing. The refusal names the kind; asking again is cheap.
        if kind not in _TASK_KINDS:
            raise KickoffError(
                f"agent output's task {index + 1} kind {kind!r} is not one of {sorted(_TASK_KINDS)}"
            )
        title = _text(item.get("title"), f"task {index + 1} title", required=True)
        description = _text(item.get("description"), f"task {index + 1} description", required=True)
        if len(title) > TITLE_CHARS:
            raise KickoffError(f"agent output's task {index + 1} title is {len(title)} characters; at most {TITLE_CHARS}")
        if len(description) > DESCRIPTION_CHARS:
            raise KickoffError(
                f"agent output's task {index + 1} description is {len(description)} characters; at most {DESCRIPTION_CHARS}"
            )
        tasks.append({"title": title, "description": description, "kind": kind})
    if not tasks:
        raise KickoffError("agent produced no usable tasks")
    needs_mockup = _flag(raw, "needs_mockup")
    return {
        "charter": _charter(raw.get("charter"), final=True),
        "tasks": tasks,
        "needs_mockup": needs_mockup,
        # Said, not silently repaired: a UI project whose proposal has no mockup task, or
        # has it anywhere but first, is a proposal the person should see as incomplete
        # before adopting it — the order is the point (mockup before any UI code), and
        # adoption turns order into priority.
        "mockup_task_missing": needs_mockup and (not tasks or tasks[0]["kind"] != MOCKUP_TASK_KIND),
    }


def _run_agent(prompt: str, cwd: Path) -> str:
    """One shot of the Claude CLI with the prompt on stdin.

    Always Claude, whatever the project's worker agent is — the same choice
    ``tasks/clarify.py`` makes for the requirements interview: this is an analyst call,
    not a run in the project's worktree, and the UI says so. Stdin rather than an
    argument because a brief plus a README head can pass the Windows command-line limit
    (~32K), and a WinError 206 at launch would read as "the agent refused".
    """
    try:
        # The same audited read-only shape the runtime registry uses for its own
        # one-shot calls (orchestration/runtime.py): plan mode (no edits, no commands),
        # safe mode, and no session left behind. The analyst is handed the context in
        # the prompt; it is not to go and act on the project.
        command = resolve_runtime_command([
            "claude", "-p", "--output-format", "text",
            "--permission-mode", "plan", "--safe-mode", "--no-session-persistence",
            "--tools", "",
        ])
    except RuntimeLaunchError as exc:
        raise KickoffError(f"agent is unavailable: {exc}") from None
    try:
        # `--tools ""` and an empty scratch directory as cwd, not the project: the record
        # says what the agent was shown, and that is only true if the agent could not go
        # and read the rest of the repository (a `.env`, the source past the README head)
        # on its own. Plan mode alone still allowed read tools; now there are none.
        scratch = tempfile.mkdtemp(prefix="agora-kickoff-")
        try:
            result = subprocess.run(
                command, cwd=scratch, input=prompt.encode("utf-8"), capture_output=True,
                timeout=KICKOFF_TIMEOUT, check=False,
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KickoffError(f"agent call failed: {type(exc).__name__}") from None
    # Bytes, decoded here and strictly: `errors="replace"` would have turned a CP932
    # console or a broken byte into "�" inside a charter that then parses, is adopted,
    # and reaches the control plane looking like text the agent wrote.
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    if result.returncode != 0:
        detail = stderr.strip().splitlines()
        raise KickoffError(f"agent exited {result.returncode}: {detail[-1] if detail else 'unknown'}"[:300])
    try:
        return (result.stdout or b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KickoffError(f"agent output was not UTF-8 (byte {exc.start})") from None


def generate(*, name: str, brief: str, root: Path, context: RepoContext) -> dict[str, Any]:
    """Pass one: charter draft + the questions only the owner can answer."""
    prompt = _fill(_GENERATE_PROMPT, name=name, brief=brief or "(none)", context=context.as_prompt_text())
    return parse_generate(_run_agent(prompt, root))


def refine(
    *, name: str, brief: str, root: Path, context: RepoContext,
    charter_draft: dict[str, Any], questions: list[dict[str, Any]], answers: dict[str, str],
    note: str = "", previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pass two: the charter to adopt and the first tasks, from the answers.

    `note` is what the person said about the draft this one replaces — the conversation
    lets them answer a proposal in words, and that has to reach the pass that draws the
    next one. It is passed with the draft it is about, because "the second task is wrong"
    means nothing without the list it points at.
    """
    qa = "\n".join(
        f"- Q({q['id']}): {q['question']}\n  A: {answers.get(q['id'], '').strip() or '(unanswered)'}"
        for q in questions
    )
    remark = ""
    if note.strip():
        drafted = json.dumps(previous, ensure_ascii=False) if isinstance(previous, dict) else "(none)"
        remark = (
            "\n\nYou already proposed this, and the owner was not happy with it:\n"
            f"{drafted}\n\nWhat they said about it — this is the instruction for the "
            f"draft you are about to write:\n{note.strip()}"
        )
    prompt = _fill(
        _REFINE_PROMPT,
        name=name, brief=brief or "(none)", context=context.as_prompt_text(),
        draft=json.dumps(charter_draft, ensure_ascii=False), qa=qa,
    ) + remark
    return parse_refine(_run_agent(prompt, root))


#: An idea the owner types into the project conversation, and the lines the agent answers
#: with. Both bounded, so a proposal stays something a person reads rather than scrolls.
IDEA_CHARS = 4_000
IDEA_LINE_CHARS = 1_000
#: The most tasks of the development order that are shown to the agent and offered as
#: positions. Past it the conversation says the order was only partly seen, rather than
#: proposing a place inside a list it does not know the end of.
ORDER_SHOWN = 50

IDEA_VERDICTS = frozenset({"task", "later", "note"})


def parse_idea(text: str, *, order_length: int) -> dict[str, Any]:
    return _first_that_parses(
        text, must_have="verdict", build=lambda raw: _build_idea(raw, order_length=order_length),
    )


def _build_idea(raw: dict[str, Any], *, order_length: int) -> dict[str, Any]:
    verdict = _text(raw.get("verdict"), "verdict", required=True).strip().lower()
    if verdict not in IDEA_VERDICTS:
        raise KickoffError(f"agent output's verdict {verdict!r} is not one of {sorted(IDEA_VERDICTS)}")
    echo = _text(raw.get("echo"), "echo", required=True)
    why = _text(raw.get("why"), "why", required=False)
    for what, line in (("echo", echo), ("why", why)):
        if len(line) > IDEA_LINE_CHARS:
            raise KickoffError(f"agent output's {what} is longer than {IDEA_LINE_CHARS} characters")
    task: dict[str, Any] | None = None
    position: int | None = None
    if verdict == "task":
        # A "task" verdict with no card is not a proposal anyone can act on, and a card
        # with no place in the order is one the conversation cannot offer a position for.
        # Refused whole rather than half-shown: asking again costs one click, and half a
        # proposal reads as a decision that was already made.
        task = _one_task(raw.get("task"))
        value = raw.get("position")
        if isinstance(value, bool) or not isinstance(value, int):
            raise KickoffError("agent output's position was not an integer")
        # 1 .. len+1 — every gap between the tasks it was shown, plus the end. Outside
        # that it is not a position in *this* order, and a clamp would quietly move the
        # card somewhere the agent never proposed.
        if not 1 <= value <= order_length + 1:
            raise KickoffError(f"agent output's position {value} is outside 1..{order_length + 1}")
        position = value
    elif raw.get("task") is not None or raw.get("position") is not None:
        # A verdict of "note" or "later" carrying a card is the agent contradicting
        # itself, and dropping the card silently would hide a piece of work it *did*
        # propose behind a one-line remark. Refused like every other broken answer:
        # the route turns it into a 502 and the person asks again.
        raise KickoffError(f"agent output's verdict is {verdict!r} but it also proposed a task")
    return {"verdict": verdict, "echo": echo, "why": why, "task": task, "position": position}


def _one_task(value: Any) -> dict[str, Any]:
    """One task object, held to exactly what the proposal's tasks are held to."""
    if not isinstance(value, dict):
        raise KickoffError("agent output had no task object")
    kind = _text(value.get("kind"), "task kind", required=True).lower()
    if kind not in _TASK_KINDS:
        raise KickoffError(f"agent output's task kind {kind!r} is not one of {sorted(_TASK_KINDS)}")
    title = _text(value.get("title"), "task title", required=True)
    description = _text(value.get("description"), "task description", required=True)
    if len(title) > TITLE_CHARS:
        raise KickoffError(f"agent output's task title is {len(title)} characters; at most {TITLE_CHARS}")
    if len(description) > DESCRIPTION_CHARS:
        raise KickoffError(
            f"agent output's task description is {len(description)} characters; at most {DESCRIPTION_CHARS}"
        )
    return {"title": title, "description": description, "kind": kind}


def propose_from_idea(
    *, name: str, root: Path, charter: dict[str, Any], order: list[dict[str, Any]], idea: str,
) -> dict[str, Any]:
    """The project conversation after adoption: the owner says something, the agent says
    what it is — work to do now, a second version, or a remark — and, when it is work,
    proposes a card with a place in the development order.

    One call, nothing written. The card becomes a task only when the person picks a
    position, and that is a separate request: what the agent proposes and what the person
    accepts stay two events, the same split the kickoff proposal has.
    """
    listed = "\n".join(
        f"{index + 1}. [{task.get('state', '')}] {task.get('title', '')}"
        for index, task in enumerate(order)
    ) or "(no tasks yet)"
    prompt = _fill(
        _IDEA_PROMPT,
        name=name,
        charter=json.dumps(charter_triplet(charter), ensure_ascii=False),
        order=listed,
        count=str(len(order)),
        append=str(len(order) + 1),
        idea=idea,
    )
    return parse_idea(_run_agent(prompt, root), order_length=len(order))


# --- the record in the repository -------------------------------------------------------

@dataclass(frozen=True)
class KickoffRead:
    """One read of ``.agora/kickoff.json``: the record, or which way it is not one."""
    available: bool
    #: "absent" (never started) | "unreadable" | "malformed" when unavailable.
    reason: str | None
    record: dict[str, Any] | None
    source: str

    def payload(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "record": self.record, "source": self.source}

    def fingerprint(self) -> str:
        """Everything a later read would have to equal for "nothing moved" to be true:
        the reason when unavailable (absent is not malformed), the whole record when
        available. A round id alone is not enough — answers, a proposal, an adoption can
        all land inside one round while an ask is out."""
        if not self.available or self.record is None:
            return f"unavailable:{self.reason}"
        return "record:" + json.dumps(self.record, sort_keys=True, ensure_ascii=False)


def read_kickoff(root: Path, project_id: str | None = None) -> KickoffRead:
    """Read the project's record. With `project_id`, a record another project wrote into
    the same directory — the registry allows two ids on one root — reads as `foreign`:
    not this project's, not "not started", and not a thing this project may write over."""
    path = root / KICKOFF_RELATIVE_PATH
    source = str(KICKOFF_RELATIVE_PATH)
    # A project root that is not there is not "never started": the write would refuse it,
    # so a start offered on that reading would run the agent and fail at the end.
    # The root itself is followed: a project registered through a symlink to its real
    # directory is ordinary on POSIX. The boundary the reads and writes hold is *inside*
    # the root — `.agora`, the record, the README, `.git` — not the way to the root.
    root_probe = probe_path(root, follow_symlinks=True)
    if root_probe.absent or (root_probe.present and root_probe.kind != "dir"):
        return KickoffRead(False, "root_missing", None, source)
    if root_probe.unknown:
        return KickoffRead(False, "unreadable", None, source)
    # The read honours the same boundary as the write: `.agora` or `kickoff.json` being a
    # link (a symlink, a Windows junction) means the record would come from wherever the
    # link points, which is not "the project's record". Said as `linked`, neither read nor
    # ever written over — the write refuses the same paths.
    try:
        if _is_link(path.parent) or _is_link(path):
            return KickoffRead(False, "linked", None, source)
    except OSError:
        return KickoffRead(False, "unreadable", None, source)
    # The file itself is then opened and checked on its descriptor. The *directory* is
    # inspected here and opened through by path a moment later: a process with write
    # access to the project root that swaps `.agora` for a junction inside that gap is
    # not stopped on the read any more than on the write — workspace CR-56 (`defer`)
    # names the condition and the fix (directory-handle-anchored opens).
    probe = probe_path(path, follow_symlinks=False)
    # `absent` and `unknown` are distinct on the probe: a definite nothing at the path,
    # versus a stat that could not complete (a denying ACL, an I/O error). Only the first
    # is "never started".
    if probe.absent:
        return KickoffRead(False, "absent", None, source)
    if probe.unknown or not probe.present:
        return KickoffRead(False, "unreadable", None, source)
    if probe.kind != "file":
        return KickoffRead(False, "malformed", None, source)
    try:
        raw = json.loads(_read_plain_text(path, RECORD_CHARS, whole=True)[0])
    except FileTooLarge:
        # Longer than any record this flow writes (the write refuses past the same bound):
        # not a record, and not read in part — a prefix that happened to parse would have
        # been "present", and the next save would have replaced the whole file, tail and all.
        return KickoffRead(False, "malformed", None, source)
    except OSError:
        return KickoffRead(False, "unreadable", None, source)
    except (ValueError, UnicodeDecodeError, RecursionError):
        # JSONDecodeError is a ValueError; so is the refusal of an integer literal past
        # the interpreter's digit limit, and nesting past the recursion limit raises
        # RecursionError. All of them: not a record — never a 500.
        return KickoffRead(False, "malformed", None, source)
    if not _is_record(raw):
        return KickoffRead(False, "malformed", None, source)
    if project_id is not None and raw["project_id"] != project_id:
        return KickoffRead(False, "foreign", None, source)
    return KickoffRead(True, None, raw, source)


RECORD_STATUSES = {"questions", "answered", "proposed", "adopting", "adopted"}


def _strs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _is_charter(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("goal"), str)
        and _strs(value.get("non_goals"))
        and _strs(value.get("success_criteria"))
    )


def _is_adoptable_charter(value: Any) -> bool:
    """`_is_charter`, plus what the adopt route requires: a non-blank goal, at least one
    non-blank success criterion, every field within CHARTER_FIELD_CHARS."""
    if not _is_charter(value):
        return False
    goal = value["goal"].strip()
    criteria = [c.strip() for c in value["success_criteria"]]
    non_goals = [n.strip() for n in value["non_goals"]]
    items = [*criteria, *non_goals]
    return (
        bool(goal) and bool(criteria) and all(criteria)
        and len(criteria) <= CHARTER_LIST_ITEMS and len(non_goals) <= CHARTER_LIST_ITEMS
        and 0 < len(goal) <= CHARTER_FIELD_CHARS and all(0 < len(f) <= CHARTER_ITEM_CHARS for f in items)
    )


def _is_approved_task(value: Any) -> bool:
    """`_is_task` with a key, plus what the adopt route requires of a task."""
    if not (_is_task(value) and isinstance(value.get("key"), str) and value["key"]):
        return False
    title, description, kind = value["title"].strip(), value["description"].strip(), value["kind"].strip().lower()
    return (
        0 < len(title) <= TITLE_CHARS and 0 < len(description) <= DESCRIPTION_CHARS and kind in TASK_KINDS
    )


def _is_task(value: Any) -> bool:
    return (
        isinstance(value, dict) and isinstance(value.get("title"), str)
        and isinstance(value.get("kind"), str) and isinstance(value.get("description"), str)
    )


def _is_record(raw: Any) -> bool:
    """The shape the flow reads — every field the UI dereferences, checked per status. A
    file with only a status is not a record: the UI would read `questions.length` off it
    and the answers route would 500 — "malformed" is the honest word, and it is said
    before anything touches the file."""
    if not isinstance(raw, dict) or raw.get("status") not in RECORD_STATUSES:
        return False
    if not isinstance(raw.get("brief"), str) or not isinstance(raw.get("round"), str):
        return False
    if not isinstance(raw.get("project_id"), str) or not raw["project_id"]:
        return False
    previous = raw.get("previous_adoptions", [])
    if not isinstance(previous, list) or not all(isinstance(r, dict) and isinstance(r.get("round"), str) for r in previous):
        return False
    if not isinstance(raw.get("previous_adoptions_dropped", 0), int):
        return False
    if not isinstance(raw.get("revision"), str) or raw["revision"] != revision_of(raw):
        # A revision that does not match the content is a record edited by hand or by a
        # merge; it is not a record this flow can safely mutate.
        return False
    answers = raw.get("answers")
    if not isinstance(answers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()):
        return False
    if not isinstance(raw.get("questions"), list) or not all(
        isinstance(q, dict) and isinstance(q.get("id"), str) and isinstance(q.get("question"), str)
        and isinstance(q.get("why"), str) and _strs(q.get("suggestions"))
        for q in raw["questions"]
    ):
        return False
    if not _is_charter(raw.get("charter_draft")) or not str(raw["charter_draft"]["goal"]).strip():
        return False
    if not isinstance(raw.get("needs_mockup"), bool) or not isinstance(raw.get("mockup_note"), str):
        return False
    if not _strs(raw.get("read_notes")):
        return False
    context = raw.get("context")
    if (
        not isinstance(context, dict) or not _strs(context.get("unread"))
        or not isinstance(context.get("readme"), bool) or not isinstance(context.get("text"), str)
    ):
        return False
    proposal = raw.get("proposal")
    if proposal is not None and not (
        isinstance(proposal, dict) and _is_charter(proposal.get("charter"))
        and isinstance(proposal.get("tasks"), list) and all(_is_task(t) for t in proposal["tasks"])
        and isinstance(proposal.get("needs_mockup"), bool) and isinstance(proposal.get("mockup_task_missing"), bool)
    ):
        return False
    if raw["status"] == "proposed" and proposal is None:
        return False
    if raw["status"] in {"adopting", "adopted"}:
        adopted = raw.get("adopted")
        # The seal is held to the *adopt route's* contract, not just to field types: a
        # resume sends the seal back through that route, and a seal with a blank
        # description, a kind outside the contract or a charter with no success criterion
        # would be refused there on every click — a record that reads as usable and can
        # never be finished. Such a record is `malformed`, and says so once.
        if not (
            isinstance(adopted, dict) and _is_adoptable_charter(adopted.get("charter"))
            and isinstance(adopted.get("approved"), list) and len(adopted["approved"]) <= APPROVED_TASKS
            and all(_is_approved_task(t) for t in adopted["approved"])
            and isinstance(adopted.get("tasks"), list) and all(isinstance(t, dict) and isinstance(t.get("key"), str) for t in adopted["tasks"])
            and isinstance(adopted.get("charter_written", False), bool)
            and (adopted.get("charter_before") is None or _is_charter(adopted.get("charter_before")))
            and isinstance(adopted.get("charter_kept_edit", False), bool)
            and (adopted.get("sealed_from_revision") is None or isinstance(adopted.get("sealed_from_revision"), str))
        ):
            return False
    return True


def _is_link(path: Path) -> bool:
    """A symlink, or on Windows a directory junction — `lstat` reports a junction as a
    plain directory, so it has to be asked separately. `Path.is_junction` exists from
    Python 3.12; on the 3.10/3.11 this project also supports the reparse-point attribute
    on `lstat` is the same question asked lower down, so the guard does not quietly stop
    guarding on an older interpreter."""
    try:
        if path.is_symlink():
            return True
        if sys.platform == "win32":
            try:
                attributes = os.lstat(path).st_file_attributes  # type: ignore[attr-defined]
            except FileNotFoundError:
                return False
            reparse = getattr(statmod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if not attributes & reparse:
                return False
            # A reparse point is not always a link: OneDrive placeholders and other cloud
            # files carry one too. Only a symlink or a junction (mount point) redirects the
            # path, so only those two tags count.
            tag = getattr(os.lstat(path), "st_reparse_tag", 0)
            link_tags = {getattr(statmod, "IO_REPARSE_TAG_SYMLINK", 0xA000000C), getattr(statmod, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)}
            return tag in link_tags
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if is_junction is not None else False
    except FileNotFoundError:
        return False
    # Any other OSError (a disconnected share, a denying ACL) is the caller's to name as
    # "could not be inspected" — not as "a link", which would send the person to replace
    # a real directory.


def _plain(path: Path, expect: str) -> None:
    """Refuse unless `path` is absent or a plain entry of the expected kind."""
    try:
        linked = _is_link(path)
    except OSError as exc:
        raise PathUnsafe(f"refusing to write: {path.name} could not be inspected ({type(exc).__name__})") from None
    if linked:
        raise PathUnsafe(f"refusing to write through {path.name}: it is a link")
    probe = probe_path(path, follow_symlinks=False)
    if probe.unknown:
        raise PathUnsafe(f"refusing to write: {path.name} could not be inspected")
    if probe.present and probe.kind != expect:
        raise PathUnsafe(f"refusing to write through {path.name}: it is not a plain {expect}")


def task_key(round_id: str, index: int, task: dict[str, Any]) -> str:
    """The key an approved task is created under and found by. Round and position alone
    were not identity: the record is versioned with the project, two branches can carry
    the same round with a different first task, and the one created on branch A would be
    found as branch B's and B's never created. The content is in the key, so "found by
    key" means "this task, as approved"."""
    body = json.dumps(
        {"title": task.get("title", ""), "description": task.get("description", ""), "kind": task.get("kind", "")},
        sort_keys=True, ensure_ascii=False,
    )
    return f"{round_id}-{index + 1}-{hashlib.sha256(body.encode('utf-8')).hexdigest()[:10]}"


def idea_key(task: dict[str, Any]) -> str:
    """The key a card accepted in the project conversation is created under.

    Content only — no round, no position. The point is a *retry*: the request that
    created the task and lost its answer sends the same card again, and must find the
    task rather than make a second one. Position is deliberately out of it, so a double
    click that lands on two different chips still means one card.

    The cost, said plainly: two cards with the same title, description and kind are one
    card to this key, so a person who wants a second copy of an identical task cannot get
    it from the conversation. That is the trade for never silently doubling one.
    """
    body = json.dumps(
        {"title": task.get("title", ""), "description": task.get("description", ""), "kind": task.get("kind", "")},
        sort_keys=True, ensure_ascii=False,
    )
    return f"idea-{hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]}"


def adoption_receipt(record: dict[str, Any]) -> dict[str, Any] | None:
    """What a later round keeps of an adopted record: enough to know what was approved and
    what it became, not the questions and answers (those are the round's, and gone with it)."""
    if record.get("status") != "adopted" or not isinstance(record.get("adopted"), dict):
        return None
    adopted = record["adopted"]
    return {
        "round": str(record.get("round") or ""),
        "adopted_at": record.get("adopted_at"),
        "charter": charter_triplet(adopted.get("charter")),
        # The whole approved task, description included: the description is where the
        # acceptance lived, and the receipt is the only copy of it the repository keeps.
        "approved": [
            {"key": t.get("key"), "title": t.get("title"), "description": t.get("description", ""), "kind": t.get("kind")}
            for t in adopted.get("approved", []) if isinstance(t, dict)
        ],
        "tasks": [
            {"key": t.get("key"), "task_id": t.get("task_id")}
            for t in adopted.get("tasks", []) if isinstance(t, dict)
        ],
        "charter_kept_edit": bool(adopted.get("charter_kept_edit", False)),
    }


#: The most the carried receipts may weigh, serialized — a quarter of what a record may
#: be at all, so a round's own content always has room. Past it the *oldest* receipts are
#: dropped, and the record says how many, so the loss is a stated fact, not a silence.
RECEIPTS_CHARS = 1_000_000


def previous_adoptions_after(record: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int]:
    """The receipts a new round starts with: the old record's own list, plus the old
    record itself if it was adopted — newest last — trimmed from the oldest end to the
    byte budget. Returns the list and how many were dropped, the old record's own count
    included."""
    if not record:
        return [], 0
    kept = [r for r in record.get("previous_adoptions", []) if isinstance(r, dict)]
    receipt = adoption_receipt(record)
    receipts = kept + ([receipt] if receipt else [])
    dropped = int(record.get("previous_adoptions_dropped") or 0)
    while receipts and len(json.dumps(receipts, ensure_ascii=False).encode("utf-8")) > RECEIPTS_CHARS:
        receipts.pop(0)
        dropped += 1
    return receipts, dropped


def adopted_record_fits(record: dict[str, Any]) -> bool:
    """Whether a record sealed as `adopting` can still be written once it is `adopted`:
    every approved task recorded with an id, the time stamped, the flags set. Checked
    *before* the seal, because the writes after it follow side effects (a charter, tasks
    in the store) and a record that outgrew the bound then would be stuck half done, with
    a restart refused and every retry failing the same way."""
    projected = json.loads(json.dumps(record, ensure_ascii=False))
    adopted = projected.get("adopted") or {}
    adopted["tasks"] = [
        {"key": t.get("key"), "task_id": "x" * 40, "title": t.get("title"), "kind": t.get("kind")}
        for t in adopted.get("approved", []) if isinstance(t, dict)
    ]
    # The longer spelling of each flag ("false" is a byte more than "true"): the
    # projection must never come in under what the adopted record can be.
    adopted["charter_written"] = False
    adopted["charter_kept_edit"] = False
    projected["adopted"] = adopted
    projected["status"] = "adopted"
    projected["adopted_at"] = now_iso()
    projected["revision"] = "0" * 16
    return len((json.dumps(projected, ensure_ascii=False, indent=2) + "\n").encode("utf-8")) <= RECORD_CHARS


def seal_fingerprint(adopted: Any) -> str:
    """What this control plane records as "applied": the approved charter and the approved
    task keys, hashed. Not the round id — two branches can fork one questions record,
    adopt different things under the same round, and only one of them was applied here."""
    raw = adopted if isinstance(adopted, dict) else {}
    body = json.dumps(
        {"charter": charter_triplet(raw.get("charter")), "keys": [t.get("key") for t in raw.get("approved", []) if isinstance(t, dict)]},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def charter_triplet(charter: Any) -> dict[str, Any]:
    """The three fields a charter is compared on — the store may carry more (timestamps),
    and the comparison "is the project's charter still what it was before adopting" must
    not be broken by those."""
    raw = charter if isinstance(charter, dict) else {}
    return {
        "goal": str(raw.get("goal") or "").strip(),
        "non_goals": [str(v) for v in (raw.get("non_goals") or [])],
        "success_criteria": [str(v) for v in (raw.get("success_criteria") or [])],
    }


def revision_of(record: dict[str, Any]) -> str:
    """The record's concurrency token: a hash of its content. A counter was the first
    shape and it was wrong for a file that is versioned with the project — two branches
    can hold "revision 4" with different answers in it, and a tab that read one would
    have passed the check against the other. Content that is the same hashes the same,
    which is the one case where "nothing moved" is true."""
    body = {k: v for k, v in record.items() if k != "revision"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest[:16]


def _replace_durably(src: Path, dst: Path) -> None:
    """Rename `src` over `dst` so that the rename itself survives a power cut.

    POSIX: `os.replace`, then `fsync` the directory — the rename is a directory write.
    Windows: `os.replace` is `MoveFileExW` without write-through, and a directory cannot
    be opened for `fsync` from here; `MoveFileExW` with `MOVEFILE_WRITE_THROUGH` is the
    platform's way to say "return only once the rename is on disk", and that is what is
    called. Either way the seal is on disk before the tasks are written."""
    if sys.platform == "win32":
        import ctypes
        MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH = 0x1, 0x8
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.MoveFileExW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        kernel32.MoveFileExW.restype = ctypes.c_int
        ok = kernel32.MoveFileExW(str(src), str(dst), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)
        if not ok:
            raise ctypes.WinError()  # type: ignore[attr-defined]
        return
    os.replace(src, dst)
    dfd = os.open(dst.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def write_kickoff(root: Path, record: dict[str, Any]) -> None:
    """Write the record into the project — and only into the project.

    What the guard covers, exactly:
    * the temporary file — created under a fresh random name with `O_EXCL`, so nothing
      that was placed at the name first (a symlink to a file elsewhere) can be opened
      through: the create fails instead. No check-then-write gap on this file.
    * `.agora` and `kickoff.json` — refused when they are links (a symlink, a Windows
      junction) *as inspected just before the write*. A process with write access to the
      project root that swaps `.agora` for a junction in the gap between that look and
      the rename is not stopped by this: closing that would take a directory handle the
      write is anchored to (`openat`), which Windows does not offer this code. Workspace
      CR-56 names the condition under which that matters.
    The revision is computed here, from the content, so it can never be stale against it."""
    path = root / KICKOFF_RELATIVE_PATH
    _plain(path.parent, "dir")
    _plain(path, "file")
    record["revision"] = revision_of(record)
    body = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(body) > RECORD_CHARS:
        # The read stops at RECORD_CHARS bytes; a record past it would be written over a
        # good one and then read as malformed, with no way back. Refused before the write.
        raise RecordTooLarge(f"the record would be {len(body)} bytes; the most that can be read back is {RECORD_CHARS}")
    if not root.is_dir():
        # A root that is gone (moved, deleted, misregistered) is not recreated as an empty
        # tree holding one record: that would split the registry from the real repository.
        raise ProjectRootMissing(f"the project root does not exist: {root}")
    agora_dir_created = not path.parent.exists()
    path.parent.mkdir(exist_ok=True)
    if agora_dir_created and sys.platform != "win32":
        # The first kickoff creates `.agora`; that is a write to the project root's
        # directory, and it is synced like the rename below is, or a power cut could take
        # the whole directory with the seal in it. (Windows: NTFS journals the creation.)
        dfd = os.open(path.parent.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    tmp = path.parent / f"kickoff.json.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            # "Sealed before any side effect" has to survive a power cut, or it is not a
            # seal: the tasks land in SQLite (which fsyncs) right after this returns, and
            # a record that came back from the crash as the *previous* version would
            # offer the adoption again over tasks it does not know about.
            handle.flush()
            os.fsync(handle.fileno())
        _replace_durably(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_round_id() -> str:
    """One kickoff round. Task keys carry it, so a round started after an adoption
    cannot mistake the previous round's tasks for its own crash recovery."""
    return uuid.uuid4().hex[:10]


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _directory_identity(root: Path) -> str:
    """The lock key: the directory's identity where the platform gives one (device and
    file id — the same directory reached as `Z:\\p` and `\\\\server\\share\\p` is one
    key), the normalised resolved path where it does not (a directory not yet there)."""
    try:
        st = os.stat(root)
        if st.st_dev and st.st_ino:
            return f"id:{st.st_dev}:{st.st_ino}"
    except OSError:
        pass
    return "path:" + os.path.normcase(str(Path(root).resolve()))


def project_lock(root: Path) -> threading.Lock:
    """Serialises the record's read-compare-write steps per *directory* within this process.

    Keyed by the root, not the project id: the record is a file in the directory, and the
    registry allows two project ids on one root — two locks over one file would be no lock.
    The control plane is one process over one SQLite file, so a process-wide lock is the
    whole story here; two requests for one directory — a double click, two tabs — take
    their turns, and the second sees what the first wrote. A multi-process deployment
    would need the lock elsewhere; nothing in this control plane runs that way, and this
    module is not the place that decision would be made.
    """
    key = _directory_identity(root)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock
