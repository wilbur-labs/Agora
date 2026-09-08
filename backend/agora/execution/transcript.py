"""Turn one agent CLI's event stream into display text, as it arrives.

Every supported CLI can emit JSON Lines (codex ``--json``, claude
``--output-format stream-json``, kiro ``--output-format stream-json`` on the v2
engine), and the conversation window needs three things out of that stream while
the process is still running:

1. **text to show**, incrementally — a window that shows nothing for three
   minutes is indistinguishable from a broken one;
2. **the provider's session id**, which is what a later turn resumes; and
3. **an honest account of what was not understood.** An event kind this reader
   does not know is written into the transcript as an unread event, never
   dropped. A transcript that silently omits what it could not parse reads as a
   complete record of the turn, and the reader has no way to tell the difference.

Nothing here decides token usage or cost: ``agora.orchestration.provider_usage``
owns that, reading the same stdout once the run has settled.
"""
from __future__ import annotations

import json
from typing import Any

from .adapters import (
    EVENT_FORMAT_CLAUDE_STREAM_JSON,
    EVENT_FORMAT_CODEX_JSONL,
    EVENT_FORMAT_KIRO_STREAM_JSON,
    EVENT_FORMAT_PLAIN,
)

#: Longest command echo or tool output kept per event. The transcript is a
#: reading surface, not the archive: a run's deliverables and its settled output
#: are recorded elsewhere, in full, by their own writers.
_ECHO_LIMIT = 2_000


class TranscriptReader:
    """Base reader: accumulates the session id and unread-event notes.

    Subclasses implement :meth:`_read`. ``feed`` handles the parts every format
    shares — a blank line, a line that is not JSON at all, and a JSON value that
    is not an object — because each of those is a fact about the stream worth
    showing rather than a reason to stop reading.
    """

    #: Top-level event kinds this reader deliberately shows nothing for. Anything
    #: outside both this set and the rendering branches is reported as unread, so
    #: adding a kind here is an explicit statement that it carries nothing a
    #: reader of the transcript needs.
    silent_kinds: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.session_id: str | None = None
        #: Event kinds seen but not understood, in first-seen order. Reported
        #: once each: a stream that repeats an unknown kind two hundred times
        #: would otherwise bury the conversation under the same note.
        self.unread_kinds: list[str] = []
        #: True when the provider said its own final text was cut short. Only
        #: kiro reports this today, and it must survive into what the user sees:
        #: a truncated answer shown as a whole one is the product lying about
        #: what it read.
        self.final_text_truncated = False
        self._malformed_lines = 0
        #: Whole lines of the events a settlement is read from, kept as they
        #: stream past. The run's retained stdout is a byte *tail*, and a final
        #: answer can be one JSON line larger than that tail — claude's
        #: ``result``, kiro's ``runFinished``, codex's ``agent_message``. Cut in
        #: the middle, the semantic text becomes a JSON fragment and the token,
        #: cost and credit figures vanish with it. Keeping the line here is the
        #: only place it is ever whole.
        #:
        #: Keyed, not appended: one line per role, last one wins, so a long turn
        #: does not accumulate every message it ever printed.
        self.settlement: dict[str, str] = {}
        #: Set when one turn's stream named **two different** sessions. The work
        #: went into the second one while the record still points at the first,
        #: so resuming the first later would carry on without the turn that just
        #: happened — and might redo or overwrite the files it wrote. A note in
        #: the transcript was not enough: this has to reach the session record and
        #: stop the next resume.
        self.session_conflict: tuple[str, str] | None = None

    def feed(self, line: str) -> str:
        """Read one line of the provider's stdout; return text to display."""
        stripped = line.strip()
        if not stripped:
            return ""
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:  # noqa: PERF203 - the recovery is the point
            # Not JSON. Either the CLI wrote a plain-text diagnostic among its
            # events, or the line is a fragment. Shown, not swallowed, and
            # counted so a stream that is entirely unparseable cannot look like
            # a stream that was simply quiet.
            self._malformed_lines += 1
            return _line(stripped)
        if not isinstance(event, dict):
            self._malformed_lines += 1
            return _line(str(event))
        self._keep_settlement(event, stripped)
        return self._read(event)

    def finish(self) -> str:
        """Any closing note for the transcript, after the process has exited."""
        notes: list[str] = []
        if self.unread_kinds:
            notes.append(
                "· 本轮有 Agora 未能解读的事件类型："
                + "、".join(self.unread_kinds)
                + "（已如实记下，未当作没发生）"
            )
        if self.final_text_truncated:
            notes.append("· 供应商声明它自己的最终文本被截断了，上面显示的不是全文")
        return "".join(_line(note) for note in notes)

    @property
    def malformed_lines(self) -> int:
        return self._malformed_lines

    def _read(self, event: dict[str, Any]) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def _keep_settlement(self, event: dict[str, Any], line: str) -> None:
        """Retain this line if a settlement is read from events of its kind.

        Default: nothing. Each format's reader names its own, because "which
        events carry the answer and its cost" is exactly the per-vendor knowledge
        this module exists to hold.
        """
        return None

    def _unread(self, kind: str) -> str:
        if kind in self.unread_kinds:
            return ""
        self.unread_kinds.append(kind)
        return _line(f"· 未解读的事件：{kind}")

    def _remember_session(self, value: Any) -> None:
        """Record the provider's session id, and never let it change silently.

        A second, different id inside one turn's stream means the assumption
        behind resuming — one call, one session — does not hold for this
        provider. Keeping the first and noting the conflict is the readable
        failure; overwriting would hand a later turn an id chosen by chance.
        """
        if not isinstance(value, str) or not value.strip():
            return
        candidate = value.strip()
        if self.session_id is None:
            self.session_id = candidate
        elif self.session_id != candidate:
            self._unread(f"session-id-conflict:{candidate[:16]}")
            if self.session_conflict is None:
                self.session_conflict = (self.session_id, candidate)


