"""The project kickoff routes (W10): read the record, start, answer, refine, adopt.

State lives in the project repository (``.agora/kickoff.json``, see
``agora/projects/kickoff.py``); the control plane is touched only by ``adopt``, which
writes the charter through the existing human path and creates the first tasks through
the task store — nothing before that is a commitment.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from agora.projects import ProjectRegistry
from agora.projects import kickoff
from agora.tasks.models import CreateTaskRequest
from agora.tasks.router import get_project_registry, get_task_store
from agora.tasks.store import TaskStore

router = APIRouter(tags=["kickoff"])


#: Optimistic concurrency for the record. Every mutation names the revision of the record
#: the caller was looking at (`None` for "I saw no record"); the server refuses with 409
#: `kickoff_stale` when the record on disk is not that one. This is what stops an old tab —
#: still showing round one's proposal, or a partial set of answers — from adopting the
#: wrong thing, dropping a sibling's answer, or starting over a newer round. The panel
#: re-reads on any refusal, so the person sees the record as it is.


class StartKickoffRequest(BaseModel):
    #: The owner's own words about the project. Required for an empty repository — there
    #: is nothing else to read — and welcome for a full one.
    brief: str = Field(default="", max_length=20_000)
    expected_revision: str | None = None


class KickoffAnswersRequest(BaseModel):
    #: Bounded per answer and in number, so the record they go into stays readable
    #: (kickoff.RECORD_CHARS); an id that is not a question's is dropped at save time.
    answers: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("answers")
    @classmethod
    def _answers_bounded(cls, values: dict[str, str]) -> dict[str, str]:
        for key, value in values.items():
            if len(key) > kickoff.QUESTION_ID_CHARS:
                raise ValueError("an answer id is too long")
            if len(value) > kickoff.ANSWER_CHARS:
                raise ValueError(f"an answer is longer than {kickoff.ANSWER_CHARS} characters")
        return values
    expected_revision: str | None = None


class KickoffRefineRequest(BaseModel):
    expected_revision: str | None = None


def _non_blank_lines(values: list[str], what: str) -> list[str]:
    out = [v.strip() for v in values]
    if any(not v for v in out):
        raise ValueError(f"{what} has a blank item")
    return out


class AdoptCharter(BaseModel):
    """What the person adopts meets the contract the agent's answer had to meet: a goal
    that is not whitespace, and at least one success criterion — the editor lets them
    clear the list, and adopting it would write a goal with no way to know it was met."""
    goal: str = Field(min_length=1, max_length=kickoff.CHARTER_FIELD_CHARS)
    non_goals: list[str] = Field(default_factory=list, max_length=kickoff.CHARTER_LIST_ITEMS)
    success_criteria: list[str] = Field(min_length=1, max_length=kickoff.CHARTER_LIST_ITEMS)

    @field_validator("non_goals", "success_criteria")
    @classmethod
    def _items_bounded(cls, values: list[str]) -> list[str]:
        if any(len(v) > kickoff.CHARTER_ITEM_CHARS for v in values):
            raise ValueError(f"a charter item is longer than {kickoff.CHARTER_ITEM_CHARS} characters")
        return values

    @field_validator("goal")
    @classmethod
    def _goal_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal is blank")
        return value.strip()

    @field_validator("non_goals")
    @classmethod
    def _non_goals_lines(cls, values: list[str]) -> list[str]:
        return _non_blank_lines(values, "non_goals")

    @field_validator("success_criteria")
    @classmethod
    def _criteria_lines(cls, values: list[str]) -> list[str]:
        return _non_blank_lines(values, "success_criteria")


class AdoptTask(BaseModel):
    """A task as approved: title, a description that says what done looks like, and a
    kind from the contract — the parser required all three of the agent, and the store
    should not receive less through the adopt route."""
    title: str = Field(min_length=1, max_length=kickoff.TITLE_CHARS)
    description: str = Field(min_length=1, max_length=kickoff.DESCRIPTION_CHARS)
    kind: str = Field(min_length=1, max_length=128)

    @field_validator("title", "description")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank")
        return value.strip()

    @field_validator("kind")
    @classmethod
    def _kind_in_contract(cls, value: str) -> str:
        kind = value.strip().lower()
        if kind not in kickoff.TASK_KINDS:
            raise ValueError(f"kind {value!r} is not one of {sorted(kickoff.TASK_KINDS)}")
        return kind


class AdoptKickoffRequest(BaseModel):
    #: What the person actually approved — possibly edited from the proposal.
    charter: AdoptCharter
    tasks: list[AdoptTask] = Field(default_factory=list, max_length=kickoff.APPROVED_TASKS)
    expected_revision: str | None = None


#: What each refusal means, in a sentence the panel can show. The shape is the shared
#: client's (`apiErrorFrom` reads `detail.code` and `detail.message`); the earlier
#: `error`-keyed shape would have reached the person as raw JSON.
_MESSAGES = {
    "kickoff_path_unsafe": "The kickoff record's path in the repository is a link or could not be inspected; nothing was written.",
    "kickoff_adopting": "An adoption is half done; resume it before starting over.",
    "kickoff_not_applied_here": "This control plane has not applied the adopted record in full; re-apply it before starting over.",
    "kickoff_foreign": "The kickoff record in this directory belongs to another project.",
    "kickoff_not_available": "The kickoff record could not be used: it is unreadable, malformed, a link, or the project's root directory is missing.",
    "kickoff_stale": "The record has changed since this page read it; it was re-read — look again before acting.",
    "kickoff_task_scan_too_large": "This project has more tasks than the adoption can scan safely.",
    "kickoff_brief_required": "This repository is empty; write a few sentences about the project first.",
    "kickoff_already_adopted": "This proposal has already been adopted.",
    "kickoff_not_answered": "The questions have not been answered yet.",
    "kickoff_unanswered": "Answer at least one question before generating the final.",
    "kickoff_state_changed": "The record moved to another step while the agent worked; nothing was written.",
    "kickoff_not_proposed": "There is no proposal to adopt yet.",
    "kickoff_record_too_large": "The adopted record would be larger than can be read back; shorten the tasks or the answers.",
    "kickoff_project_root_missing": "The project's root directory does not exist; nothing was written.",
    "kickoff_key_conflict": "A task in this project already carries one of this adoption's keys but is not the approved task; nothing was created. Cancel or archive that task in the workbench, then adopt again.",
    "kickoff_seal_mismatch": "Another adoption of this proposal is half done and its content differs from yours; the record was re-read — resume that one, or start over.",
}


def _detail(code: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": _MESSAGES.get(code, code), **extra}


def _project(registry: ProjectRegistry, project_id: str):
    try:
        return registry.get(project_id)
    except (KeyError, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown project: {project_id}") from None


def _write(project, record: dict[str, Any]) -> None:
    """A write that was refused — the record's path is a link, or could not be inspected —
    is the person's problem to hear about, not a 500."""
    try:
        kickoff.write_kickoff(project.root, record)
    except kickoff.RecordTooLarge as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_record_too_large", detail=str(exc), limit=kickoff.RECORD_CHARS)) from None
    except kickoff.ProjectRootMissing as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_project_root_missing", detail=str(exc))) from None
    except kickoff.KickoffError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_path_unsafe", detail=str(exc))) from None


