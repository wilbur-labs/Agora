"""End-to-end scenario tests — validates the full pipeline against real LLM.

Usage:
    python -m tests.test_scenarios              # run all
    python -m tests.test_scenarios 1            # run scenario 1 only
    python -m tests.test_scenarios 1 2 3        # run scenarios 1, 2, 3

Each scenario prints a PASS/FAIL summary. Requires AZURE_OPENAI_API_KEY in .env.
"""
from __future__ import annotations

import asyncio
import sys
import os

# Ensure backend is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agora.api._state import get_council, reset_council
from agora.config.settings import reset_config
from agora.models.registry import reset_registry
from agora.skills.store import Skill

B, D, R = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"


def _fresh_council():
    reset_config()
    reset_registry()
    reset_council()
    return get_council()


def _report(name: str, checks: list[tuple[str, bool]]):
    print(f"\n{B}━━━ {name} ━━━{R}")
    all_pass = True
    for label, ok in checks:
        icon = f"{GREEN}✓{R}" if ok else f"{RED}✗{R}"
        print(f"  {icon} {label}")
        if not ok:
            all_pass = False
    status = f"{GREEN}PASS{R}" if all_pass else f"{RED}FAIL{R}"
    print(f"  → {B}{status}{R}\n")
    return all_pass


async def scenario_1():
    """复杂技术决策 + 执行：route → discuss → execute"""
    council = _fresh_council()

    # Step 1: Route
    agents_seen: set[str] = set()
    route_text = ""
    async for name, role, chunk in council.route("我的 Go 项目需要加缓存层，QPS 大概 5000，数据量 2GB"):
        agents_seen.add(name)
        route_text += chunk

    route_ok = council.last_route in ("DISCUSS", "EXECUTE", "QUICK")
    moderator_ok = "moderator" in agents_seen

    # Step 2: Discuss (if routed to discuss, or force it)
    discuss_agents: list[str] = []
    async for name, role, chunk in council.stream_discuss():
        if name not in discuss_agents:
            discuss_agents.append(name)

    multi_agent_ok = len(discuss_agents) >= 3
    synthesizer_ok = "synthesizer" in discuss_agents

    # Step 3: Execute (tool-calling)
    exec_events: list[str] = []
    exec_text = ""
    async for name, role, chunk in council.stream_execute():
        if chunk.startswith("[tool_call]"):
            exec_events.append("tool_call")
        elif chunk.startswith("[tool_result]"):
            exec_events.append("tool_result")
        elif chunk.startswith("[done]"):
            exec_events.append("done")
        elif not chunk.startswith("["):
            exec_text += chunk

    # Executor should either call tools OR produce text output
    executor_responded = len(exec_events) > 0 or len(exec_text.strip()) > 0

    return _report("场景 1: 复杂技术决策 + 执行", [
        ("Moderator 发言", moderator_ok),
        (f"路由判断有效 (got: {council.last_route})", route_ok),
        (f"多 agent 讨论 (agents: {discuss_agents})", multi_agent_ok),
        ("Synthesizer 收敛", synthesizer_ok),
        ("Executor 响应", executor_responded),
    ])


async def scenario_2():
    """直接执行：跳过讨论，executor tool-calling"""
    council = _fresh_council()

    # Simulate /exec — add user message and go straight to execute
    council.context.add_user("在 /tmp/agora_test/ 创建一个 hello.txt，内容写 Hello from Agora")

    exec_events: list[str] = []
    exec_text = ""
    async for name, role, chunk in council.stream_execute():
        if chunk.startswith("[tool_call]"):
            exec_events.append(chunk)
        elif chunk.startswith("[tool_result]"):
            exec_events.append("tool_result")
        elif not chunk.startswith("["):
            exec_text += chunk

    has_write = any("write_file" in e for e in exec_events)

    # Verify file was actually created
    import os
    file_exists = os.path.exists("/tmp/agora_test/hello.txt")
    file_content_ok = False
    if file_exists:
        with open("/tmp/agora_test/hello.txt") as f:
            file_content_ok = "Hello" in f.read()
        # Cleanup
        os.remove("/tmp/agora_test/hello.txt")
        os.rmdir("/tmp/agora_test/")

    return _report("场景 2: 直接执行", [
        (f"Tool call 触发 (calls: {len(exec_events)})", len(exec_events) > 0),
        ("write_file 被调用", has_write),
        ("文件实际创建", file_exists),
        ("文件内容正确", file_content_ok),
    ])


