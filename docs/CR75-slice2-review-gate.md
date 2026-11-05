# CR-75 slice 2 — a commit is refused unless its content was reviewed

Status: design, 2026-11-05. Owner decisions (2026-11-05, all three recommended options):

1. **Block, and check afterwards.** A local hook refuses a commit whose content no review
   has read; Agora additionally reports commits that reached the branch without a review
   record, which is how `--no-verify` shows up.
2. **Skipping takes a written reason.** `AGORA_REVIEW_SKIP="<reason>" git commit …` is
   allowed; the reason is written into the commit message. No reason, no commit.
3. **Installed on Agora-private only**, by an explicit command. Other repositories later,
   through a per-project switch in Agora (not this slice).

## What the gate claims, exactly

> Every path a commit changes has, in exactly the content being committed, been read by a
> review that `codex-review.sh` or `kiro-review.sh` accepted as having read it — or the
> commit carries the reason it was not.

What it does **not** claim, and must never be described as claiming:

- **Not that the review approved.** A review exit 0 means the reviewer ran and read the
  change; its findings are triaged by the agent. Whether findings were handled is still
  the agent's job (and the history's: rounds and outcomes stay in the commit message and
  PROGRESS.md).
- **Not tamper-proof.** Stamps live in `.git/`; anyone who can commit can write one, and
  `--no-verify` skips both hooks. The hook stops *forgetting*. Deliberate bypass is what
  the after-the-fact check (part D) is for, and that check can see `--no-verify` but not a
  forged stamp. Said once here so no later sentence rounds it up.

## A. Review stamp (claude-workspace: `codex-review.sh`, `kiro-review.sh`)

Only for the default scope `--uncommitted` (the pre-commit case). `--commit`, `--base`,
`--prompt-only` never stamp.

1. **Before** the reviewer starts: snapshot the manifest of the uncommitted change —
   every path that differs from `HEAD` in the index or the working tree, plus untracked
   non-ignored files (`git status --porcelain=v1 -z --untracked-files=all --no-renames`).
   For each path, the content id is `git hash-object <path>` of the working-tree file, or
   `deleted` if it is gone. (Index-only content that differs from the working tree is not
   what the reviewer reads — the reviewer reads the working tree — so the working-tree blob
   is the one recorded.)
2. Run the review exactly as today.
3. **After**, only if the script is about to exit 0 (for codex: the read check passed):
   take the manifest again. If it differs from the first, the tree moved under the review:
   **no stamp**, and the script says so and exits 5 (new code; 4 stays "did not read").
   This turns the constitution's "stop editing the tree while a review runs" into
   something that fails instead of a sentence.
4. Write `<git-common-dir>/agora-review/stamps/<UTC timestamp>-<reviewer>.stamp`: NUL-
   terminated records `reviewer=<codex|kiro>`, `at=<iso>`, `head=<sha>`, then
   `<blob> <path>` / `deleted <path>` per file (any path is safe in a NUL record, and the
   hooks match a whole record with `grep -zFx`, no JSON parser in sh). Written to a temp
   name and renamed. `git-common-dir`, so worktrees share stamps. Only when the gate is
   installed in that repository: a review elsewhere leaves nothing in `.git/`.

Stamps older than 14 days are pruned when a new one is written (they are never needed to
commit content that has since changed, and the trailer carries the record).

## B. `pre-commit` hook (installed per repository)

Plain POSIX sh + git (Git for Windows runs hooks in its own sh). No Python, no path back
into claude-workspace, so the hook works from any agent, any terminal, any worktree.

1. If `AGORA_REVIEW_SKIP` is set and non-blank: allow; write `skip:<reason>` to
   `<git-dir>/agora-review/pending` for part C. Blank or whitespace = not set.
2. Staged changes: `git diff --cached --name-status -z --no-renames` against `HEAD` (or
   the empty tree for a root commit). For each path: staged blob `git rev-parse :<path>`,
   or `deleted`.
3. A path is **covered** if some stamp lists that path with that exact blob (or
   `deleted`). Coverage may come from several stamps (a slice reviewed in two parts).
4. All covered → allow; write `reviewed:<reviewer list>:<stamp ids>` to `pending`.
   Any uncovered → refuse, list each uncovered path with why ("never reviewed" /
   "changed since it was reviewed"), and print both ways forward: run the review, or
   commit with a reason.
