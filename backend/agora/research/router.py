"""Deterministic research task classifier and router policy."""
from __future__ import annotations

from typing import Any


ROUTE_TABLE = {
    "paper_survey": ("claude-research", ["verifier"]),
    "technical_report": ("claude-research", ["verifier"]),
    "official_docs_comparison": ("claude-research", ["codex-engineering"]),
    "library_evaluation": ("codex-engineering", ["claude-research", "verifier"]),
    "repo_deep_dive": ("codex-engineering", ["verifier"]),
    "architecture_decision": ("council", ["claude-research", "codex-engineering", "verifier"]),
    "poc_or_benchmark": ("codex-engineering", ["verifier"]),
    "migration_plan": ("codex-engineering", ["kiro-spec", "claude-research"]),
    "spec_generation": ("kiro-spec", ["claude-research"]),
    "code_implementation": ("codex-engineering", ["kiro-spec"]),
}


def classify(question: str) -> dict[str, Any]:
    text = question.lower()
    task_type = "technical_report"
    confidence = 0.62
    reason = "Defaulted to a broad technical report because no narrower rule matched."

    if any(k in text for k in ("paper", "论文", "survey", "综述", "arxiv")):
        task_type, confidence = "paper_survey", 0.82
        reason = "The request asks for paper-oriented survey work."
    elif any(k in text for k in ("official docs", "官方文档", "docs comparison", "文档对比")):
        task_type, confidence = "official_docs_comparison", 0.82
        reason = "The request emphasizes official documentation comparison."
    elif any(k in text for k in ("library", "库", "开源", "github", "选型", "compare", "比较", "reranker")):
        task_type, confidence = "library_evaluation", 0.86
        reason = "The request asks for open-source/library evaluation and likely needs engineering validation."
    elif any(k in text for k in ("repo", "repository", "仓库", "deep dive", "源码")):
        task_type, confidence = "repo_deep_dive", 0.84
        reason = "The request focuses on a repository or source-code deep dive."
    elif any(k in text for k in ("benchmark", "poc", "proof of concept", "基准", "验证")):
        task_type, confidence = "poc_or_benchmark", 0.84
        reason = "The request needs empirical validation, PoC, or benchmark work."
    elif any(k in text for k in ("architecture", "架构", "adr", "decision", "决策", "evidence store", "sqlite", "文件还是", "应该用")):
        task_type, confidence = "architecture_decision", 0.80
        reason = "The request asks for architecture decision support."
    elif any(k in text for k in ("migration", "迁移", "upgrade", "升级")):
        task_type, confidence = "migration_plan", 0.78
        reason = "The request asks for a migration or upgrade plan."
    elif any(k in text for k in ("spec", "requirements", "design", "tasks", "需求", "设计", "任务拆解")):
        task_type, confidence = "spec_generation", 0.80
        reason = "The request asks for structured requirements/design/tasks."
    elif any(k in text for k in ("implement", "code", "修复", "实现", "写代码")):
        task_type, confidence = "code_implementation", 0.78
        reason = "The request asks for code implementation."

    primary, secondary = ROUTE_TABLE[task_type]
    needs_repo_execution = task_type in {
        "library_evaluation",
        "repo_deep_dive",
        "poc_or_benchmark",
        "migration_plan",
        "code_implementation",
    }
    needs_spec = task_type in {
        "library_evaluation",
        "migration_plan",
        "spec_generation",
        "code_implementation",
        "architecture_decision",
    }
    output_artifacts = _output_artifacts(task_type, needs_repo_execution, needs_spec)

    return {
        "task_type": task_type,
        "primary_worker": primary,
        "secondary_workers": secondary,
        "needs_web": task_type != "code_implementation",
        "needs_papers": task_type == "paper_survey",
        "needs_repo_execution": needs_repo_execution,
        "needs_spec": needs_spec,
        "output_artifacts": output_artifacts,
        "confidence": confidence,
        "reason": reason,
    }


def _output_artifacts(task_type: str, needs_repo_execution: bool, needs_spec: bool) -> list[str]:
    artifacts = ["comparison_report" if task_type == "library_evaluation" else "research_report"]
    if needs_repo_execution:
        artifacts.append("repo_eval")
    if needs_spec:
        artifacts.append("implementation_tasks")
    return artifacts