def _refuse_start_over_a_halfway_adoption(project_id: str, registry: ProjectRegistry, tasks: TaskStore, read: kickoff.KickoffRead) -> None:
    """A record in `adopting` holds the only copy of what was approved and not yet
    created. Starting over would write a fresh questions record on top of it, and the
    approved task that failed to be created could never be created — the first one would
    be left in the store as an orphan. Resume it (adopt again) first; there is no path
    that discards a sealed adoption, on purpose."""
    if not read.available or read.record is None:
        return
    if read.record.get("status") == "adopting":
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_adopting"))
    # The same holds for an *adopted* record this control plane has not applied in full
    # (a clone, a database not restored): its seal is the only list of what to create
    # here, and a new round would write over it. Re-apply first; then start over if wanted.
    here = _here(project_id, registry, tasks, read.record)
    if here is not None and not here["complete"]:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_not_applied_here", here=here))


# One snapshot per decision. The record is a file another process may replace at any
# moment — `git checkout` most of all — so "check the revision" and "take the record"
# must look at the *same* read: checked against read A and then taken from read B, a save
# from A's page would have been written onto B. Every mutation below reads once under the
# lock, checks that read, mutates that read, writes. (The gap between that read and the
# rename is still open to a process outside this one — workspace CR-56 names it.)

def _snapshot(project_id: str, project) -> kickoff.KickoffRead:
    return kickoff.read_kickoff(project.root, project_id)


def _check_revision(read: kickoff.KickoffRead, expected: str | None) -> None:
    """The caller mutates the record it saw, or nothing. `expected` is the revision the
    caller holds — `None` when it saw no record — and this read must be at it."""
    if read.reason == "foreign":
        # Another project registered on this directory wrote the record. No mutation from
        # this project may touch it — starting over would erase theirs, adopting would
        # mark theirs adopted while this project's control plane got the charter.
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_foreign"))
    if not read.available and read.reason != "absent":
        # Unreadable, malformed, linked: not "no record" — a caller that saw no record
        # (`expected` None) must not pass on the strength of that, least of all before
        # a 240-second agent call that the commit would then refuse.
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_not_available", reason=read.reason))
    current = read.record.get("revision") if read.available and read.record else None
    if current != expected:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_stale", revision=current))


def _record_of(read: kickoff.KickoffRead) -> dict[str, Any]:
    if read.reason == "foreign":
        # Another project's record on this directory: the same refusal wherever it is met.
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_foreign"))
    if not read.available:
        # Absent, unreadable and malformed are three different answers; the client asked
        # for a step that needs a record, and which of the three it got matters.
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_not_available", reason=read.reason))
    assert read.record is not None
    return read.record





