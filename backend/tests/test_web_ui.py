"""Tests for Web UI API endpoints — sessions, agents CRUD, extras, chat flow."""
import json
import pytest
from fastapi.testclient import TestClient

from agora.api.app import app

client = TestClient(app)


# === Health & Frontend ===

class TestFrontend:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_index_page(self):
        r = client.get("/")
        assert r.status_code == 200

    def test_agents_page(self):
        r = client.get("/agents")
        assert r.status_code == 200

    def test_skills_page(self):
        r = client.get("/skills")
        assert r.status_code == 200

    def test_settings_page(self):
        r = client.get("/settings")
        assert r.status_code == 200

    def test_chat_page(self):
        r = client.get("/chat")
        assert r.status_code == 200

    def test_shared_page(self):
        r = client.get("/shared")
        assert r.status_code == 200

    def test_nonexistent_static(self):
        # Should fallback to index.html
        r = client.get("/nonexistent-page")
        assert r.status_code == 200


# === Sessions API ===

class TestSessionsAPI:
    def test_list_empty(self):
        r = client.get("/api/sessions")
        assert r.status_code == 200
        assert "sessions" in r.json()

    def test_create_and_get(self):
        r = client.post("/api/sessions", json={"title": "Test", "messages": [{"type": "user", "content": "hi"}]})
        assert r.status_code == 200
        sid = r.json()["id"]
        assert sid

        r = client.get(f"/api/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["title"] == "Test"
        assert len(r.json()["messages"]) == 1

        # Cleanup
        client.delete(f"/api/sessions/{sid}")

    def test_update_session(self):
        r = client.post("/api/sessions", json={"title": "Old", "messages": []})
        sid = r.json()["id"]

        r = client.put(f"/api/sessions/{sid}", json={"messages": [{"type": "user", "content": "updated"}], "title": "New"})
        assert r.status_code == 200

        r = client.get(f"/api/sessions/{sid}")
        assert r.json()["title"] == "New"
        assert r.json()["messages"][0]["content"] == "updated"

        client.delete(f"/api/sessions/{sid}")

    def test_delete_session(self):
        r = client.post("/api/sessions", json={"title": "Del", "messages": []})
        sid = r.json()["id"]

        r = client.delete(f"/api/sessions/{sid}")
        assert r.status_code == 200

        r = client.get(f"/api/sessions/{sid}")
        assert r.status_code == 404

    def test_get_nonexistent(self):
        r = client.get("/api/sessions/nonexistent")
        assert r.status_code == 404

    def test_update_nonexistent(self):
        r = client.put("/api/sessions/nonexistent", json={"messages": []})
        assert r.status_code == 404


# === Share API ===

class TestShareAPI:
    def test_create_and_get_share(self):
        msgs = [{"type": "user", "content": "hello"}, {"type": "agent", "agent": "scout", "content": "hi"}]
        r = client.post("/api/chat/share", json={"messages": msgs})
        assert r.status_code == 200
        share_id = r.json()["id"]
        assert share_id

        r = client.get(f"/api/shared/{share_id}")
        assert r.status_code == 200
        assert len(r.json()["messages"]) == 2

    def test_get_nonexistent_share(self):
        r = client.get("/api/shared/nonexistent")
        assert r.status_code == 404


# === Agents API ===

class TestAgentsAPI:
    def test_list_agents(self):
        r = client.get("/api/agents")
        assert r.status_code == 200
        agents = r.json()["agents"]
        assert len(agents) >= 1
        assert all("name" in a for a in agents)

    def test_available_agents(self):
        r = client.get("/api/agents/available")
        assert r.status_code == 200
        agents = r.json()["agents"]
        # Should not include utility agents
        names = [a["name"] for a in agents]
        assert "moderator" not in names
        assert "synthesizer" not in names

    def test_get_agent_detail(self):
        r = client.get("/api/agents/scout")
        assert r.status_code == 200
        d = r.json()
        assert d["name"] == "scout"
        assert d["role"] == "Researcher"
        assert len(d["perspective"]) > 0
        assert "active" in d

    def test_get_nonexistent_agent(self):
        r = client.get("/api/agents/nonexistent_agent_xyz")
        assert r.status_code == 404

    def test_update_agent(self):
        # Get original
        r = client.get("/api/agents/scout")
        original_role = r.json()["role"]

        # Update
        r = client.put("/api/agents/scout", json={"role": "Test Role"})
        assert r.status_code == 200

        # Verify
        r = client.get("/api/agents/scout")
        assert r.json()["role"] == "Test Role"

        # Restore
        client.put("/api/agents/scout", json={"role": original_role})

    def test_create_and_delete_agent(self):
        r = client.post("/api/agents", json={
            "name": "test_agent_xyz",
            "role": "Test Role",
            "perspective": "You are a test agent.",
        })
        assert r.status_code == 200

        r = client.get("/api/agents/test_agent_xyz")
        assert r.status_code == 200
        assert r.json()["role"] == "Test Role"

        r = client.delete("/api/agents/test_agent_xyz")
        assert r.status_code == 200

        r = client.get("/api/agents/test_agent_xyz")
        assert r.status_code == 404

    def test_create_duplicate(self):
        r = client.post("/api/agents", json={"name": "scout", "role": "X", "perspective": "X"})
        assert r.status_code == 409

    def test_delete_utility_agent(self):
        r = client.delete("/api/agents/moderator")
        assert r.status_code == 400

    def test_delete_nonexistent(self):
        r = client.delete("/api/agents/nonexistent_xyz")
        assert r.status_code == 404

    def test_set_active_agents(self):
        r = client.post("/api/agents/active", json={"agents": ["scout", "critic"]})
        assert r.status_code == 200
        names = [a["name"] for a in r.json()["agents"]]
        assert "scout" in names
        assert "critic" in names

        # Restore
        client.post("/api/agents/active", json={"agents": ["scout", "architect", "critic"]})


# === Extras API (Skills, Memory, Profile) ===

class TestExtrasAPI:
    def test_list_skills(self):
        r = client.get("/api/skills")
        assert r.status_code == 200
        assert "skills" in r.json()

    def test_get_memory(self):
        r = client.get("/api/memory")
        assert r.status_code == 200
        assert "memory" in r.json()

    def test_get_profile(self):
        r = client.get("/api/profile")
        assert r.status_code == 200
        assert "profile" in r.json()

    def test_update_profile(self):
        r = client.put("/api/profile", json={"profile": {"language": "en", "test_key": "test_val"}})
        assert r.status_code == 200

        r = client.get("/api/profile")
        p = r.json()["profile"]
        assert p.get("test_key") == "test_val"


# === Chat API ===

class TestChatAPI:
    def test_reset(self):
        r = client.post("/api/chat/reset")
        assert r.status_code == 200

    def test_feedback(self):
        r = client.post("/api/chat/feedback", json={"message_id": "msg-1", "rating": "up"})
        assert r.status_code == 200
        assert r.json()["rating"] == "up"

    def test_feedback_down(self):
        r = client.post("/api/chat/feedback", json={"message_id": "msg-2", "rating": "down"})
        assert r.status_code == 200
        assert r.json()["rating"] == "down"


# === OpenAI Provider ===

class TestOpenAIProvider:
    def test_stream_method_exists(self):
        from agora.models.openai_provider import OpenAIProvider
        p = OpenAIProvider(api_key="test", base_url="http://localhost", model="test")
        assert hasattr(p, "stream")
        assert hasattr(p, "generate_with_tools")

    def test_azure_inherits_stream(self):
        from agora.models.openai_provider import AzureOpenAIProvider
        p = AzureOpenAIProvider(api_key="test", base_url="http://localhost", deployment="test")
        assert hasattr(p, "stream")

    def test_headers(self):
        from agora.models.openai_provider import OpenAIProvider, AzureOpenAIProvider
        p1 = OpenAIProvider(api_key="sk-test", base_url="http://localhost", model="m")
        assert "Authorization" in p1._headers()

        p2 = AzureOpenAIProvider(api_key="ak-test", base_url="http://localhost", deployment="d")
        assert "api-key" in p2._headers()

    def test_url_format(self):
        from agora.models.openai_provider import OpenAIProvider, AzureOpenAIProvider
        p1 = OpenAIProvider(api_key="x", base_url="http://api.example.com/v1", model="m")
        assert "chat/completions" in p1._url()

        p2 = AzureOpenAIProvider(api_key="x", base_url="http://azure.example.com", deployment="gpt4", api_version="2024-01-01")
        assert "gpt4" in p2._url()
        assert "2024-01-01" in p2._url()

    def test_body_format(self):
        from agora.models.openai_provider import OpenAIProvider, AzureOpenAIProvider
        p1 = OpenAIProvider(api_key="x", base_url="http://localhost", model="gpt-4")
        body = p1._body([{"role": "user", "content": "hi"}], tools=[])
        assert body["model"] == "gpt-4"
        assert len(body["messages"]) == 1

        p2 = AzureOpenAIProvider(api_key="x", base_url="http://localhost", deployment="d")
        body = p2._body([{"role": "user", "content": "hi"}], tools=[])
        assert "model" not in body  # Azure doesn't send model in body

    def test_parse_response(self):
        from agora.models.openai_provider import OpenAIProvider
        data = {
            "choices": [{
                "message": {"content": "Hello!", "tool_calls": None},
                "finish_reason": "stop",
            }]
        }
        result = OpenAIProvider._parse_response(data)
        assert result.content == "Hello!"
        assert result.tool_calls == []

    def test_parse_response_with_tools(self):
        from agora.models.openai_provider import OpenAIProvider
        data = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "write_file", "arguments": '{"path": "/tmp/test.txt", "content": "hi"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }]
        }
        result = OpenAIProvider._parse_response(data)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function_name == "write_file"
        assert result.tool_calls[0].arguments["path"] == "/tmp/test.txt"