class PlainTextReader(TranscriptReader):
    """No event format: the CLI's stdout is the transcript."""

    def feed(self, line: str) -> str:
        return line if line.endswith("\n") else _line(line.rstrip("\r\n"))

    def _read(self, event: dict[str, Any]) -> str:  # pragma: no cover - unused
        return ""


class CodexJsonlReader(TranscriptReader):
    """codex 0.149.1 ``exec --json`` / ``exec resume --json``.

    Event vocabulary verified by running both commands on 2026-09-08:
    ``thread.started`` (carries ``thread_id``), ``turn.started``,
    ``item.started``/``item.completed`` (``agent_message`` / ``file_change`` /
    ``command_execution``), ``turn.completed`` (carries ``usage``).
    """

    silent_kinds = frozenset({"turn.started", "turn.completed", "thread.started"})

    def _keep_settlement(self, event: dict[str, Any], line: str) -> None:
        kind = str(event.get("type", ""))
        if kind == "turn.completed":
            self.settlement["usage"] = line
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                self.settlement["message"] = line

    def __init__(self) -> None:
        super().__init__()
        #: Item ids whose command line has already been echoed. Codex sends a
        #: command as ``item.started`` and then again as ``item.completed``; the
        #: echo belongs to whichever arrives first, because either can be the
        #: only one this reader sees — a fast command may be reported once, and a
        #: reader attached late misses the start.
        self._echoed_commands: set[str] = set()

    def _read(self, event: dict[str, Any]) -> str:
        kind = str(event.get("type", ""))
        if kind == "thread.started":
            self._remember_session(event.get("thread_id"))
            return ""
        if kind in {"item.started", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict):
                return self._unread(f"{kind}(no item)")
            return self._read_item(kind, item)
        if kind in {"turn.failed", "error"}:
            return _line(f"✖ {_text_of(event.get('error') or event.get('message'))}")
        if kind in self.silent_kinds:
            return ""
        return self._unread(kind)

    def _read_item(self, kind: str, item: dict[str, Any]) -> str:
        item_type = str(item.get("type", ""))
        completed = kind == "item.completed"
        if item_type == "agent_message":
            # Codex sends the whole message once, on completion; the started
            # event for the same item carries no text worth showing twice.
            return _line(_text_of(item.get("text"))) if completed else ""
        if item_type == "reasoning":
            return _line(f"· {_clip(_text_of(item.get('text')))}") if completed else ""
        if item_type == "file_change":
            if not completed:
                return ""
            changes = item.get("changes")
            if not isinstance(changes, list):
                return self._unread("file_change(no changes)")
            rendered = [
                _line(f"✎ {change.get('kind', '?')} {change.get('path', '?')}")
                for change in changes
                if isinstance(change, dict)
            ]
            return "".join(rendered)
        if item_type == "command_execution":
            item_id = str(item.get("id", ""))
            echo = ""
            if item_id not in self._echoed_commands:
                self._echoed_commands.add(item_id)
                echo = _line(f"$ {_clip(_text_of(item.get('command')))}")
            if not completed:
                return echo
            output = _clip(_text_of(item.get("aggregated_output"))).rstrip()
            exit_code = item.get("exit_code")
            body = "".join(_line(f"  {row}") for row in output.splitlines()) if output else ""
            tail = "" if exit_code in (0, None) else _line(f"  (exit {exit_code})")
            return echo + body + tail
        return self._unread(f"codex item:{item_type}")