def _here(project_id: str, registry: ProjectRegistry, tasks: TaskStore, record: dict[str, Any] | None) -> dict[str, Any] | None:
    """What *this* control plane holds of an adopted record. The record travels with the
    repository — cloned to another machine, restored without the database — and "adopted"
    in the file says nothing about the charter and tasks here. Looked up, not assumed:
    the charter by its goal, each recorded task by its id."""
    if not record or record.get("status") not in {"adopted", "adopting"}:
        return None
    adopted = record.get("adopted") or {}
    # What exists is what the store holds under the *approved* keys — not what the record
    # got around to writing down: a task created just before a record write failed is in
    # the store and not in the record, and a re-apply that failed halfway has written the
    # record's task list back shorter than the approved list. The approved list is the
    # measure; the store is the evidence.
    approved_keys = [t.get("key") for t in adopted.get("approved", []) if isinstance(t, dict)]
    try:
        by_key = _tasks_by_kickoff_key(tasks, project_id)
    except HTTPException:
        # More tasks than the scan takes: a read still answers — with nothing counted as
        # present and `scan_failed` said — and the adopt route refuses before any seal.
        return {
            "applied_here": False, "charter": "absent", "tasks_recorded": len(approved_keys),
            "tasks_present": 0, "receipt_complete": False, "complete": False, "scan_failed": True,
        }
    approved_by_key = {t.get("key"): t for t in adopted.get("approved", []) if isinstance(t, dict)}
    resolved = {k: _resolve_key(by_key, k, approved_by_key[k]) for k in approved_keys if isinstance(k, str)}
    present = sum(1 for m, _conflict in resolved.values() if m is not None)
    # A stranger under an approved key is a conflict the adopt route refuses; said here.
    key_conflict = any(conflict for _m, conflict in resolved.values())
    if record.get("status") == "adopting":
        return {
            "applied_here": False,
            "charter": "absent",
            "tasks_recorded": len(approved_keys),
            "tasks_present": present,
            "receipt_complete": False,
            "complete": False,
            "key_conflict": key_conflict,
        }
    current = kickoff.charter_triplet(registry.get(project_id).charter)
    sealed = kickoff.charter_triplet(adopted.get("charter"))
    # Three states, not "some goal is there": `matches` the approved charter, `differs`
    # from it, `absent`. Which of the three is fine depends on whether *this* control
    # plane applied this round — and that is not inferred from the charter or the task
    # count (a charter-only adoption has no task to prove itself by): the adoption writes
    # a marker with the round id into this control plane's settings, and the marker is
    # what "applied here" means.
    charter = "absent" if not current["goal"] else ("matches" if current == sealed else "differs")
    marker_fingerprint, marker_kept = _read_marker(registry, project_id)
    applied_here = marker_fingerprint == kickoff.seal_fingerprint(adopted)
    # The record's "kept a person's edit" note must agree with what the marker says was
    # done; a record write that failed right after the charter transaction leaves them
    # apart, and the adopt route then rewrites the record — nothing else.
    note_consistent = (not applied_here) or marker_kept is None or bool(adopted.get("charter_kept_edit", False)) == marker_kept
    # The record's own receipt has to name every approved task too: the last task can be
    # in the store while the record write that would have listed it failed, and "already
    # adopted" then would leave the receipt short for good. Incomplete means the adopt
    # route looks again — finds that task by key, records it, creates nothing.
    # … and name, for each key, the task *this* store holds under it: a receipt carrying
    # another machine's ids over a re-apply that crashed before the record write would
    # otherwise read as complete with the wrong ids in it for good.
    receipt_ids = {t.get("key"): t.get("task_id") for t in adopted.get("tasks", []) if isinstance(t, dict)}
    receipt_complete = all(
        resolved.get(k, (None, False))[0] is not None and receipt_ids.get(k) == resolved[k][0].task_id for k in approved_keys
    ) and note_consistent
    return {
        "applied_here": applied_here,
        "charter": charter,
        "tasks_recorded": len(approved_keys),
        "tasks_present": present,
        "receipt_complete": receipt_complete,
        "key_conflict": key_conflict,
        # Applied here, every approved task in the store and in the receipt: complete.
        # On a control plane that applied this seal the charter is the person's from then
        # on — edited (`differs`) or cleared (`absent`) alike — and neither makes the
        # adoption incomplete or gets written over; on one that never applied it, an
        # absent or differing charter is another tree's, and the re-apply sets it.
        "complete": applied_here and present == len(approved_keys) and receipt_complete and not key_conflict,
    }


def _adopted_marker_key(project_id: str) -> str:
    return f"kickoff_adopted:{project_id}"


def _marker(sealed: dict[str, Any], *, kept_edit: bool) -> str:
    """The marker's value: the seal's fingerprint, and whether this control plane wrote
    the approved charter (`wrote`) or left a person's edit in place (`kept`). The second
    part is what the record's `charter_kept_edit` must agree with — the marker is written
    in the same transaction as the charter, the record a moment later, and a record write
    that failed must not leave the record claiming the opposite of what was done."""
    return f"{kickoff.seal_fingerprint(sealed)}:{'kept' if kept_edit else 'wrote'}"