# === Sessions DB ===

class TestSessionsDB:
    def test_create_list_delete(self):
        from agora.api.sessions_db import create_session, list_sessions, get_session, delete_session
        sid = create_session("DB Test")
        sessions = list_sessions()
        assert any(s["id"] == sid for s in sessions)

        s = get_session(sid)
        assert s is not None
        assert s["title"] == "DB Test"

        delete_session(sid)
        assert get_session(sid) is None

    def test_update_messages(self):
        from agora.api.sessions_db import create_session, update_session_messages, get_session, delete_session
        sid = create_session("Msg Test")
        update_session_messages(sid, [{"type": "user", "content": "hello"}])
        s = get_session(sid)
        assert len(s["messages"]) == 1
        delete_session(sid)

    def test_update_title(self):
        from agora.api.sessions_db import create_session, update_session_title, get_session, delete_session
        sid = create_session("Old Title")
        update_session_title(sid, "New Title")
        assert get_session(sid)["title"] == "New Title"
        delete_session(sid)

    def test_shares(self):
        from agora.api.sessions_db import create_share, get_share
        share_id = create_share([{"type": "user", "content": "shared"}])
        s = get_share(share_id)
        assert s is not None
        assert len(s["messages"]) == 1
        assert get_share("nonexistent") is None