class ClaudeStreamJsonReader(TranscriptReader):
    """claude 2.1.246 ``-p --output-format stream-json --include-partial-messages``.

    Text arrives twice: token by token as ``stream_event`` deltas, then whole
    inside the ``assistant`` message. The deltas are what makes the window feel
    alive, so they are what gets shown, and the assistant message's *text*
    blocks are skipped as already-shown. Its tool-use blocks are not: those never
    appear as text deltas.

    ``--verbose`` is mandatory for this format (the CLI exits 1 without it), and
    it is why ``system`` hook/status chatter shows up in the stream at all; that
    chatter is deliberately silent here.
    """

    silent_kinds = frozenset({"system", "rate_limit_event", "result", "user"})

    def _keep_settlement(self, event: dict[str, Any], line: str) -> None:
        if str(event.get("type", "")) == "result":
            self.settlement["result"] = line

    def _read(self, event: dict[str, Any]) -> str:
        kind = str(event.get("type", ""))
        self._remember_session(event.get("session_id"))
        if kind == "stream_event":
            inner = event.get("event")
            return self._read_stream_event(inner) if isinstance(inner, dict) else ""
        if kind == "assistant":
            message = event.get("message")
            return self._read_assistant(message) if isinstance(message, dict) else ""
        if kind in self.silent_kinds:
            return ""
        return self._unread(kind)

    def _read_stream_event(self, inner: dict[str, Any]) -> str:
        if str(inner.get("type", "")) != "content_block_delta":
            return ""
        delta = inner.get("delta")
        if not isinstance(delta, dict) or str(delta.get("type", "")) != "text_delta":
            return ""
        # Deliberately unterminated: consecutive deltas are one sentence being
        # typed, and a newline per delta would shred it.
        return _text_of(delta.get("text"))

    def _read_assistant(self, message: dict[str, Any]) -> str:
        blocks = message.get("content")
        if not isinstance(blocks, list):
            return ""
        rendered: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", ""))
            if block_type == "text":
                # Already streamed as deltas. Closed off here so the next thing
                # written starts on its own line.
                rendered.append("\n")
            elif block_type == "tool_use":
                rendered.append(_line(f"$ {block.get('name', '?')} {_clip(_compact(block.get('input')))}"))
            elif block_type == "thinking":
                rendered.append(_line(f"· {_clip(_text_of(block.get('thinking')))}"))
            else:
                rendered.append(self._unread(f"claude block:{block_type}"))
        return "".join(rendered)