def _read_marker(registry: ProjectRegistry, project_id: str) -> tuple[str | None, bool | None]:
    raw = registry.store.get_setting(_adopted_marker_key(project_id))
    if not raw:
        return None, None
    fingerprint, _sep, verb = raw.partition(":")
    return fingerprint, (verb == "kept") if verb else None


#: One query for every task of the project — one SQLite statement is one snapshot. Paging
#: with OFFSET was not: an ordinary task inserted between two pages shifts the rows, and
#: the one "created but not yet recorded" task the resume looks for could fall off the
#: seam and be created twice. The number is a ceiling, not a page: past it the read is
#: incomplete and says so rather than resuming on a partial list.
_TASK_SCAN_CEILING = 100_000


def _is_active(manifest: Any) -> bool:
    """A task the person has not cancelled or archived. A stranger under an approved key
    blocks adoption only while active: cancelling or archiving it in the workbench — the
    two things the product can do to a task — is how the conflict is cleared."""
    state = getattr(manifest, "state", None)
    state_value = getattr(state, "value", state)
    return state_value != "cancelled" and not getattr(manifest, "archived_at", None)


def _is_the_approved_task(manifest: Any, approved: dict[str, Any]) -> bool:
    """A task under an approved key counts only if it *is* that task: created by an
    adoption (`metadata.kickoff`), with the approved title and kind. A key is a string
    any ordinary task write can carry; standing in for the approved task by the key alone
    would let the approved one go uncreated and the receipt name a stranger."""
    meta = manifest.metadata or {}
    # The whole approved content, not the title alone: the description is where the
    # acceptance lives, and the task's own kind is what the board acts on.
    return (
        meta.get("kickoff") is True
        and manifest.title == approved.get("title")
        and (getattr(manifest, "description", None) or "") == (approved.get("description") or "")
        and (getattr(manifest, "kind", None) or approved.get("kind")) == approved.get("kind")
        and meta.get("kickoff_kind", approved.get("kind")) == approved.get("kind")
    )


def _tasks_by_kickoff_key(tasks: TaskStore, project_id: str) -> dict[str, Any]:
    rows = tasks.list(project_id=project_id, limit=_TASK_SCAN_CEILING + 1, offset=0)
    if len(rows) > _TASK_SCAN_CEILING:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_task_scan_too_large", ceiling=_TASK_SCAN_CEILING))
    by_key: dict[str, list[Any]] = {}
    for manifest in rows:
        key = (manifest.metadata or {}).get("kickoff_key")
        if isinstance(key, str):
            by_key.setdefault(key, []).append(manifest)
    return by_key


def _resolve_key(by_key: dict[str, list[Any]], key: Any, approved: dict[str, Any]) -> tuple[Any, bool]:
    """Which task, if any, an approved key stands for, and whether a stranger blocks it.

    Among several tasks under one key the *approved* task wins whatever its state — a
    cancelled approved task is still the approved task, and recreating it would revive
    work the person ended. Only when no approved task is there does an active stranger
    count, and then as a conflict; a cancelled or archived stranger counts for nothing."""
    candidates = by_key.get(key, []) if isinstance(key, str) else []
    matching = [m for m in candidates if _is_the_approved_task(m, approved)]
    if matching:
        active = [m for m in matching if _is_active(m)]
        return (active[0] if active else matching[0]), False
    if any(_is_active(m) for m in candidates):
        return None, True
    return None, False


def _payload(project_id: str, project, registry: ProjectRegistry, tasks: TaskStore) -> dict[str, Any]:
    read = kickoff.read_kickoff(project.root, project_id)
    return {"project_id": project_id, **read.payload(), "here": _here(project_id, registry, tasks, read.record)}


@router.get("/projects/{project_id}/kickoff")
def get_kickoff(
    project_id: str,
    registry: ProjectRegistry = Depends(get_project_registry),
    tasks: TaskStore = Depends(get_task_store),
) -> dict[str, Any]:
    project = _project(registry, project_id)
    return _payload(project_id, project, registry, tasks)


