"""The append-only transcript of one run, readable while it is still running.

A conversation window has to show text before the process exits, and the run's
own record cannot carry it: ``execution_runs.stdout_tail`` is written once, when
the run settles. So the rendered transcript goes to a file per run, appended as
events arrive, and is read back by byte offset.

**Byte offset, not "give me everything again", on purpose.** It is the same
contract a remote environment can serve with ``tail -c +<offset>`` over ssh,
which is what W3-a has to dispatch through once runs move off this machine. One
mechanism, local now and remote later, rather than a websocket that would have to
be replaced the moment the process stops being a local child.

The directory sits beside the control-plane database and is resolved from the
same function, so "where is Agora's state" keeps exactly one answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agora.fsprobe import probe_path

#: Run ids are control-plane generated, but this module turns one into a
#: filesystem path, so the shape is enforced here rather than trusted. Anything
#: else is refused: a run id carrying a separator or ``..`` would otherwise
#: choose the file.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

#: Most bytes returned by one read. A window that has been closed for an hour
#: asks for a large slice; it gets it in pages rather than in one response big
#: enough to stall the browser.
MAX_SLICE_BYTES = 256 * 1024

#: Most bytes one run's transcript may occupy. Transcripts are kept for the life
#: of the run record, so without a cap a single looping command fills the disk
#: the control-plane database lives on. Generous enough to hold a long working
#: session in full; small enough that a thousand runs cannot.
MAX_LOG_BYTES = 8 * 1024 * 1024

#: Appended once when a transcript reaches the cap, so a conversation that stops
#: mid-sentence says why instead of looking like an agent that went quiet.
TRUNCATION_NOTICE = (
    "\n· 这一轮的记录到达了大小上限，后面的输出没有被保存下来。"
    "run 本身不受影响；结论与改动文件仍在它自己的记录里。\n"
).encode("utf-8")


class RunLogError(ValueError):
    """A run id that may not be turned into a path."""


@dataclass(frozen=True)
class RunLogSlice:
    """One read of a transcript.

    ``reachable`` is the honesty field. False means this process could not
    establish what the transcript holds — the file could not be stat'd or read —
    which is **not** the same as a run that has produced no output yet, and
    callers must render the two differently. ``text`` is empty in both cases.
    """

    text: str
    offset: int
    next_offset: int
    reachable: bool
    unreachable_reason: str | None = None
    #: True when an append to this transcript failed in this process, so output
    #: the agent produced is missing from it. Distinct from ``reachable``: the
    #: file reads fine, and that is exactly why the loss needed its own flag —
    #: "nothing was written" and "what was written could not be saved" looked
    #: identical, and the docstring here used to claim `reachable` covered it.
    #:
    #: Persisted next to the transcript, because the gap outlives the process
    #: that saw it: held only in memory, a restart re-reported a mutilated log as
    #: complete and readable. The marker is a sibling file, written on the same
    #: failure — a tiny write that can itself fail, which is why the in-memory set
    #: is kept as well. Between them: whichever survives says the log is short.
    lossy: bool = False
    #: True when this read stopped at ``MAX_SLICE_BYTES`` and more is already on
    #: disk, so a caller polling for "anything new" knows to come straight back
    #: rather than waiting for its next tick.
    more_available: bool = False


class RunLogStore:
    """Append and read run transcripts under one directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._lossy: set[str] = set()

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id or ""):
            raise RunLogError(f"run id is not usable as a transcript name: {run_id!r}")
        return self._root / f"{run_id}.log"

    def append(self, run_id: str, text: str) -> None:
        """Append rendered text. One call, one write, so a reader never lands mid-unit.

        Stops at :data:`MAX_LOG_BYTES` and writes one line saying so. Unbounded
        was the wrong answer: a looping command or a broken CLI can emit hundreds
        of megabytes before its timeout, and the transcript is kept forever, so
        "the read is capped" protected nothing about the disk. A capped transcript
        that says it is capped is honest; a full disk takes the control plane down
        with it.

        Failure does not raise: the transcript is a reading surface, and losing
        the ability to *show* a run must not be able to kill the run itself. It is
        **recorded** though — see ``RunLogSlice.lossy`` — because a failed append
        leaves a readable file with output missing from it, which is not the same
        as a run that said nothing and must not read as one.
        """
        if not text:
            return
        path = self.path_for(run_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            probe = probe_path(path)
            size = probe.size or 0
            if size >= MAX_LOG_BYTES:
                # Already full. The notice was written by the append that filled
                # it, but everything after this point is being dropped — and
                # returning silently left the transcript looking like a complete
                # one that simply ended, with no way after a restart to tell that
                # it had not.
                self._lossy.add(run_id)
                self._mark_lossy(run_id)
                return
            payload = text.encode("utf-8")
            if size + len(payload) > MAX_LOG_BYTES:
                # Cut on a character boundary, then say the cut happened. Silence
                # here would show a conversation that simply stops mid-sentence,
                # which reads as an agent that stopped rather than a transcript
                # that ran out of room.
                room = MAX_LOG_BYTES - size
                payload = _whole_characters(payload[:room]) + TRUNCATION_NOTICE
                # The notice is in the text, but `lossy` has to say so too: a
                # caller reading the flag alone was told nothing was missing,
                # which is what the field exists to deny. Marked durably, so it
                # survives the process that saw the truncation.
                self._lossy.add(run_id)
                self._mark_lossy(run_id)
            with path.open("ab") as handle:
                handle.write(payload)
                handle.flush()
        except OSError:
            # Remembered, not just swallowed. The run keeps going — that part was
            # right — but a reader of this transcript now has to be told that
            # output is missing from it, which nothing here used to do.
            self._lossy.add(run_id)
            self._mark_lossy(run_id)
            return

    def _lossy_marker(self, run_id: str) -> Path:
        return self.path_for(run_id).with_suffix(".lossy")

    def _mark_lossy(self, run_id: str) -> None:
        """Leave a durable note that this transcript is short.

        Best-effort by nature: the reason we are here is that a write failed, so
        this one may fail too. It is a different, much smaller write, and when it
        lands the loss survives the restart that clears the in-memory set.
        """
        try:
            self._lossy_marker(run_id).write_text("append failed\n", encoding="utf-8")
        except OSError:
            return

    def _is_lossy(self, run_id: str) -> bool:
        if run_id in self._lossy:
            return True
        try:
            return not probe_path(self._lossy_marker(run_id)).absent
        except RunLogError:
            return False

    def read(self, run_id: str, offset: int = 0) -> RunLogSlice:
        """Read from ``offset`` to the end of what is on disk right now."""
        start = max(0, int(offset))
        path = self.path_for(run_id)
        probe = probe_path(path)
        if probe.unknown:
            # Cannot tell whether a transcript is there. Reported as unreachable
            # rather than as an empty one: "I could not read it" and "it wrote
            # nothing" send the reader to different places.
            return RunLogSlice(
                text="", offset=start, next_offset=start,
                reachable=False, unreachable_reason=probe.reason(),
                lossy=self._is_lossy(run_id),
            )
        if probe.absent:
            # A run whose transcript has not been created yet. Reachable and
            # empty is the true answer: nothing has been written, and the next
            # read from the same offset will pick up whatever arrives.
            return RunLogSlice(
                text="", offset=start, next_offset=start, reachable=True,
                lossy=self._is_lossy(run_id),
            )
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                raw = handle.read(MAX_SLICE_BYTES + 1)
        except OSError as exc:
            return RunLogSlice(
                text="", offset=start, next_offset=start,
                reachable=False, unreachable_reason=type(exc).__name__,
                lossy=self._is_lossy(run_id),
            )
        more = len(raw) > MAX_SLICE_BYTES
        raw = raw[:MAX_SLICE_BYTES]
        # An offset that lands inside a character: its leading bytes belong to a
        # character that began before `start`, and returning them produced a
        # `\ufffd` that no later page could repair, because `next_offset` had
        # moved past it. The served `offset` says where the text really starts.
        skipped = _aligned_start(raw)
        raw = _whole_characters(raw[skipped:])
        start += skipped
        return RunLogSlice(
            text=raw.decode("utf-8", errors="replace"),
            offset=start,
            next_offset=start + len(raw),
            reachable=True,
            more_available=more,
            lossy=self._is_lossy(run_id),
        )


def _aligned_start(data: bytes) -> int:
    """How many leading bytes are the tail of a character that began earlier.

    A read may be asked for any offset — a client's own bookkeeping, a remote
    environment resuming a transfer — and one that lands inside a multi-byte
    character used to return continuation bytes that decode to ``\ufffd``. The
    character could not be recovered afterwards either: ``next_offset`` moved
    past it. Skipped instead, with the served ``offset`` saying where the text
    actually starts.
    """
    index = 0
    while index < len(data) and (data[index] & 0xC0) == 0x80:
        index += 1
    return index


def _whole_characters(raw: bytes) -> bytes:
    """Drop a trailing partial UTF-8 sequence so the next read resumes on it.

    ``append`` writes one whole unit per call, so an offset handed back by a
    previous read is normally already a character boundary. This covers the two
    cases that are not: a slice cut at ``MAX_SLICE_BYTES``, and an OS write seen
    partially flushed. Both are real, and both would otherwise put a replacement
    character into the middle of a word — permanently, since the next read starts
    after it.
    """
    if not raw:
        return raw
    # A UTF-8 character is at most 4 bytes, so the last lead byte can be at most
    # 3 positions from the end. Walk back over continuation bytes (0b10xxxxxx)
    # to find it.
    index = len(raw) - 1
    limit = max(0, len(raw) - 4)
    while index >= limit and raw[index] & 0xC0 == 0x80:
        index -= 1
    if index < limit:
        # Four or more continuation bytes with no lead in reach: this slice did
        # not start on a boundary, which append/read cannot produce. Left as-is
        # rather than trimmed by guesswork; decode() marks it and the caller sees
        # replacement characters instead of a silently shortened transcript.
        return raw
    lead = raw[index]
    if lead < 0x80:
        needed = 1
    elif lead & 0xE0 == 0xC0:
        needed = 2
    elif lead & 0xF0 == 0xE0:
        needed = 3
    elif lead & 0xF8 == 0xF0:
        needed = 4
    else:
        return raw  # not a valid lead byte either; same reasoning as above
    if len(raw) - index < needed:
        return raw[:index]
    return raw
