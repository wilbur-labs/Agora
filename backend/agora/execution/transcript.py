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

    def __init__(self) -> None:
        super().__init__()
        #: The run's working directory, from the stream's own ``system/init`` event.
        #: Paths are shown relative to it, as the agent's terminal shows them — a
        #: worktree path is ~110 characters here, and printing it in full made every
        #: tool line unreadable while saying nothing the reader did not know.
        self._cwd: str | None = None
        #: Open thinking blocks, by the index ``content_block_start`` gave them:
        #: ``{"tokens": <last estimate>, "shown": <any text was rendered, never
        #: unset>, "line_open": <the rendered text ended mid-line>,
        #: "signature": <accumulated ``signature_delta``>, "text": <the words
        #: shown for it so far>, "counted": <its estimate was already printed>}``.
        #: ``content_block_stop`` carries only an index, so closing one needs this.
        #:
        #: The last two fields are what lets a closing ``assistant`` copy be tied
        #: to the block it repeats — see ``_match_thinking``.
        #:
        #: ``shown`` and ``line_open`` are two questions and were briefly one
        #: field: clearing ``shown`` to re-open the marker after a trailing
        #: newline also told the close that nothing had been rendered, so a block
        #: printed its words *and* a token count (kiro review, 2026-09-22).
        self._thinking_blocks: dict[int, dict[str, Any]] = {}
        #: Blocks this message has already closed, kept only so that a closing
        #: copy arriving after its ``content_block_stop`` can still be matched.
        #: Emptied when the next message's index 0 starts.
        self._thinking_closed: list[dict[str, Any]] = []

    def _candidate_thinking(self) -> list[dict[str, Any]]:
        """Blocks a closing copy could belong to: still open, or closed this
        message. Emptied when the next message's index 0 starts."""
        return [*self._thinking_blocks.values(), *self._thinking_closed]

    def _match_thinking(
        self, signature: str, text: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        """The open block a closing ``assistant`` copy belongs to, or ``None``.

        A copy of a block whose deltas already carried the words is a duplicate;
        a copy of any other block is not, and telling them apart needs to know
        *which* block the copy is. Three things were tried:

        * **Its position in ``content``** — wrong. Measured against the installed
          CLI on 2026-09-22, ``assistant`` events arrive one **per block**, each
          carrying that block alone and each *before* its own
          ``content_block_stop``: ``content_block_start 0 (thinking) → deltas →
          assistant [thinking] → content_block_stop 0 → content_block_start 1
          (text) → deltas → assistant [text] → content_block_stop 1``. So every
          copy sits at position 0 while the stream index climbs, and reading the
          position as the index asked about block 0 for every block.
        * **A queue of indices, one taken per copy** — wrong for a different
          reason (kiro review, 2026-09-22): a copy that never arrives, or arrives
          in a shape this cannot read, leaves the queue head pointing at the
          previous block, and every later copy is then matched to the wrong one.
          Order is not identity.
        * **The block's own identity**, which is what this does. Measured on the
          same stream: the ``signature_delta`` accumulates to exactly the
          ``signature`` the closing copy carries (500 characters, byte for byte).
          The words are the fallback identity when a copy carries no signature.

        Returns ``(block, ambiguous)``. No block means the copy cannot be tied to
        one, and the caller renders it: text that cannot be matched is text that
        would otherwise be lost, and a duplicate is the cheaper mistake of the
        two. ``ambiguous`` says that was not a clean "no match" but a tie the
        stream does not let this resolve, which the caller reports.

        **The words are not a unique identity, and the signature is.** Two blocks
        in one message can carry the same words: block 0 streams "检查文件" and
        closes, block 1 streams nothing and its copy carries "检查文件" too. The
        text fallback matched block 0, saw that it had already been shown, and
        silenced block 1's only copy (kiro review, 2026-09-22). So whenever the
        words match one block while another is still waiting for its first ones,
        nothing is guessed — the tie is reported. **Whether the word-for-word
        block is open or closed makes no difference to that tie**, and a first
        version that only tested the closed ones left the same hole open for a
        block whose ``content_block_stop`` went missing (kiro review, round 3).
        """
        open_blocks = list(self._thinking_blocks.values())
        if signature:
            for state in (*open_blocks, *self._thinking_closed):
                if state["signature"] and state["signature"] == signature:
                    return state, False
            # A copy that names its block, for a block this reader never saw
            # start. Falling through to the words matched it to a *different*
            # block that happened to say the same thing, and suppressed it as
            # that block's repeat (kiro review, round 6). A signature is an
            # answer: when it matches nothing, the answer is "not one of these".
            return None, False
        if not text:
            return None, False
        candidates = [*open_blocks, *self._thinking_closed]
        # A copy that *extends* what a block streamed is that block's copy too —
        # equality alone sent it down the "no block" path, where the prefix was
        # printed a second time and the open line broken (kiro review, round 5).
        matches = [
            state for state in candidates
            if state["text"] and text.startswith(state["text"])
        ]
        if not matches:
            # Identity by elimination: exactly one block has yet to show
            # anything, and a copy whose words match no other block can only be
            # its. Counting *candidates* rather than empty ones was too narrow —
            # a message whose earlier blocks were filled in by their own copies
            # left more than one candidate, so the match failed and the block's
            # `content_block_stop` printed a token count on top of the words the
            # copy had just shown (kiro review, rounds 5 and 7).
            silent = [state for state in candidates if not state["text"]]
            if len(silent) == 1:
                return silent[0], False
            # More than one block has shown nothing, so elimination names none
            # of them. Reported, not returned as a plain miss: the copy is
            # rendered either way, but leaving every candidate unshown lets each
            # one's `content_block_stop` add a token count beside words that are
            # already on screen (kiro review, round 8).
            return None, len(silent) > 1
        if any(not state["text"] for state in (*open_blocks, *self._thinking_closed)):
            # A block that has streamed nothing could be whose copy this is, and
            # so could the one it matches word for word. Without a signature
            # nothing here can tell them apart. **Closed blocks count on both
            # sides of that tie**: a silent block that closed before its copy
            # arrived is exactly as plausible a source as an open one, and
            # testing only the open ones lost its words (kiro review, round 4).
            return None, True
        if len(matches) > 1:
            # Two blocks whose words are both a prefix of this copy — "前半" and
            # "前半和" — and nothing without a signature says which one it
            # continues. Taking the first cut the suffix at the wrong place and
            # said nothing about it (kiro review, round 6).
            return None, True
        return matches[0], False

    def _keep_settlement(self, event: dict[str, Any], line: str) -> None:
        if str(event.get("type", "")) == "result":
            self.settlement["result"] = line

    def _read(self, event: dict[str, Any]) -> str:
        kind = str(event.get("type", ""))
        self._remember_session(event.get("session_id"))
        if kind == "system" and str(event.get("subtype", "")) == "init":
            cwd = event.get("cwd")
            if isinstance(cwd, str) and cwd:
                self._cwd = cwd
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
        """Text as it is typed, and what can honestly be said about thinking.

        **The thinking text is not available**, measured against the installed CLI
        on 2026-09-22 rather than assumed: ``content_block_start`` carries
        ``{"type": "thinking", "thinking": "", "signature": ""}``, every
        ``thinking_delta`` carries ``thinking: ""`` plus an ``estimated_tokens``
        count, and the closing ``assistant`` copy is empty too. Claude does not
        hand this mode the words.

        So the window used to show a bare ``·`` with nothing after it, once per
        thinking block, and stood still for however long the block took (CR-104).
        What *is* knowable is how much thinking happened, and that is what the
        closing line says. Rendering nothing at all would be the other honest
        option; a count is the one that tells the reader the pause was work.

        If a future CLI does stream the words, they render — the count line then
        steps aside rather than repeating what was already shown.
        """
        kind = str(inner.get("type", ""))
        index = inner.get("index")
        if kind == "content_block_start":
            carried_over = ""
            if index == 0:
                # Block indices are per message and restart at 0, so this is a
                # new message and `self._thinking_blocks[0]` is about to be
                # overwritten. Anything still open there never got its
                # `content_block_stop` — a truncated message, a dropped line —
                # and its estimate is written out now rather than overwritten in
                # silence (kiro review, 2026-09-22). Done at the *start* of a
                # message because, measured, the closing copies arrive
                # interleaved with the blocks rather than after them: no point
                # in the stream reliably means "the message ended".
                carried_over = "".join(
                    self._close_thinking(state)
                    for _, state in sorted(self._thinking_blocks.items())
                )
                self._thinking_blocks.clear()
                self._thinking_closed.clear()
            block = inner.get("content_block")
            if isinstance(block, dict) and str(block.get("type", "")) == "thinking":
                if isinstance(index, int):
                    self._thinking_blocks[index] = {
                        "tokens": 0, "shown": False, "line_open": False,
                        "signature": _text_of(block.get("signature")), "text": "",
                        "counted": False,
                    }
                # Nothing is written yet. Opening the line here is what produced
                # the empty marker: with no text ever arriving, `· ` and the
                # newline at stop were the entire block.
            return carried_over
        if kind == "content_block_stop":
            if isinstance(index, int) and index in self._thinking_blocks:
                state = self._thinking_blocks.pop(index)
                # Kept, not dropped: measured, the closing copy arrives *before*
                # this event, but a CLI that sends it after would leave the copy
                # with no block to match and its words printed a second time.
                # Identity outlives the block; the list is emptied per message.
                self._thinking_closed.append(state)
                return self._close_thinking(state)
            return ""
        if kind != "content_block_delta":
            return ""
        delta = inner.get("delta")
        if not isinstance(delta, dict):
            return ""
        delta_type = str(delta.get("type", ""))
        if delta_type == "text_delta":
            # Deliberately unterminated: consecutive deltas are one sentence being
            # typed, and a newline per delta would shred it. A thinking line left
            # open by a block that never closed is ended first, so the reply does
            # not continue it under a `·` (kiro review, round 7).
            return self._close_open_lines() + _text_of(delta.get("text"))
        if delta_type == "thinking_delta":
            state = self._thinking_blocks.get(index) if isinstance(index, int) else None
            estimated = delta.get("estimated_tokens")
            if state is not None and isinstance(estimated, int):
                # Last one wins: the estimate is cumulative for the block.
                state["tokens"] = estimated
            text = _text_of(delta.get("thinking"))
            if not text:
                return ""
            if state is not None:
                # Kept whole, because it is this block's second identity: a
                # closing copy carrying no signature is matched on its words.
                state["text"] += text
            # Another block's unfinished line is ended first: a stream that lost
            # a `content_block_stop` used to run the next block's thinking onto
            # it, and a line-by-line reader files both under the first block
            # (kiro review, round 7).
            broke = self._close_open_lines(keep=state)
            # A line already open continues; otherwise this opens one. Every line
            # carries the marker, as `_prompt_echo` does for the same reason: the
            # transcript is line-based, and a second line without the marker reads
            # as the agent's own prose rather than as its thinking.
            lead = "" if state is not None and state["line_open"] else "· "
            body = lead + text.replace("\n", "\n· ")
            if body.endswith("\n· "):
                # Text ending on a newline would leave the marker for a line that
                # has not started — an empty `·`, which is the thing this whole
                # change removes (kiro review, 2026-09-22). The next delta opens
                # its own.
                body = body[:-2]
            if state is not None:
                state["shown"] = True
                state["line_open"] = not body.endswith("\n")
            return broke + body
        if delta_type == "signature_delta":
            # The block's identity, accumulated: measured, these deltas add up to
            # exactly the `signature` its closing copy carries.
            state = self._thinking_blocks.get(index) if isinstance(index, int) else None
            if state is not None:
                state["signature"] += _text_of(delta.get("signature"))
            return ""
        return ""

    def _close_thinking(self, state: dict[str, Any]) -> str:
        """End a thinking block: terminate its line, or report its size.

        Split out because the block also has to be closed when the stream stops
        without a ``content_block_stop`` — a killed process, a cancelled turn, a
        truncated pipe — where the estimate would otherwise be dropped although it
        had already arrived (kiro review, 2026-09-22).
        """
        if state["shown"]:
            # Words were shown, so the count would only repeat what the reader
            # can already see. All that is left is to terminate an open line —
            # and to record that it is now terminated, or a copy arriving after
            # this would continue a line that no longer exists and write its
            # words without the thinking marker (kiro review, round 5).
            if not state["line_open"]:
                return ""
            state["line_open"] = False
            return "\n"
        tokens = state["tokens"]
        if not tokens:
            return ""
        # Recorded, because it is what a later copy cannot undo: once the count
        # is on the line, words arriving afterwards make the transcript show
        # both (kiro review, round 4). Measured, the copy comes first and this
        # never fires; when it does, `_render_thinking_copy` says so rather than
        # letting the pair look intended.
        state["counted"] = True
        return _line(f"· 想了约 {tokens} tokens")

    def finish(self) -> str:
        # A thinking block still open here never got its `content_block_stop`.
        # Whatever was known about it is written before the closing notes rather
        # than discarded with the reader.
        pending = "".join(
            self._close_thinking(state)
            for _, state in sorted(self._thinking_blocks.items())
        )
        self._thinking_blocks.clear()
        self._thinking_closed.clear()
        return pending + super().finish()

    def _render_thinking_copy(
        self, words: str, source: dict[str, Any] | None, ambiguous: bool,
    ) -> str:
        """What a non-empty closing copy adds to the transcript.

        Four outcomes, and the reason there are four is that the block's identity
        now says which one applies:

        * **no block** — render it. Text that cannot be matched is text that
          would be lost, and a duplicate is the cheaper mistake. Reported when
          the match was a tie rather than a plain miss.
        * **a block that streamed nothing** — this copy is the only place the
          words appear, so it renders, and the block is marked shown so its
          ``content_block_stop`` does not also print a token count on top of the
          words it just showed (kiro review, 2026-09-22).
        * **a block whose words this copy extends** — only the suffix is
          appended. The old code dropped the whole copy here and its comment
          called the suffix unrecoverable; with the block identified and its
          streamed text in hand, the suffix is exactly what the copy has beyond
          it (kiro review, 2026-09-22).
        * **a block whose words this copy repeats** — nothing is appended, and
          it is reported rather than passed over: no observed CLI serves the
          words twice, so this firing at all is news.

        Every line written here carries the ``·`` marker and every diagnostic
        starts on its own line: a continuation line without the marker reads as
        the agent's own prose, and a note glued to an unfinished thinking line
        breaks the line boundaries the whole transcript is parsed on (kiro
        review, round 3).
        """
        if source is None:
            note = self._unread("claude thinking:copy matches two blocks") if ambiguous else ""
            return self._close_open_lines() + _marked_lines(_clip(words)) + note
        streamed = source["text"]
        if not source["shown"]:
            # Another block's unfinished line first: this copy is not its
            # continuation, and appending to it glued two blocks into one line
            # that a line-by-line reader then files under the wrong one (kiro
            # review, round 6).
            broke = self._close_open_lines()
            source["shown"] = True
            source["line_open"] = False
            # The words are now this block's text, so an identical copy replayed
            # after this one is recognised as the repeat it is. Leaving it empty
            # made the replay look like a suffix and printed the whole thing
            # again (kiro review, round 4).
            source["text"] = words
            late = (
                self._unread("claude thinking:copy arrived after the count")
                if source.get("counted") else ""
            )
            return broke + _marked_lines(_clip(words)) + late
        if words == streamed:
            return self._close_open_lines() + self._unread(
                "claude thinking:streamed and repeated"
            )
        if words.startswith(streamed):
            broke = self._close_open_lines(keep=source)
            suffix = _clip(words[len(streamed):])
            body = _marked_lines(suffix, lead=not source["line_open"])
            source["text"] = words
            source["line_open"] = False
            return broke + body
        # Same block, and the copy is neither what streamed nor an extension of
        # it. Rendered so nothing is lost, and reported because the two versions
        # disagreeing is a shape nothing observed produces.
        broke = self._close_open_lines()
        # The copy's words are what the reader now sees for this block, so a
        # replay of the same event is recognised as a repeat. Left unset, it was
        # rendered again — and `_unread` deduplicates, so the second copy came
        # with no warning at all (kiro review, round 8).
        source["text"] = words
        return broke + _marked_lines(_clip(words)) + self._unread(
            "claude thinking:copy disagrees with the deltas"
        )

    def _close_open_lines(self, keep: dict[str, Any] | None = None) -> str:
        """Terminate any other block's unfinished line before writing elsewhere.

        Each block owns the line it left open; text written for a *different*
        block continues it, and the two end up on one line under one block's
        marker (kiro review, round 6). ``keep`` is the block about to be written
        to, whose own line is a continuation and is left alone.
        """
        return "".join(
            self._close_open_line(state)
            for state in self._candidate_thinking()
            if state is not keep
        )

    @staticmethod
    def _close_open_line(state: dict[str, Any]) -> str:
        """Terminate a thinking line the deltas left unfinished, if any.

        Anything written after an open line — a note, a diagnostic — would
        otherwise continue it, so the transcript's own line boundaries stop
        holding (kiro review, round 3).
        """
        if not state["line_open"]:
            return ""
        state["line_open"] = False
        return "\n"

    def _read_assistant(self, message: dict[str, Any]) -> str:
        blocks = message.get("content")
        if not isinstance(blocks, list):
            # A copy in a shape this cannot read describes no block. Nothing is
            # cleared: the open blocks are still open — measured, this event
            # arrives *before* their `content_block_stop` — and their estimates
            # are still owed to the reader.
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
                rendered.append(_line(f"$ {_tool_call(block.get('name'), block.get('input'), cwd=self._cwd)}"))
            elif block_type == "thinking":
                # The closing copy. Measured against the installed CLI it is
                # always empty — the words are not served in this mode — and an
                # empty one rendered a `·` with nothing after it, which is all the
                # window used to show for an entire thinking block (CR-104).
                #
                # A non-empty one renders only when *this block's* deltas did not
                # already show it. All three parts are needed: dropping it
                # unconditionally would lose text a future CLI serves only here,
                # rendering it unconditionally would print the same thinking twice
                # on a CLI that serves it both ways, and asking the question once
                # per message rather than per block dropped a block that never
                # streamed because a sibling did (kiro review, 2026-09-22).
                #
                # Which block this copy is comes from the block's own identity —
                # its signature, or failing that its words. Not from its position
                # in `content`, and not from the order the copies arrive in:
                # `_match_thinking` records what each of those got wrong.
                words = _text_of(block.get("thinking"))
                source, ambiguous = self._match_thinking(
                    _text_of(block.get("signature")), words,
                )
                if words.strip():
                    rendered.append(self._render_thinking_copy(words, source, ambiguous))
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


def _marked_lines(text: str, *, lead: bool = True) -> str:
    """Thinking text as transcript lines, with the ``·`` marker on **every** one.

    The transcript is line-based and read line by line, so a continuation line
    without the marker reads as the agent's own prose rather than as its
    thinking. ``lead=False`` continues a line the deltas left open. A trailing
    marker for a line that never starts is dropped — the same empty ``·`` the
    streaming path exists to avoid.
    """
    body = ("· " if lead else "") + text.replace("\n", "\n· ")
    if body.endswith("\n· "):
        body = body[:-2]
    return _line(body)


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


#: A command is the one tool input that *is* reading matter — what ran is the whole
#: point — so it gets the same room the old echo had. Every other tool's input is a
#: payload the transcript names rather than reprints. A command past this is still
#: cut, and says so with an ellipsis: unbounded would put a generated script back in
#: the window, which is the thing this limit exists to keep out.
_COMMAND_LIMIT = 2_000

#: Which field of a tool's input *is* the call, per tool. The CLI shows a write as
#: ``Write(path)``, not as the file — printing the payload put whole source files in
#: the conversation window (user, 2026-09-19: "这啥内容啊 为什么要放到窗口里").
_TOOL_SUBJECT = {
    # Both shells appear: `Bash` everywhere, `PowerShell` as the primary shell on
    # Windows (measured on this machine, 2026-09-19 — a real headless run used it).
    "Bash": ("command", _COMMAND_LIMIT),
    "PowerShell": ("command", _COMMAND_LIMIT),
    "BashOutput": ("bash_id", 120),
    "Read": ("file_path", 300),
    "Write": ("file_path", 300),
    "Edit": ("file_path", 300),
    "NotebookEdit": ("notebook_path", 300),
    "Glob": ("pattern", 200),
    "Grep": ("pattern", 200),
    "WebFetch": ("url", 300),
    "WebSearch": ("query", 200),
    "Task": ("description", 200),
    "Skill": ("skill", 120),
    "TodoWrite": (None, 0),
}


#: Tools whose subject is a path, and so may be shown relative to the run's cwd.
_PATH_TOOLS = frozenset({"Read", "Write", "Edit", "NotebookEdit"})


def _relative_to(path: str, cwd: str | None) -> str:
    """``path`` as the agent's terminal would print it: relative to ``cwd`` when it
    is inside it, and untouched otherwise. Purely textual — nothing is resolved on
    disk, because the run may have executed on another machine (W3-a) and a path is
    not a claim this module is entitled to check."""
    if not cwd:
        return path
    base = cwd.replace("\\", "/").rstrip("/")
    candidate = path.replace("\\", "/")
    # Case-sensitive, except on a Windows path, where the filesystem itself is not.
    # Folding case everywhere made `/srv/data/archive` look like it sat inside
    # `/srv/Data` — two different directories on POSIX, and the shortened path would
    # have named the wrong one (review, 2026-09-19).
    windows = len(base) > 1 and base[1] == ":" and len(candidate) > 1 and candidate[1] == ":"
    prefix = f"{base}/"
    hit = (
        candidate.lower().startswith(prefix.lower()) if windows
        else candidate.startswith(prefix)
    )
    if hit:
        # The *original* string is cut, so the path keeps its own separators:
        # normalising them would be the same rewrite this module refuses elsewhere,
        # and `replace` does not change a string's length, so the index still lands
        # on the separator that follows the prefix.
        return path[len(base) + 1:]
    return path


def _tool_call(name: Any, payload: Any, *, cwd: str | None = None) -> str:
    """One line naming what the agent did, the way its own terminal names it.

    The subject is the field that identifies the call — a command, a path, a pattern.
    A write's content, an edit's replacement text and every other bulk field are left
    out: they are what the *deliverables* list and the file itself are for, and a
    conversation that reprints them is unreadable. A tool this does not know about
    falls back to a short clip of its whole input, so a new tool is thin rather than
    invisible.
    """
    tool = str(name or "?")
    known = _TOOL_SUBJECT.get(tool)
    if known is None:
        return f"{tool} {_shorten(_compact(payload), 300)}".rstrip()
    field, limit = known
    if field is None:
        return tool
    value = payload.get(field) if isinstance(payload, dict) else None
    text = _text_of(value) if value is not None else ""
    if text and tool in _PATH_TOOLS:
        text = _relative_to(text, cwd)
    subject = _shorten(text, limit) if text else ""
    if tool == "Write" and isinstance(payload, dict):
        # How much is being written is the one number a reader wants here.
        body = _text_of(payload.get("content"))
        if body:
            return f"{tool}({subject}) {body.count(chr(10)) + 1} 行"
    return f"{tool}({subject})" if subject else tool


def _shorten(text: str, limit: int) -> str:
    """At most ``limit`` characters of ``text``, with **one** transformation, stated.

    Nothing inside a line is rewritten: an earlier version collapsed runs of
    whitespace so the call would fit one line, which made the transcript show a
    command that was not the one that ran — ``A  B.txt`` became ``A B.txt`` (review,
    2026-09-19).

    The one thing that *is* done: a transcript is a line-oriented record, and a
    multi-line command would otherwise be indistinguishable from the agent's own
    prose on the lines after the first. So continuation lines are quoted with two
    leading spaces, the way an email quotes. **A multi-line command copied out of the
    window therefore carries that indent** — which matters for a heredoc or a
    here-string, where leading whitespace is significant. The alternative, a
    structured record, is a bigger change than this line-oriented file (workspace
    CR-101).
    """
    clipped = text if len(text) <= limit else f"{text[:limit]}…"
    first, *rest = clipped.split("\n")
    return "\n".join([first, *(f"  {line}" for line in rest)])