@router.post("/projects/{project_id}/kickoff")
async def start_kickoff(
    project_id: str,
    request: StartKickoffRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
    tasks: TaskStore = Depends(get_task_store),
) -> dict[str, Any]:
    """Pass one: read the repository, draft the charter, ask the questions.

    Starting again over an existing record replaces it — that is "重新提问"; nothing
    adopted is undone by it, because adoption wrote elsewhere.
    """
    project = _project(registry, project_id)
    # Pinned to the record the *caller* saw, not to whatever is on disk when this request
    # arrives: a tab still showing an old round must not be allowed to start over a newer
    # one it never looked at. Checked now, so the agent is not spent on an ask that will
    # be refused, and again under the lock before writing.
    early = _snapshot(project_id, project)
    _check_revision(early, request.expected_revision)
    _refuse_start_over_a_halfway_adoption(project_id, registry, tasks, early)
    context = await run_in_threadpool(kickoff.gather_context, project.root)
    brief = request.brief.strip()
    # An empty repository and an empty brief is a prompt with no facts in it; the agent
    # would answer anyway, and the "analysis" would be invention. Refused, with the reason.
    if not brief and context.is_empty:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _detail("kickoff_brief_required"))
    try:
        generated = await run_in_threadpool(
            kickoff.generate, name=project.name, brief=brief, root=project.root, context=context,
        )
    except kickoff.KickoffError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    record = {
        "project_id": project_id,
        "previous_adoptions": [],
        "previous_adoptions_dropped": 0,
        "status": "questions",
        "round": kickoff.new_round_id(),
        "brief": brief,
        "context": context.summary(),
        "charter_draft": generated["charter_draft"],
        "questions": generated["questions"],
        "answers": {},
        "needs_mockup": generated["needs_mockup"],
        "mockup_note": generated["mockup_note"],
        "read_notes": generated["read_notes"],
        "proposal": None,
        "generated_at": kickoff.now_iso(),
        "answered_at": None,
        "proposed_at": None,
        "adopted_at": None,
    }
    def commit() -> None:
        with kickoff.project_lock(project.root):
            # The record must still be the one the caller saw — the agent took a while,
            # and answers, a proposal or an adoption can land inside one round meanwhile.
            # An absent record is not a malformed one: a file that appeared and is not a
            # record is refused too, not overwritten.
            read = _snapshot(project_id, project)
            _check_revision(read, request.expected_revision)
            if not read.available and read.reason != "absent":
                raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_not_available", reason=read.reason))
            _refuse_start_over_a_halfway_adoption(project_id, registry, tasks, read)
            # A new round does not erase the receipts of the old: what was adopted before
            # (the approved charter, the task keys and ids, when) is carried into the new
            # record, so "the sealed charter is still in the record" stays true across a
            # restart, and a file not yet committed loses nothing.
            record["previous_adoptions"], record["previous_adoptions_dropped"] = kickoff.previous_adoptions_after(read.record)
            _write(project, record)

    await run_in_threadpool(commit)
    # The same answer every route gives: the record as it is now, and — should it have
    # become an adopted one meanwhile (a checkout between the lock and this read) — what
    # this control plane holds of it, so the panel never claims "set" from the file alone.
    return _payload(project_id, project, registry, tasks)


@router.post("/projects/{project_id}/kickoff/answers")
async def save_kickoff_answers(
    project_id: str,
    request: KickoffAnswersRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
    tasks: TaskStore = Depends(get_task_store),
) -> dict[str, Any]:
    project = _project(registry, project_id)

    # One synchronous section, run off the event loop with the project's lock held inside
    # it. A `threading.Lock` taken *on* the loop thread and held across an `await` would
    # block the loop that the awaited work needs to resume on — the first request would
    # never come back to release, and the second would wait forever.
    def commit() -> None:
        with kickoff.project_lock(project.root):
            # The caller's answers replace the set — which is right only if the set it
            # started from is the set on disk. A sibling tab's save in between moves the
            # revision, and this save is refused rather than dropping that answer.
            read = _snapshot(project_id, project)
            _check_revision(read, request.expected_revision)
            record = _record_of(read)
            # A record whose adoption has started is terminal for this round: an old tab
            # saving an answer must not walk it back to "answered" and reopen refine and
            # adopt over tasks that already exist. Ask again from the start instead.
            if record.get("status") in {"adopting", "adopted"}:
                raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_already_adopted"))
            known = {q["id"] for q in record.get("questions", [])}
            # Trimmed, and blanks dropped: an answer of spaces is not an answer, and a record
            # marked "answered" on the strength of one would offer a proposal built on nothing.
            record["answers"] = {k: v.strip() for k, v in request.answers.items() if k in known and v.strip()}
            record["status"] = "answered" if record["answers"] else "questions"
            record["answered_at"] = kickoff.now_iso() if record["answers"] else None
            # Answers changed: a proposal built on older answers is not this proposal.
            record["proposal"] = None
            record["proposed_at"] = None
            _write(project, record)

    await run_in_threadpool(commit)
    # The same answer every route gives: the record as it is now, and — should it have
    # become an adopted one meanwhile (a checkout between the lock and this read) — what
    # this control plane holds of it, so the panel never claims "set" from the file alone.
    return _payload(project_id, project, registry, tasks)