class KiroStreamJsonReader(TranscriptReader):
    """kiro-cli 2.19.1 ``chat --agent-engine v2 --output-format stream-json``.

    ACP events, verified on 2026-09-08: ``runStarted``, ``metadata`` (carries
    ``sessionId``), ``sessionUpdate`` (``agent_message_chunk`` and the other ACP
    update kinds), ``runFinished`` (carries ``finalText`` and, crucially,
    ``finalTextTruncated``), ``runError``.

    The v1 engine refuses this output format and says so as a ``runError`` — so a
    misconfigured engine surfaces as a readable error rather than an empty
    transcript.
    """

    silent_kinds = frozenset({"runStarted", "metadata"})

    def _keep_settlement(self, event: dict[str, Any], line: str) -> None:
        kind = str(event.get("type", ""))
        data = event.get("data")
        if kind == "runFinished":
            self.settlement["finished"] = line
        elif kind == "metadata" and isinstance(data, dict) and data.get("meteringUsage"):
            # Only a metering report; the other metadata events carry nothing a
            # settlement is read from.
            self.settlement["metering"] = line

    def _read(self, event: dict[str, Any]) -> str:
        kind = str(event.get("type", ""))
        data = event.get("data")
        data = data if isinstance(data, dict) else {}
        self._remember_session(data.get("sessionId"))
        if kind == "sessionUpdate":
            update = data.get("update")
            return self._read_update(update) if isinstance(update, dict) else ""
        if kind == "runFinished":
            if data.get("finalTextTruncated") is True:
                self.final_text_truncated = True
            return ""
        if kind == "runError":
            stage = _text_of(data.get("stage")) or "?"
            return _line(f"✖ [{stage}] {_text_of(data.get('message'))}")
        if kind in self.silent_kinds:
            return ""
        return self._unread(kind)

    def _read_update(self, update: dict[str, Any]) -> str:
        update_kind = str(update.get("sessionUpdate", ""))
        if update_kind == "agent_message_chunk":
            content = update.get("content")
            if isinstance(content, dict) and str(content.get("type", "")) == "text":
                return _text_of(content.get("text"))
            return self._unread("kiro chunk(non-text)")
        if update_kind == "agent_thought_chunk":
            content = update.get("content")
            if isinstance(content, dict):
                return _line(f"· {_clip(_text_of(content.get('text')))}")
            return ""
        if update_kind in {"tool_call", "tool_call_update"}:
            title = _text_of(update.get("title")) or _text_of(update.get("kind")) or "?"
            status = _text_of(update.get("status"))
            return _line(f"$ {_clip(title)}{f' ({status})' if status else ''}")
        if update_kind == "plan":
            return _line("· 收到计划更新")
        return self._unread(f"kiro update:{update_kind}")


_READERS = {
    EVENT_FORMAT_CODEX_JSONL: CodexJsonlReader,
    EVENT_FORMAT_CLAUDE_STREAM_JSON: ClaudeStreamJsonReader,
    EVENT_FORMAT_KIRO_STREAM_JSON: KiroStreamJsonReader,
    EVENT_FORMAT_PLAIN: PlainTextReader,
}


def reader_for(event_format: str) -> TranscriptReader:
    """The reader for one adapter's event format.

    An unknown format falls back to plain text rather than raising: the argv
    validation in ``build_adapter_registry`` already refuses a format with no
    reader, so reaching here means a caller built an adapter by hand, and
    showing its stdout verbatim beats showing nothing.
    """
    return _READERS.get(event_format, PlainTextReader)()


def _line(text: str) -> str:
    if not text:
        return ""
    return text if text.endswith("\n") else f"{text}\n"


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return _compact(value)


def _compact(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _clip(text: str) -> str:
    if len(text) <= _ECHO_LIMIT:
        return text
    return f"{text[:_ECHO_LIMIT]}… (截断，共 {len(text)} 字)"
