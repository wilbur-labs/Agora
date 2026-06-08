from __future__ import annotations

import asyncio

from agora.research import ResearchOrchestrator
from agora.research.router import classify, should_auto_dispatch


def test_classify_library_evaluation_routes_to_codex():
    decision = classify("比较生产级 RAG 系统可用的开源 reranker，并推荐一个。")

    assert decision["task_type"] == "library_evaluation"
    assert decision["primary_worker"] == "codex-engineering"
    assert "claude-research" in decision["secondary_workers"]
    assert "verifier" in decision["secondary_workers"]
    assert decision["needs_repo_execution"] is True
    assert decision["needs_spec"] is True


def test_should_auto_dispatch_research_like_prompt():
    assert should_auto_dispatch("比较生产级 RAG 系统可用的开源 reranker，并推荐一个。") is True


def test_should_not_auto_dispatch_simple_chat_prompt():
    assert should_auto_dispatch("你好，今天怎么样？") is False


def test_research_orchestrator_writes_artifacts(tmp_path):
    cfg = {
        "research": {
            "artifact_dir": str(tmp_path),
            "workspaces": {
                "codex": "/work01/s141026/codex-workspace",
                "claude": "/work01/s141026/claude-workspace",
                "kiro": "/work01/s141026/kiro-workspace",
            },
        }
    }

    task = asyncio.run(ResearchOrchestrator(cfg).run("Agora 的 evidence store 第一版应该用文件还是 SQLite？"))

    assert (task.task_dir / "question.md").exists()
    assert (task.task_dir / "router-decision.json").exists()
    assert (task.task_dir / "sources.json").exists()
    assert (task.task_dir / "claims.json").exists()
    assert (task.task_dir / "verification.md").exists()
    assert (task.task_dir / "final-report.md").exists()
    assert (task.task_dir / "notes" / "council.md").exists()
    assert (task.task_dir / "notes" / "claude-research.md").exists()
    assert (task.task_dir / "notes" / "codex-engineering.md").exists()
    assert (task.task_dir / "notes" / "verifier.md").exists()