@router.post("/projects/{project_id}/kickoff/refine")
async def refine_kickoff(
    project_id: str,
    request: KickoffRefineRequest | None = None,
    registry: ProjectRegistry = Depends(get_project_registry),
    tasks: TaskStore = Depends(get_task_store),
) -> dict[str, Any]:
    """Pass two: from the answers, the charter to adopt and the first tasks."""
    project = _project(registry, project_id)
    expected = request.expected_revision if request is not None else None
    early = _snapshot(project_id, project)
    _check_revision(early, expected)
    record = _record_of(early)
    if record.get("status") not in {"answered", "proposed"}:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_not_answered"))
    if not record.get("answers"):
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_unanswered"))
    context = await run_in_threadpool(kickoff.gather_context, project.root)
    try:
        proposal = await run_in_threadpool(
            kickoff.refine,
            name=project.name, brief=record.get("brief", ""), root=project.root, context=context,
            charter_draft=record["charter_draft"], questions=record["questions"], answers=record["answers"],
        )
    except kickoff.KickoffError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    # The agent took a while. Re-read before writing, under the project's lock so the
    # compare and the write are one step: answers saved meanwhile — from another tab, or
    # a second thought — are not to be overwritten by the copy this request read minutes
    # ago, and a proposal built on the old answers is not this record's proposal. Said as
    # a conflict; the person refines again. The status too: an adoption that completed
    # while the agent worked must not be walked back to "proposed" — that is a second
    # adoption waiting to happen.
    def commit() -> None:
        with kickoff.project_lock(project.root):
            # The revision the caller saw is the revision this proposal was built from; a
            # save in between — an answer, an adoption — moved it, and this write is refused.
            read = _snapshot(project_id, project)
            _check_revision(read, expected)
            current = _record_of(read)
            if current.get("status") not in {"answered", "proposed"}:
                raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_state_changed"))
            # The first pass's verdict on the UI stands: a second pass that says "no
            # mockup needed" over a project the first pass saw screens in does not lift the
            # rule that the mockup task comes first. Either pass saying UI means UI.
            needs_mockup = bool(proposal["needs_mockup"]) or bool(current.get("needs_mockup"))
            proposal["needs_mockup"] = needs_mockup
            proposal["mockup_task_missing"] = needs_mockup and (
                not proposal["tasks"] or proposal["tasks"][0]["kind"] != kickoff.MOCKUP_TASK_KIND
            )
            current["proposal"] = proposal
            # What *this* pass was shown. The repository may have moved since the first
            # read, and the proposal was drawn from this text, not that one.
            current["context_at_proposal"] = context.summary()
            current["status"] = "proposed"
            current["proposed_at"] = kickoff.now_iso()
            _write(project, current)

    await run_in_threadpool(commit)
    # The same answer every route gives: the record as it is now, and — should it have
    # become an adopted one meanwhile (a checkout between the lock and this read) — what
    # this control plane holds of it, so the panel never claims "set" from the file alone.
    return _payload(project_id, project, registry, tasks)


@router.post("/projects/{project_id}/kickoff/adopt")
async def adopt_kickoff(
    project_id: str,
    request: AdoptKickoffRequest,
    registry: ProjectRegistry = Depends(get_project_registry),
    tasks: TaskStore = Depends(get_task_store),
) -> dict[str, Any]:
    """The one step that commits: the charter through the human path, the tasks into the
    store, in the order proposed. Records what was adopted next to what was proposed, so
    an edit made at adoption is visible later."""
    project = _project(registry, project_id)
    # Held for the whole adoption: two requests that both read "proposed" — two tabs, a
    # double click, a retry that overtook a slow first attempt — would each seal a payload
    # and each create the tasks. Under the lock the second one reads what the first wrote
    # ("adopting" or "adopted") and resumes or is refused. The store has no unique key
    # for kickoff tasks, so this lock is what makes `existing_by_key` sufficient; the
    # control plane is one process over one SQLite file (see kickoff.project_lock).
    # Synchronous and off the loop: the lock is a `threading.Lock`, and holding it on the
    # loop thread across an await would deadlock the second request against the first.
    def commit() -> dict[str, Any]:
        with kickoff.project_lock(project.root):
            return _adopt_locked(project_id, project, request, registry, tasks)

    return await run_in_threadpool(commit)


