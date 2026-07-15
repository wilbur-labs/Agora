"""Supervise one version-matched Codex app-server turn over stdio JSONL."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .codex_broker import CodexApprovalBroker
from .models import BridgeVendor


class CodexAppServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexAppServerResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    error: str | None = None
    timed_out: bool = False


class CodexAppServerRunner:
    def __init__(self, broker: CodexApprovalBroker):
        self.broker = broker
        self._write_lock = asyncio.Lock()

    async def run(
        self, *, command: tuple[str, ...], prompt: str, cwd: Path, timeout_seconds: int,
        env: dict[str, str], on_process: Callable[[asyncio.subprocess.Process], Awaitable[None]],
    ) -> CodexAppServerResult:
        proc = await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        stderr_task = asyncio.create_task(self._read_all(proc.stderr))
        output = bytearray()
        failure: Exception | None = None
        cancelled: asyncio.CancelledError | None = None
        timed_out = False
        try:
            await on_process(proc)
            await asyncio.wait_for(self._drive(proc, prompt, cwd, output), timeout=timeout_seconds)
            return_code = 0
        except asyncio.CancelledError as exc:
            return_code = None
            cancelled = exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            return_code = None
            failure = exc
            timed_out = True
        except Exception as exc:
            return_code = None
            failure = exc
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (TimeoutError, asyncio.TimeoutError):
                    proc.kill()
                    await proc.wait()
        stderr = await stderr_task
        if cancelled is not None:
            raise cancelled
        error = f"{type(failure).__name__}: {failure}" if failure else None
        return CodexAppServerResult(
            exit_code=return_code, stdout=bytes(output), stderr=stderr,
            error=error, timed_out=timed_out,
        )

    async def _drive(
        self, proc: asyncio.subprocess.Process, prompt: str, cwd: Path, output: bytearray,
    ) -> None:
        await self._send(proc, {"method": "initialize", "id": 1, "params": {
            "clientInfo": {"name": "agora", "title": "Agora", "version": "0.1.0"},
        }})
        initialize = await self._read_response(proc, 1, output)
        if "error" in initialize:
            raise CodexAppServerError(f"initialize failed: {initialize['error'].get('message', 'unknown')}")
        await self._send(proc, {"method": "initialized", "params": {}})
        await self._send(proc, {"method": "thread/start", "id": 2, "params": {
            "cwd": str(cwd), "sandbox": "workspace-write", "approvalPolicy": "on-request",
            "approvalsReviewer": "user", "ephemeral": True,
        }})
        thread_response = await self._read_response(proc, 2, output)
        try:
            thread_id = thread_response["result"]["thread"]["id"]
        except (KeyError, TypeError):
            raise CodexAppServerError("thread/start returned an invalid response") from None
        await self._send(proc, {"method": "turn/start", "id": 3, "params": {
            "threadId": thread_id, "input": [{"type": "text", "text": prompt}],
        }})
        turn_response = await self._read_response(proc, 3, output)
        if "error" in turn_response:
            raise CodexAppServerError(f"turn/start failed: {turn_response['error'].get('message', 'unknown')}")
        try:
            turn_id = turn_response["result"]["turn"]["id"]
        except (KeyError, TypeError):
            raise CodexAppServerError("turn/start returned an invalid response") from None

        while True:
            failure = self.broker.store.delivery_failure_for_run(self.broker.run_id, BridgeVendor.CODEX)
            if failure:
                raise CodexAppServerError(f"approval delivery failed: {failure}")
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.25)
            except (TimeoutError, asyncio.TimeoutError):
                await self.broker.deliver_ready(lambda message: self._send(proc, message))
                continue
            if not line:
                raise CodexAppServerError("app-server exited before turn completion")
            self._record(output, line)
            message = self._decode(line)
            self.broker.capture(message)
            await self.broker.deliver_ready(lambda response: self._send(proc, response))
            if message.get("method") == "turn/completed":
                params = message.get("params", {})
                completed_turn = params.get("turn", {})
                if params.get("threadId") != thread_id or completed_turn.get("id") != turn_id:
                    raise CodexAppServerError("turn/completed identity does not match the active turn")
                status = completed_turn.get("status")
                if status not in {"completed", "succeeded"}:
                    raise CodexAppServerError(f"Codex turn completed with status {status}")
                return

    async def _read_response(self, proc, request_id: int, output: bytearray) -> dict:
        while True:
            failure = self.broker.store.delivery_failure_for_run(self.broker.run_id, BridgeVendor.CODEX)
            if failure:
                raise CodexAppServerError(f"approval delivery failed: {failure}")
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.25)
            except (TimeoutError, asyncio.TimeoutError):
                await self.broker.deliver_ready(lambda message: self._send(proc, message))
                continue
            if not line:
                raise CodexAppServerError("app-server exited during handshake")
            self._record(output, line)
            message = self._decode(line)
            self.broker.capture(message)
            await self.broker.deliver_ready(lambda response: self._send(proc, response))
            if message.get("id") == request_id:
                return message

    async def _send(self, proc: asyncio.subprocess.Process, message: dict) -> None:
        if proc.stdin is None:
            raise CodexAppServerError("app-server stdin is unavailable")
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            proc.stdin.write(encoded)
            await proc.stdin.drain()

    @staticmethod
    def _decode(line: bytes) -> dict:
        try:
            message = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexAppServerError("app-server emitted invalid JSON") from exc
        if not isinstance(message, dict):
            raise CodexAppServerError("app-server message must be an object")
        return message

    @staticmethod
    async def _read_all(stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        tail = bytearray()
        while chunk := await stream.read(8192):
            CodexAppServerRunner._record(tail, chunk)
        return bytes(tail)

    @staticmethod
    def _record(buffer: bytearray, chunk: bytes, limit: int = 128 * 1024) -> None:
        buffer.extend(chunk)
        if len(buffer) > limit:
            del buffer[:-limit]