5. No staged paths (`--allow-empty`, message-only amend) → allow, nothing written.
6. Stamps unreadable (directory missing, malformed file) → that stamp covers nothing; the
   hook never treats "could not read" as "reviewed".

`pre-commit` starts by deleting any `pending` / `last` file left by an earlier commit
that never reached `commit-msg` (one made with `--no-verify`, or aborted), so stale state
can never be consumed by a later commit. `pending` also records the index tree
(`git write-tree`); part C refuses to use a `pending` whose tree is not the one being
committed.

**`--amend`** (codex design review round 1): the replacement commit's trailer speaks for
its whole content, so the check must cover everything it changes against its parent, not
only what the amend added — otherwise amending a skipped commit with one reviewed file
would relabel all of it "reviewed" and drop the skip reason.

No hook that runs *before* the commit can tell an amend reliably: `prepare-commit-msg`
gets `commit <sha>` for `--amend` but `message` for `--amend -m/-F` (codex design review
round 2). So the check is made *after*, where it is certain:

- `pre-commit` records in `pending` the `HEAD` it checked against (`base`).
- `commit-msg` writes the trailer and moves `pending` to `last`.
- **`post-commit`** reads and deletes `last`. If the new commit's parent (none for a root
  commit) is not `base`, the commit replaced `base` — an amend. When the trailer it got
  was `Reviewed-by`, `post-commit` repeats the coverage check of the commit's whole change
  against its parent. Not covered → **`git reset --soft <base>`**: the amend is undone,
  the old commit (and its trailer) is back, the amended content stays staged, nothing in
  the working tree moves. It prints why and the two ways forward (review everything the
  commit changes, or amend with a reason).
- `post-commit` runs whether or not `--no-verify` was given; with `--no-verify` there is
  no `last` (neither of the other hooks ran) and it does nothing — the commit has no
  trailer, which part D reports.
- A skipped amend needs no recheck: its `Review-skipped:` covers the whole commit.

Replays — rebase, cherry-pick, and the commit concluding a conflicted merge — are
recognised by `rebase-merge/`, `rebase-apply/`, `CHERRY_PICK_HEAD`, `MERGE_HEAD` in the
git dir. They keep the replayed commit's message, trailers included, and the gate adds
nothing; whether that record still fits is decided afterwards by `Review-patch` (a
hand-resolved conflict changes the patch → no record). A revert or squash commit is new
content nobody reviewed as such: it is checked like any other commit.

Merges, rebases and cherry-picks do not run `pre-commit` (git does not call it for them);
their commits keep the trailers of the commits they replay. A real merge commit has no
trailer of its own — part D counts merge commits separately, not as unreviewed.

## C. `commit-msg` hook

Reads and deletes `<git-dir>/agora-review/pending`, removes any existing `Reviewed-by:` /
`Review-skipped:` trailer lines this gate wrote, then appends one git trailer:

- `Reviewed-by: codex` / `Reviewed-by: kiro` / `Reviewed-by: codex, kiro`
- `Review-skipped: <reason>` (single line; newlines in the reason become spaces)

Plus, on both kinds, **`Review-patch: <patch-id>`** — `git patch-id --stable` of the
change the trailer speaks for (`git diff --cached --binary --no-renames --no-color
--no-ext-diff --src-prefix=a/ --dst-prefix=b/ <base>`). It binds the record to the
content (codex design review round 3): a trailer carried along by
`--amend --no-edit --no-verify` with new content, or by a rebase / cherry-pick whose
conflicts were resolved by hand, no longer matches the commit's own patch, and part D
counts such a commit as having no record. A clean rebase keeps the patch-id, so a
reviewed commit stays reviewed after one. The same property has a cost, stated so no
sentence rounds it up: `--stable` ignores whitespace, so a whitespace-only change made
under a carried-along trailer (which takes `--no-verify`) still matches (kiro review
round 3, accepted as within "stops forgetting, not cheating"). Same diff options on both sides, or the ids
never match.

An amend that `post-commit` keeps (whole change covered) still carries the patch-id of
only the amended part, so `post-commit` rewrites the trailers once with
`git commit --amend --no-verify -F <message with the corrected Review-patch>`; that
second commit finds no `last` and does nothing more.

