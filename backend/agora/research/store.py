"""File-backed evidence and artifact store for research tasks."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ResearchTask, WorkerResult


FINAL_REPORT_TEMPLATE = """# Final Report

## Short Answer

Agora created this research task and routed it to the workers listed below. Real worker execution is not enabled yet, so this report records the plan, dispatch intent, and verification gaps.

## Recommendation

Use the router decision as the execution plan. Enable the required worker adapters before treating this as a completed research result.

## Evidence

No external sources have been collected yet.

## Comparison

See `router-decision.json` for the selected primary and secondary workers.

## Risks And Counterarguments

- Worker output is currently stubbed unless an adapter is enabled.
- Claims without source mappings must remain marked as inferred or unsupported.

## Engineering Validation

No engineering validation has run yet.

## Next Actions

See `next-actions.md`.

## Sources

No sources registered yet.
"""


class ResearchStore:
    def __init__(self, root: str | Path = "research"):
        self.root = Path(root).expanduser()

    def create_task(self, question: str, decision: dict[str, Any]) -> ResearchTask:
        self.root.mkdir(parents=True, exist_ok=True)
        task_id = self._task_id(question)
        task_dir = self.root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "notes").mkdir(exist_ok=True)

        task = ResearchTask(task_id=task_id, question=question, task_dir=task_dir, decision=decision)
        self.write_task_files(task)
        return task

    def write_task_files(self, task: ResearchTask) -> None:
        self.write_text(task, "question.md", f"# Research Question\n\n{task.question}\n")
        self.write_json(task, "router-decision.json", task.decision)
        self.write_json(task, "sources.json", [])
        self.write_json(task, "claims.json", [])
        self.write_json(task, "worker-results.json", [])
        self.write_text(task, "plan.md", self._plan(task))
        self.write_text(task, "verification.md", self._verification(task))
        self.write_text(task, "final-report.md", FINAL_REPORT_TEMPLATE)
        self.write_text(task, "next-actions.md", self._next_actions(task))

    def write_worker_result(self, task: ResearchTask, result: WorkerResult) -> None:
        note_name = {
            "claude-research": "claude-research.md",
            "codex-engineering": "codex-engineering.md",
            "kiro-spec": "kiro-spec.md",
            "verifier": "verifier.md",
            "council": "council.md",
        }.get(result.worker, f"{result.worker}.md")
        result.notes_file = f"notes/{note_name}"
        self.write_text(task, result.notes_file, result.notes)
        self.append_worker_result(task, result)

    def append_worker_result(self, task: ResearchTask, result: WorkerResult) -> None:
        path = task.task_dir / "worker-results.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        existing.append(result.to_record())
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_json(self, task: ResearchTask, relative_path: str, data: Any) -> None:
        path = task.task_dir / relative_path
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_text(self, task: ResearchTask, relative_path: str, text: str) -> None:
        path = task.task_dir / relative_path
        path.write_text(text, encoding="utf-8")

    def _task_id(self, question: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", question).strip("-").lower()
        slug = slug[:48].strip("-") or "research"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{stamp}-{slug}"

    def _plan(self, task: ResearchTask) -> str:
        d = task.decision
        workers = [d["primary_worker"], *d.get("secondary_workers", [])]
        lines = [
            "# Research Plan",
            "",
            f"- Task type: `{d['task_type']}`",
            f"- Primary worker: `{d['primary_worker']}`",
            f"- Secondary workers: {', '.join(f'`{w}`' for w in d.get('secondary_workers', [])) or '(none)'}",
            f"- Needs web: `{d['needs_web']}`",
            f"- Needs repo execution: `{d['needs_repo_execution']}`",
            f"- Needs spec: `{d['needs_spec']}`",
            "",
            "## Dispatch Order",
            "",
        ]
        lines.extend(f"1. `{w}`" for w in workers)
        return "\n".join(lines) + "\n"

    def _verification(self, task: ResearchTask) -> str:
        return (
            "# Verification\n\n"
            "- [ ] Major claims are mapped to `sources.json` entries or marked as inference.\n"
            "- [ ] Source publication/access dates are recorded where available.\n"
            "- [ ] Tool capability claims prefer official documentation.\n"
            "- [ ] Open-source recommendations include repository activity and license checks.\n"
            "- [ ] Engineering recommendations include install/build/API/benchmark validation where feasible.\n\n"
            "## Current Status\n\n"
            "Verification has not passed yet because real worker execution has not run.\n"
        )

    def _next_actions(self, task: ResearchTask) -> str:
        return (
            "# Next Actions\n\n"
            "1. Enable or confirm the worker adapters needed for this task.\n"
            "2. Run the selected workers and save their normalized notes in `notes/`.\n"
            "3. Populate `sources.json` and `claims.json`.\n"
            "4. Run the verification gate.\n"
            "5. Replace `final-report.md` with the sourced final synthesis.\n"
        )
