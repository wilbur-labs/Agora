"""What a failed review run's reason means, and who may stand in for its reviewer.

CR-120 / CR-121 (2026-09-25, found by the owner in real use): a codex review that
hit its usage limit settled as "process exited with a non-zero status", the task
stopped, and nothing offered a way on. The owner's rule for that case is the one
the workspace's own review gate already follows (adr/0017 there): **when codex is
out of quota, kiro reviews instead** — a substitute for that run, never a second
opinion beside it.

Only a usage limit triggers the substitution. A reviewer that failed for any other
reason — a crash, a timeout, a bad login — may be telling us something about the
work or the setup, and quietly asking a different vendor would bury that. Those
still end in the blocker, now with the reason written the way a person reads it.
"""
from __future__ import annotations

import re

#: Who stands in for whom when the original reviewer is out of quota. One hop only:
#: the substitute's own failure ends in the ordinary blocker.
REVIEW_SUBSTITUTES: dict[str, str] = {"codex": "kiro"}

#: The vendor phrase for "you have used up your allowance". Matched on the
#: provider's own words (``TranscriptReader.vendor_error``), not on exit codes: a
#: codex that ran out of quota exits 1, exactly like one that crashed. Only this
#: phrase — "rate limit exceeded; retry in 1 second" is a transient that should
#: surface, and "Disk quota exceeded" is a machine problem, not an allowance
#: (kiro round 3). Neither may quietly hand the review to another vendor.
_USAGE_LIMIT = re.compile(r"usage limit", re.IGNORECASE)

#: What Agora writes when the process failed and said nothing about why.
GENERIC_FAILURE = "process exited with a non-zero status"

#: "... or try again at 8:44 PM." — codex's wording for when the allowance resets.
_RETRY_AT = re.compile(r"try again at ([^.]+?)\.?\s*$", re.IGNORECASE)


def is_usage_limit(reason: str | None) -> bool:
    return bool(reason) and _USAGE_LIMIT.search(reason) is not None


def retry_at(reason: str | None) -> str | None:
    """When the provider said the allowance comes back, as it said it; else None."""
    if not reason:
        return None
    match = _RETRY_AT.search(reason.strip())
    return match.group(1).strip() if match else None


def failure_reason(error_message: str | None, stderr_tail: str | None) -> str | None:
    """The most telling reason on record: the error, unless it is only the generic
    exit-status sentence and stderr has something to say (a login that expired)."""
    last = next((line.strip() for line in reversed((stderr_tail or "").splitlines()) if line.strip()), None)
    if error_message and error_message != GENERIC_FAILURE:
        return error_message
    return last or error_message


def readable_failure(adapter: str, reason: str | None) -> str:
    """One sentence for a failed run's reason, the way the owner reads it."""
    if reason and reason.startswith("agent reported: "):
        reason = reason[len("agent reported: "):]
    if is_usage_limit(reason):
        when = retry_at(reason)
        return f"{adapter} 额度用完" + (f"（它说 {when} 之后可以再用）" if when else "")
    if reason and "not a provisioned worktree" in reason:
        # The CR-61 shape: the run never started because its worktree did not exist.
        # Said in words, with the original kept as evidence rather than replaced.
        return f"{adapter} 的工作目录没有建好，运行没能开始（原始错误：{reason}）"
    return reason or "（没有错误详情）"