Uses `git interpret-trailers` so it sits with `Co-Authored-By:`. If `pending` is absent
(the hook ran without pre-commit, e.g. `git commit --no-verify` skips both), nothing is
added — which is precisely the trace part D looks for.

Kiro standing in for codex is therefore recorded by the gate itself; the existing rule
"say so in the commit message" is met automatically, though a human sentence is still
welcome.

## D. After-the-fact check (Agora)

Per project repository with the gate installed:

- `<git-common-dir>/agora-review/installed`: `{ "at": "<iso>", "commit": "<sha HEAD at install>" }`.
- Agora runs `git log --format=%H%x00%P%x00%(trailers:key=Reviewed-by,key=Review-skipped,key=Review-patch,unfold,separator=%x01)%x00 <installed.commit>..HEAD`
  — keys kept, not `valueonly`, so `Reviewed-by: codex` and `Review-skipped: codex`
  cannot be confused (codex design review round 3) — and classifies each commit:
  **merge** (two parents), **reviewed** (`Reviewed-by: codex|kiro…` and a `Review-patch`
  equal to the commit's own patch-id against its first parent), **skipped (with reason)**
  (`Review-skipped:` and a matching `Review-patch`), otherwise **no record** (no trailer,
  a human `Reviewed-by: <name>`, or a patch-id that does not match). Patch-ids are
  computed per commit with the same diff options as part C, cached by commit sha.
- The overview card shows one line under the project, only when the gate is installed:
  - all reviewed → nothing extra (quiet is the normal state)
  - skipped > 0 → "N 个提交跳过评审（有理由）" (link-free; the reasons are in `git log`)
  - no record > 0 → "N 个提交没有评审记录" in the warning colour
  - not installed → nothing on the card (installation is not in this slice's UI)
  - git failed / `installed` unreadable → "评审记录读不到", never "0"
- Read with the workspace sources — on page load and when the page comes back into view,
  not on the 10-second terminal poll (kiro review round 2: the doc said otherwise) —
  bounded to 500 commits (beyond that: "超过 500 个，
  只看了最近 500 个" — said, not silently truncated).

## E. Installing (claude-workspace `scripts/review-gate.sh`)

`review-gate.sh install|uninstall|status <repo>`

- `install` refuses when `core.hooksPath` is set or a non-gate `pre-commit`,
  `commit-msg` or `post-commit` already exists (names the file; never overwrites or chains
  someone else's hook). Writes the three hooks and the shared `agora-review-lib.sh`, each
  with a marker line, then `installed`.
- `uninstall` removes only files carrying the marker; leaves stamps.
- `status` prints installed/not, install commit, number of stamps.

Run once on Agora-private by me, with the owner's go-ahead (given 2026-11-05).

## Changes elsewhere

- `Agora-private/scripts/release.ps1` commits "release: vX.Y.Z" — content (version
  files) no reviewer read. It sets `AGORA_REVIEW_SKIP="release version bump"` for that
  one commit, so releases are recorded as skipped-with-reason rather than blocked.
- claude-workspace `CLAUDE.md` "Code review" section: one paragraph — the gate exists
  where installed, what exit 5 means, how to skip with a reason. (Tool repo, public: no
  internal names.)

## Tests

- claude-workspace: a shell test that builds a temp repo, installs the gate, and checks:
  unreviewed commit refused; stamped content commits with trailer; content changed after
  the stamp refused; partial commit of stamped files allowed; skip with reason allowed and
  trailer present; blank reason refused; `--no-verify` commit has no trailer and leaves
  no state the next commit picks up; amending a skipped commit with one reviewed file is
  undone, both with the editor and with `--amend -m` (and allowed with a reason, trailer =
  the new reason); amending a fully reviewed commit with reviewed content is kept;
  amending a root commit; cherry-pick of a
  reviewed commit keeps its trailer; existing
  foreign hook makes install refuse; tree changed during review → no stamp (simulated by
  a fake reviewer command).
- Agora backend: classification of trailers (reviewed / skipped / merge / none), install
  file missing → not installed, unreadable → error not zero, the 500 bound.
- Frontend: card line per state.

## Order of work

1. claude-workspace: stamp + hooks + `review-gate.sh` + shell test (one review/commit
   there).
2. Install on Agora-private; this very slice's later commits are the first to pass it.
3. Agora: part D + release.ps1 + PROGRESS (review/commit in Agora-private, through the gate).
