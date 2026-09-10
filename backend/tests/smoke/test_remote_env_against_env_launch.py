"""Cross-unit smoke: U2 (remote-env) against U1 (env-launch)'s FROZEN contract.

Per .kiro/steering/cross-unit-smoke.md: exercise U2's real generated code against U1's
REAL contract — never mock the upstream unit. Here that means: a RemoteEnvironment's
launch() must return something that genuinely satisfies U1's ProcessHandle Protocol,
and be drivable by the SAME lifecycle the dispatcher (U1's consumer) uses —
attach pid, read both streams, wait, terminate→kill. The SSH transport underneath is a
fake (there is no real host in unit CI), but U1's ProcessHandle/Environment types and
the dispatcher's _stop escalation are the real, un-mocked things under test.

Litmus (cross-unit-smoke.md): if U1's ProcessHandle contract were broken, this must
fail. Run with --noconftest to bypass the unrelated repo manifest-hash issue.
"""
from __future__ import annotations

import asyncio

import pytest

# U1's REAL contract types — not mocked.
from agora.environments import Environment, ProcessHandle, GRACE_SECONDS
from agora.environments_remote import RemoteEnvironment, RemoteProcessHandle

# NOTE: importing agora.execution.dispatcher (to drive the real _stop) transitively
# imports agora.orchestration, whose __init__ runs an activation-manifest hash check
# that fails in this checkout — a PRE-EXISTING repo issue unrelated to U2 (see
# aidlc-docs). So the dispatcher-driven smoke below is skipped with that reason; the
# ProcessHandle-contract smoke (which needs no dispatcher) runs and is the core check.
try:
    from agora.execution.dispatcher import ExecutionDispatcher
    _DISPATCHER_IMPORTABLE = True
    _DISPATCHER_SKIP = ""
except ValueError as exc:  # the specific pre-existing manifest-hash failure
    if "activation manifest hash" not in str(exc):
        raise  # a DIFFERENT ValueError is a real regression — do not hide it
    ExecutionDispatcher = None  # type: ignore
    _DISPATCHER_IMPORTABLE = False
    _DISPATCHER_SKIP = f"dispatcher import blocked by pre-existing repo issue: {exc}"


def _run(coro):
    return asyncio.run(coro)


class _FakeReader:
    def __init__(self, data: bytes):
        self._data = data
    async def read(self, n: int = -1) -> bytes:
        d, self._data = self._data, b""
        return d


class _FakeProc:
    """A live fake: returncode is None until terminate()/kill() is called, so
    terminate/kill are NOT no-ops and the escalation is actually exercised (a
    returncode=0 fake would let _stop and terminate short-circuit — a vacuous test)."""
    def __init__(self, out=b"hi", err=b""):
        self._rc = None
        self.stdout = _FakeReader(out)
        self.stderr = _FakeReader(err)
        self.terminated = False
        self.killed = False
    @property
    def returncode(self):
        return self._rc
    async def wait(self):
        return self._rc
    def terminate(self):
        self.terminated = True
        self._rc = -15  # SIGTERM: the process now exits
    def kill(self):
        self.killed = True
        self._rc = -9


class _FakeConn:
    def __init__(self):
        self.proc: _FakeProc | None = None
    async def create_process(self, cmd, *, encoding=None, env=None):
        self.proc = _FakeProc()
        return self.proc
    def close(self):
        pass
    async def wait_closed(self):
        pass


def _remote_env(monkeypatch):
    monkeypatch.setenv("AGORA_SSH_KEY_x", "pw")
    env = RemoteEnvironment(host="h", user="u", secret_ref="AGORA_SSH_KEY_x")
    conn = _FakeConn()
    async def _fake_ensure():
        return conn
    monkeypatch.setattr(env, "_ensure_conn", _fake_ensure)
    return env, conn


def test_remote_env_satisfies_u1_environment_launch_contract(monkeypatch):
    # U2 is an AsyncExecutionEnvironment, and its launch() is Environment.launch — the
    # shared async capability. The handle it returns must satisfy U1's ProcessHandle.
    env, conn = _remote_env(monkeypatch)

    async def scenario():
        handle = await env.launch(["agent"], env={"AGORA_TASK_ID": "t"})
        # U1's real Protocol — structural check against the un-mocked ProcessHandle.
        assert isinstance(handle, ProcessHandle)
        assert isinstance(handle, RemoteProcessHandle)
        # Every member U1's dispatcher reads must be present and usable:
        assert handle.pid is None                    # remote pid contract (U1 Q3=B)
        assert handle.returncode is None             # live: not exited yet
        out = await handle.stdout.read()             # _ByteReader.read -> bytes
        err = await handle.stderr.read()
        await handle.terminate()                     # async; must actually signal
        assert conn.proc.terminated                  # the terminate reached the process
        code = await handle.wait(timeout=5)
        await handle.kill()                          # idempotent after exit
        return out, err, code

    out, err, code = _run(scenario())
    assert out == b"hi" and err == b"" and code == -15  # exited via terminate


@pytest.mark.skipif(not _DISPATCHER_IMPORTABLE, reason=_DISPATCHER_SKIP)
def test_dispatcher_stop_escalation_drives_a_remote_handle(monkeypatch):
    # The dispatcher's real _stop (U1) must be able to drive a RemoteProcessHandle
    # through terminate→grace→kill without knowing it is remote. Not mocked: the real
    # _stop, the real ProcessHandle branch selection, GRACE_SECONDS.
    env, conn = _remote_env(monkeypatch)

    async def scenario():
        handle = await env.launch(["agent"])
        # _stop is a staticmethod; it must accept a ProcessHandle and use the async
        # terminate/kill branch (NOT the raw-subprocess branch) — proving U1's
        # handle-vs-Process dispatch works for U2's handle.
        await ExecutionDispatcher._stop(handle)
        assert conn.proc.terminated  # _stop actually drove the handle (not a no-op)

    _run(scenario())  # no TypeError / no AttributeError == the contract holds


def test_remote_env_is_usable_where_u1_environment_launch_is_expected(monkeypatch):
    # Duck-typed: RemoteEnvironment exposes the async launch Environment declares.
    env, _conn = _remote_env(monkeypatch)
    assert callable(getattr(env, "launch", None))
    assert asyncio.iscoroutinefunction(env.launch)