async def scenario_3():
    """快速问答：单 agent 直接回复"""
    council = _fresh_council()
    council.context.add_user("Python 的 match-case 语法最低要求什么版本？")

    agents_seen: list[str] = []
    text = ""
    async for name, role, chunk in council.stream_quick():
        if name not in agents_seen:
            agents_seen.append(name)
        text += chunk

    single_agent = len(agents_seen) == 1
    has_answer = "3.10" in text

    return _report("场景 3: 快速问答", [
        (f"单 agent 回复 (agents: {agents_seen})", single_agent),
        ("回答包含 3.10", has_answer),
    ])


async def scenario_4():
    """代码审查：多 agent 讨论"""
    council = _fresh_council()

    # Route
    async for name, role, chunk in council.route(
        "帮我 review 这段代码：\n"
        "```python\n"
        "import pickle\n"
        "def load_user(data):\n"
        "    return pickle.loads(data)  # from user input\n"
        "```"
    ):
        pass

    # Discuss
    discuss_agents: list[str] = []
    discuss_text = ""
    async for name, role, chunk in council.stream_discuss():
        if name not in discuss_agents:
            discuss_agents.append(name)
        discuss_text += chunk

    multi_agent = len(discuss_agents) >= 3
    # Should catch the pickle security issue
    security_mentioned = any(w in discuss_text.lower() for w in ["pickle", "security", "安全", "漏洞", "risk", "危险", "deserializ"])

    return _report("场景 4: 代码审查", [
        (f"多 agent 讨论 (agents: {discuss_agents})", multi_agent),
        ("识别到安全问题", security_mentioned),
    ])


async def scenario_5():
    """学习效果：skill 注入到 prompt"""
    council = _fresh_council()

    # Manually inject a learned skill
    test_skill = Skill(
        name="go_cache_layer_setup",
        trigger="缓存 cache 缓存层",
        steps=["使用 ristretto 做 L1 本地缓存", "使用 Redis 做 L2", "用 pub/sub 做失效通知"],
        lessons=["蒸馏前必须定义评估指标"],
    )
    council.skill_store._skills = [test_skill]
    council.skill_store.enabled = True

    # Check skill injection
    council._last_user_input = "另一个项目也要加缓存"
    _, skills_text = council._get_injections()

    skill_injected = "go_cache_layer_setup" in skills_text
    has_steps = "ristretto" in skills_text

    # Verify it would appear in agent prompt
    agent = council.agents[0]
    prompt = agent.system_prompt(skills=skills_text)
    in_prompt = "go_cache_layer_setup" in prompt

    return _report("场景 5: 学习效果", [
        ("Skill 匹配成功", skill_injected),
        ("Skill 步骤注入", has_steps),
        ("Skill 出现在 agent prompt 中", in_prompt),
    ])


SCENARIOS = {
    "1": ("复杂技术决策 + 执行", scenario_1),
    "2": ("直接执行", scenario_2),
    "3": ("快速问答", scenario_3),
    "4": ("代码审查", scenario_4),
    "5": ("学习效果", scenario_5),
}


async def main():
    args = sys.argv[1:] or list(SCENARIOS.keys())
    results: list[tuple[str, bool]] = []

    for key in args:
        if key not in SCENARIOS:
            print(f"{RED}Unknown scenario: {key}{R}")
            continue
        label, fn = SCENARIOS[key]
        print(f"\n{B}{YELLOW}▶ Running scenario {key}: {label}{R}")
        try:
            passed = await fn()
            results.append((f"场景 {key}: {label}", passed))
        except Exception as e:
            print(f"  {RED}✗ Exception: {e}{R}")
            results.append((f"场景 {key}: {label}", False))

    # Summary
    print(f"\n{B}{'='*50}{R}")
    print(f"{B}Summary{R}")
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    for label, ok in results:
        icon = f"{GREEN}PASS{R}" if ok else f"{RED}FAIL{R}"
        print(f"  {icon}  {label}")
    print(f"\n  {passed}/{total} passed")
    print(f"{B}{'='*50}{R}")


if __name__ == "__main__":
    asyncio.run(main())