def _adopt_locked(project_id: str, project, request: AdoptKickoffRequest, registry: ProjectRegistry, tasks: TaskStore) -> dict[str, Any]:
    read = _snapshot(project_id, project)
    record = _record_of(read)
    status_now = record.get("status")
    # The proposal the caller adopts is the one it saw. A tab still showing round one's
    # proposal while round two has been proposed holds an older revision, and is refused
    # — otherwise round one's charter and tasks would be sealed under round two's keys.
    # A *resume* is exempt: the seal itself moved the revision, so the very request whose
    # answer was lost holds the old one; its body is ignored anyway, and resuming is
    # idempotent, so "send the same request again" is allowed to mean what it says.
    # Once is once. A retry after a lost response, or a double click, must not create
    # the tasks again; "already adopted" is the answer, with the record attached — and it
    # is the answer *before* the revision is looked at, since the very request that
    # adopted carries the revision from before the adoption.
    here = _here(project_id, registry, tasks, record)
    if here is not None and here.get("scan_failed"):
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_task_scan_too_large", ceiling=_TASK_SCAN_CEILING))
    if here is not None and here.get("key_conflict"):
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_key_conflict"))
    if status_now == "proposed":
        # The scan the seal will depend on, run before the seal: refused now, nothing
        # is written; refused after it, the adoption would be stuck half done.
        _tasks_by_kickoff_key(tasks, project_id)
    if status_now == "adopted" and here is not None and here["complete"]:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_already_adopted"))
    # An adopted record whose charter or tasks are *not here* — the repository was cloned
    # or the database was not restored — is re-applied from the seal: what is missing on
    # this control plane is created, what is present is left alone.
    reapply = status_now == "adopted"
    if status_now not in {"adopting", "adopted"}:
        _check_revision(read, request.expected_revision)
    else:
        # A resume or a re-apply is pinned as well — to *this seal*: the revision the
        # record has now, or the one the proposal had when it was sealed (the request
        # whose answer was lost holds that one). A page still showing branch A's record
        # after a checkout to B holds neither, and must not execute B's seal.
        sealed_from = (record.get("adopted") or {}).get("sealed_from_revision")
        if request.expected_revision not in {record.get("revision"), sealed_from}:
            raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_stale", revision=record.get("revision")))
        if request.expected_revision == sealed_from and request.expected_revision != record.get("revision"):
            # Two tabs can hold the same pre-seal revision with different edits; the one
            # that sealed first is the adoption, and the other must not "resume" it as its
            # own — its body would be ignored and its choices silently replaced. On the
            # pre-seal revision the body has to be the seal's.
            sealed_now = record.get("adopted") or {}
            same_charter = kickoff.charter_triplet(request.charter.model_dump()) == kickoff.charter_triplet(sealed_now.get("charter"))
            same_tasks = [(t.title, t.description, t.kind) for t in request.tasks] == [
                (a.get("title"), a.get("description"), a.get("kind")) for a in sealed_now.get("approved", [])
            ]
            if not (same_charter and same_tasks):
                raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_seal_mismatch", revision=record.get("revision")))
    if status_now not in {"proposed", "adopting", "adopted"}:
        raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_not_proposed"))

    # Not atomic across the charter, the tasks (both in the control-plane DB) and the
    # record (a file) — so it is made *sealed and resumable* instead:
    #
    #   1. The first call seals what was approved — the edited charter and the ticked
    #      tasks, in order, each with a stable key — into the record, before any side
    #      effect. A retry uses the sealed payload and ignores its own body: the tasks the
    #      person unticked cannot come back, and the charter they edited cannot be
    #      overwritten by a reload that re-sent the original proposal.
    #   2. Each task is created with its key in its metadata, and the store is asked for
    #      that key before creating — so a crash between "created" and "recorded" is
    #      recovered by finding the task, not by creating it twice. Titles are not
    #      identity: two approved tasks may share one.
    #   3. The charter write is idempotent by nature.
    if status_now == "proposed":
        approved = [
            # Round, position and a hash of the content are in the key: a later round's
            # first task, or the same round's different first task on another branch, must
            # not be found in the store as this one and skipped as "already created".
            {
                "key": kickoff.task_key(record["round"], index, {"title": task.title, "description": task.description, "kind": task.kind}),
                "title": task.title, "description": task.description, "kind": task.kind,
            }
            for index, task in enumerate(request.tasks)
        ]
        record["adopted"] = {
            "charter": request.charter.model_dump(),
            "approved": approved,
            "tasks": [],
            # The revision the proposal had when it was adopted: the very request that
            # sealed it holds this one, and a retry of that request is let through on it.
            "sealed_from_revision": record.get("revision"),
            # What the project's charter was before this adoption — so a resume can tell
            # "not written yet" from "written" from "edited by a person meanwhile" by
            # looking, instead of guessing from a flag that a crash may have skipped.
            "charter_before": kickoff.charter_triplet(registry.get(project_id).charter),
            "charter_written": False,
            "charter_kept_edit": False,
        }
        record["status"] = "adopting"
        if not kickoff.adopted_record_fits(record):
            # Said now, with nothing written: the seal would fit and the adopted record
            # after it would not, and that record follows side effects it cannot undo.
            raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_record_too_large", limit=kickoff.RECORD_CHARS))
        _write(project, record)
    sealed = record["adopted"]
    charter = sealed["charter"]
    approved = sealed["approved"]
    created: list[dict[str, Any]] = list(sealed.get("tasks", []))
    # Whatever the status: only the recorded tasks this store holds under their key, with
    # the recorded id, count as created; the rest are looked up by key and, failing that,
    # created. A half-done record cloned to another machine carries the other machine's
    # ids, and trusting them would have turned the first resume into a false "adopted".
    by_key_now = _tasks_by_kickoff_key(tasks, project_id)
    approved_by_key = {t.get("key"): t for t in approved if isinstance(t, dict)}
    created = [
        t for t in created
        if isinstance(t.get("task_id"), str) and isinstance(t.get("key"), str)
        and (lambda m: m is not None and m.task_id == t["task_id"])(_resolve_key(by_key_now, t["key"], approved_by_key.get(t["key"], {}))[0])
    ]
    if reapply:
        # The charter, if absent here, is written from the seal — an absent charter is
        # not "edited by a person meanwhile".
        # The charter is set to the approved one unless it already is: on a control plane
        # where this adoption is incomplete, a differing charter is another branch's or a
        # leftover, not an edit of this one — and the panel says before the click that
        # re-applying sets it.
        # The charter is written only where this seal was never applied: on this control
        # plane, after the adoption, the charter is the person's — cleared through the
        # editor included — and the panel promises exactly that.
        if here is not None and not here["applied_here"] and here["charter"] != "matches":
            wanted = kickoff.charter_triplet(charter)
            # The charter and the "applied here" marker in ONE transaction: a retry after
            # a task failed must see "applied here" and leave a charter edited meanwhile
            # alone, and a crash between two separate writes would have left exactly the
            # gap that lets the approved charter be written a second time over that edit.
            registry.store.set_charter_with_setting(
                project_id, wanted, _adopted_marker_key(project_id), _marker(sealed, kept_edit=False),
            )
            # The approved charter *was* written here now; a "kept a person's edit" note
            # from the machine the record came from is no longer true of this one — and
            # that is written to the record now, before the task stage that can fail: a
            # retry sees "applied here", skips this branch, and would otherwise have
            # written the old note back.
            sealed["charter_kept_edit"] = False
            _write(project, record)
        elif here is not None and here["applied_here"]:
            # Applied here already: the record's note follows what the marker says was
            # done (a record write may have failed right after the charter transaction).
            _fp, marker_kept = _read_marker(registry, project_id)
            if marker_kept is not None:
                sealed["charter_kept_edit"] = marker_kept
        elif here is not None and not here["applied_here"]:
            # The charter already reads as the approved one — nothing to write there, but
            # "applied here" still has to be on record before the task stage: a failure
            # there, an edit, and a retry would otherwise take the edit for another
            # tree's charter and write the approved one over it.
            registry.store.set_setting(_adopted_marker_key(project_id), _marker(sealed, kept_edit=False))

    # The charter is written once. Whether it was is *looked at*, not guessed: the seal
    # holds the charter as it was before adopting, so on a resume the project's current
    # charter is one of three things —
    #   still the one from before  → the first pass never got there: write it now;
    #   already the sealed one     → written, the flag write was what the crash skipped;
    #   neither                    → a person edited it through the charter editor while
    #                                the adoption sat half done: theirs stands, and the
    #                                record says the sealed charter was not applied.
    # A flag alone could not tell the second case from the third; that is why the
    # "before" is sealed too. Two things this look does not close, both said: a save from
    # the charter editor landing between this read and `set_charter` is overwritten (the
    # editor has no version and the store no compare-and-set — workspace CR-57, `defer`,
    # names the fix: the adopt write goes through the editor's CAS once adr/0005 gives it
    # one); and a person who deliberately restored the *before* text after a crash that
    # skipped the flag write gets the approved charter written again — the resume note in
    # the UI states exactly that before the click.
    if not sealed.get("charter_written") and not reapply:
        current = kickoff.charter_triplet(registry.get(project_id).charter)
        wanted = kickoff.charter_triplet(charter)
        before = sealed.get("charter_before")
        if current == wanted:
            pass
        elif before is None or current == kickoff.charter_triplet(before):
            registry.set_charter(
                project_id,
                goal=wanted["goal"],
                non_goals=wanted["non_goals"],
                success_criteria=wanted["success_criteria"],
            )
        else:
            sealed["charter_kept_edit"] = True
        sealed["charter_written"] = True
        _write(project, record)
    # What the store already holds under a kickoff key — the crash-between-create-and-
    # record case. Every page of the project's tasks, not the first: a project with more
    # tasks than one page would hide the one created a moment ago behind older ones.
    existing_by_key = _tasks_by_kickoff_key(tasks, project_id)
    recorded_keys = {t["key"] for t in created}
    for index, task in enumerate(approved):
        if task["key"] in recorded_keys:
            continue
        manifest, conflict = _resolve_key(existing_by_key, task["key"], task)
        if conflict:
            # A stranger under the key: neither taken for the approved task nor doubled.
            raise HTTPException(status.HTTP_409_CONFLICT, _detail("kickoff_key_conflict"))
        if manifest is None:
            manifest = tasks.create(CreateTaskRequest(
                project_id=project_id,
                title=task["title"],
                description=task.get("description", ""),
                kind=task.get("kind", "custom"),
                # Earlier in the proposal, higher on the board: the agent ordered them.
                # The approved order is the priority: distinct values for every one of
                # the APPROVED_TASKS the route allows (100, 96, … 24), so the store's
                # tie-break never reorders the tail.
                priority=100 - index * 4,
                metadata={"kickoff": True, "kickoff_key": task["key"], "kickoff_kind": task.get("kind", "custom")},
                created_by="user",
            ))
        created.append({"key": task["key"], "task_id": manifest.task_id, "title": manifest.title, "kind": task.get("kind", "custom")})
        recorded_keys.add(task["key"])
        record["adopted"]["tasks"] = created
        _write(project, record)
    record["status"] = "adopted"
    record["adopted_at"] = record.get("adopted_at") or kickoff.now_iso()
    # This control plane applied this round: the marker `_here` reads. Written after the
    # charter and the tasks, before the record says adopted.
    registry.store.set_setting(_adopted_marker_key(project_id), _marker(record["adopted"], kept_edit=bool(record["adopted"].get("charter_kept_edit", False))))
    _write(project, record)
    return _payload(project_id, project, registry, tasks)
