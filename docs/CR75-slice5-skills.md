# CR-75 slice 5 — skills management

Workspace CR-75 step ⑤: "skills managed on their own (add / edit / delete), shared ones
and ones chosen per project". Decided with the owner on 2026-11-10.

## Before

The same skill lived once per agent CLI, kept in step by hand: `~/.claude/skills`,
`~/.codex/skills`, claude-workspace's `.claude/commands` plus hand-made codex copies in
its `.agents/skills`, kiro-workspace's `.kiro/skills/*.md` listed one by one in each kiro
agent profile. The Skills page was a placeholder, and `/skills` itself was still on the
0.5 retirement list, so it redirected to `/control-plane`.

## Decisions (owner, 2026-11-10)

1. **One original per skill, in Agora's library**: `<claude-workspace work root>/skills/<name>/SKILL.md`,
   next to `tasks/` and `lessons/`, so it is versioned with the work data.
   `AGORA_SKILLS_LIBRARY` names another place (and is required when zero or several
   claude-workspace work roots are registered — the location is never guessed).
   Skills already elsewhere are imported one at a time by the person; the original is
   never moved or changed.
2. **Installed copies stay out of the project's git**: each copy's directory gets a line in
   the repository's `.git/info/exclude`, anchored at the repository's top (a project below
   it gets its path in the line). Uninstall leaves the lines: linked worktrees share one
   exclude file, and a line for a missing directory costs nothing.
3. **The workflow's own commands** (claude-workspace `adr`, `kickoff`, `save`, …) are listed
   as "provided by the workflow" and not edited here. A library skill may not take one of
   their names: in claude a skill replaces the command of the same name.
4. **First slice**: library + add/edit/delete + import + install per project, local
   projects, all three CLIs. Installing into the global directories (a skill for every
   project) and remote projects are the next slice.

Not in scope (CR-7, backlog since 2026-10-23): versions, a registry, publishing.

## How it works

One format: `SKILL.md` with `name` (must equal the directory name) and `description` in
front matter. All three CLIs read it:

| CLI | where a project's skills are read | note |
|---|---|---|
| claude | `.claude/skills/<name>/` | the work page starts claude in the tool directory, so it now adds the task's `primary_repo` with `--add-dir`, which loads that directory's skills (docs: code.claude.com/docs/en/skills) |
| codex | `.agents/skills/<name>/` | codex's repository skill folder |
| kiro | `.kiro/skills/<name>/` | kiro 3.1.0's built-in agent reads `skill://.kiro/skills/*/SKILL.md`; a kiro-workspace task agent reads only what its profile lists, which Agora does not edit |

Each installed copy carries `.agora-skill.json` (skill name + hash of what was copied).
From it:

- `current` / `outdated` (the library changed since) / `modified` (edited in place) —
  a modified copy is never overwritten or removed without `force`, which the page asks
  for explicitly;
- a directory **without** the marker is `foreign`: never touched; it refuses the whole
  install before anything is written.

The project unit is the work page's project (a workspace task); the repository is its
`primary_repo` (kiro-workspace: `project_path`). A project without one is listed with the
reason. Paused and archived projects still count: their copies keep a
library original from being deleted, and can still be removed.

## Carried over from the 2026-08-27 mockup review

These were the placeholder page's content; they still hold:

- Three tiers: project-only / every project / published. Most skills stay in the first.
  Promotion is deliberate, never automatic.
- Publishing is per skill, classification before redaction (a business process with no
  identifier in it is the dangerous kind). Nothing is uploaded by default.
- "Used by N projects" is not shown: whether an agent loaded a skill is not observable from
  outside. "Installed in N" and "last changed" are.
- No "repeated solution detected → skill generated" (v0.1–0.4's `skills/extractor.py`,
  deleted).
