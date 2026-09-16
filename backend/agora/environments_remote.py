"""SSH-backed Environment — W3's remote implementation, kept out of the core.

Why a separate module. :mod:`agora.environments` is the abstraction (W2's seam);
this is one implementation of it that drags in ``asyncssh``, the same way each agent
bridge lives under ``attention/bridges`` rather than in the core. Import it only where
a remote environment is actually built.

What it provides:

``RemoteEnvironment``
    Implements :class:`agora.environments.AsyncExecutionEnvironment` (async run/probe)
    plus the async ``launch`` of :class:`agora.environments.Environment`. It holds ONE
    asyncssh connection for its lifetime (one instance per run), created lazily on the
    first call and reused across run/probe/launch on a single event loop — a synchronous
    ``run`` bridged through a short-lived loop cannot reuse that connection safely
    (CR-4), which is exactly why the async contract exists.

``RemoteProcessHandle``
    A remote process as a :class:`agora.environments.ProcessHandle`: bytes streams,
    channel-signal terminate/kill, a pid that is ``None`` (a remote pid is not this
    host's and is not needed to kill — that goes over the channel).

Security (never weakened from the frozen contract):
  - host keys are verified — ``strict`` uses asyncssh's native ``known_hosts`` and
    rejects an unknown/changed key. ``tofu`` (CR-6) pins the key the server presents on
    the FIRST connect into ``known_hosts_path`` (atomically), then verifies strict
    against it on every connect — so a later key change is rejected like any strict
    mismatch. The first-connect trust window is inherent to trust-on-first-use. Host-key
    checking is never disabled: no code path passes a null known-hosts value to asyncssh,
    which is what would turn verification off.
  - credentials come from an env var named by ``secret_ref`` — never inline, never in
    config or logs, and stripped from anything forwarded to the remote.
  - remote commands are argv + ``shlex.quote`` — never a shell string.
  - remote output is read as bytes and decoded UTF-8 by us, never by the remote locale.
  - ``probe`` uses SFTP stat/lstat (typed errno) so ENOENT (absent) is kept apart from
    permission/other failures (unknown) — the fsprobe rule a shell test cannot keep.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import stat
import tempfile
import weakref
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

import asyncssh

from agora.environments import (
    CONNECT_MAX_RETRIES,
    CONNECT_RETRY_BACKOFF,
    CONNECT_TIMEOUT,
    KEEPALIVE_INTERVAL,
    KEEPALIVE_MAX_MISSED,
    MAX_FS_READ_BYTES,
    CommandResult,
    PathProbe,
    ProcessHandle,
)
from agora.fsprobe import PathProbe as _PathProbe  # same type; explicit for constructors

HostKeyMode = str  # "strict" | "tofu"; validated in __init__ (raises on anything else)


# Per-known_hosts-file TOFU locks. One instance per run (see RemoteEnvironment), so two
# concurrent runs to the same host on the SAME event loop are two different instances
# pointing at the same known_hosts file — a per-instance lock would let both pin. Keying
# the lock by the CANONICAL file path (resolve() + normcase, so ./ , .. , symlinks and
# Windows case aliases collapse to one key) makes them share one lock; the
# check->fetch->write then serializes across instances on that loop.
#
# Bounds of this mechanism (honest, not hidden): an asyncio.Lock belongs to the loop it
# is first awaited on. The locks are stored under a WeakKeyDictionary keyed by the loop
# OBJECT, so a lock never leaks across loops (which would raise at await time) and entries
# vanish when their loop is gc'd. Two first-connects on DIFFERENT event loops (e.g.
# separate asyncio.run() / separate processes) are therefore NOT serialized by this
# in-memory lock; that case is out of scope for CR-6 option B and is covered instead by
# the real-host integration step. Within one loop, exactly-once pinning holds.
_PIN_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]]" = (
    weakref.WeakKeyDictionary()
)


def _pin_lock_for(known_hosts_path: str) -> asyncio.Lock:
    # Canonicalize so path aliases (relative/absolute, .., symlink, and — via normcase —
    # Windows case) that name the same file share one lock. strict=False: the file may
    # not exist yet (first pin); normcase folds case on Windows, no-op on POSIX.
    path_key = os.path.normcase(str(Path(known_hosts_path).expanduser().resolve(strict=False)))
    # Key the outer map by the loop OBJECT (weakly), not id(loop): a dead loop's id can be
    # reused by a later loop, which would hand back the dead loop's lock. A WeakKeyDictionary
    # drops the entry when the loop is gc'd, which also bounds growth.
    loop = asyncio.get_running_loop()
    per_path = _PIN_LOCKS.get(loop)
    if per_path is None:
        per_path = {}
        _PIN_LOCKS[loop] = per_path
    lock = per_path.get(path_key)
    if lock is None:
        # No await between get and set: atomic across asyncio tasks on this one loop.
        lock = asyncio.Lock()
        per_path[path_key] = lock
    return lock


class RemoteHostKeyError(Exception):
    """Host key verification failed (unknown host under strict, or a changed key).
    A distinct type so a caller can tell a security refusal from an ordinary
    connection failure — the two call for opposite responses."""


class RemoteProcessHandle:
    """A remote process as a :class:`ProcessHandle`. Wraps an asyncssh
    ``SSHClientProcess`` opened with ``encoding=None`` (bytes streams).

    ``terminate``/``kill`` send the signal over the channel and are idempotent on an
    exited process (asyncssh raises nothing once the channel is closed; we also guard
    on ``returncode``), matching the local handle and the dispatcher's ``_stop``."""

    def __init__(self, proc: "asyncssh.SSHClientProcess") -> None:
        self._proc = proc

    @property
    def pid(self) -> int | None:
        # A remote pid is not this host's and is not needed to kill (channel signal
        # does that). Reporting None is more honest than a fake sentinel.
        return None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    @property
    def stdout(self):  # asyncssh SSHReader (encoding=None) satisfies _ByteReader
        return self._proc.stdout

    @property
    def stderr(self):
        return self._proc.stderr

    async def wait(self, timeout: float | None = None) -> int | None:
        try:
            if timeout is None:
                await self._proc.wait()
            else:
                await asyncio.wait_for(self._proc.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return None
        return self._proc.returncode

    async def terminate(self) -> None:
        if self._proc.returncode is not None:
            return
        try:
            self._proc.terminate()  # SIGTERM over the channel
        except OSError:
            return

    async def kill(self) -> None:
        if self._proc.returncode is not None:
            return
        try:
            self._proc.kill()  # SIGKILL over the channel
        except OSError:
            return


class RemoteEnvironment:
    """Implements AsyncExecutionEnvironment (async run/probe) + Environment.launch,
    over one reused asyncssh connection (one instance per run, same event loop)."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 22,
        user: str,
        secret_ref: str,
        host_key_mode: HostKeyMode = "strict",
        known_hosts_path: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._secret_ref = secret_ref
        if host_key_mode not in ("strict", "tofu"):
            # A misconfigured host_key_mode is a security setting we cannot interpret;
            # refuse rather than guess (fail-closed).
            raise ValueError(
                f"host_key_mode must be 'strict' or 'tofu', got {host_key_mode!r}"
            )
        self._host_key_mode = host_key_mode
        self._known_hosts_path = known_hosts_path
        self._conn: "asyncssh.SSHClientConnection | None" = None

    # --- connection ---

    def _credentials(self) -> dict:
        """Resolve credentials from the env var named by ``secret_ref``. A key path is
        preferred; a password is the fallback. Never inline, never logged."""
        secret = os.environ.get(self._secret_ref)
        if not secret:
            raise RemoteHostKeyError(
                f"credential env var {self._secret_ref!r} is unset; refusing to connect"
            )
        # A path that exists is treated as a private key; otherwise as a password.
        if Path(secret).expanduser().is_file():
            return {"client_keys": [secret]}
        return {"password": secret}

    def _known_hosts_arg(self):
        """Map host_key_mode to asyncssh's ``known_hosts``. NEVER None (that would
        disable checking — the POC hazard this unit removes).

        strict: verify against the known_hosts file (an explicit path, or asyncssh's
        default ~/.ssh/known_hosts via ``()``) — an unknown or changed host key is
        rejected. This is the default and the safe posture (NFR-1).

        tofu: trust on first use (CR-6). The pinning itself happens in ``_connect_once``
        before the verified connect; by the time we build connect kwargs the key is
        already in ``known_hosts_path``, so tofu verifies against that same file exactly
        like strict. A changed key is then rejected like any strict mismatch — that is
        the whole point of pinning. ``known_hosts_path`` is REQUIRED for tofu: there must
        be a definite file to pin into, so refuse rather than pin somewhere implicit."""
        if self._host_key_mode == "tofu":
            if self._known_hosts_path is None:
                raise RemoteHostKeyError(
                    "host_key_mode 'tofu' requires known_hosts_path: there must be a "
                    "definite file to pin the first-seen key into; refusing to connect."
                )
            return self._known_hosts_path
        # strict:
        if self._known_hosts_path is not None:
            return self._known_hosts_path
        return ()  # asyncssh: () means "use the default known_hosts files" (strict)

    # --- trust on first use (CR-6) ---
    #
    # Scope (deliberately narrow — CR-6 chose option B). TOFU here supports the real
    # need: connecting to a small set of fixed hosts, pinning the first-seen key into a
    # known_hosts file that Agora owns, from a single process. It is NOT a general
    # OpenSSH known_hosts manager. The "already pinned?" decision is delegated to
    # asyncssh's own match_known_hosts, so it uses the SAME matching the verified connect
    # will use (comma lists, hashed entries, CA markers, non-default-port bare-host
    # fallback are all asyncssh's to judge, not ours) — that is what keeps a changed key
    # from slipping in via a matcher that disagrees with the verifier. Cross-process
    # sharing of one known_hosts file is out of scope: a single asyncio lock serializes
    # this process; concurrent Agora processes writing the same file are not supported.

    def _host_key_line_prefix(self) -> str:
        """The leading token to WRITE for a newly pinned host. asyncssh writes a
        non-default port as ``[host]:port`` and a default-port host bare. Used only when
        appending a new pin — the pinned/not-pinned decision is match_known_hosts', not
        this string's."""
        return self._host if self._port == 22 else f"[{self._host}]:{self._port}"

    def _host_is_pinned(self) -> bool:
        """True if known_hosts_path already trusts a key for this host, decided by
        asyncssh's own matcher (the one the verified connect uses). A missing file is
        'not pinned'. A file we cannot read/parse is NOT silently treated as empty —
        the error propagates, because overwriting an unreadable trust store is exactly
        the failure this mode must not cause."""
        path = Path(self._known_hosts_path).expanduser()
        if not path.is_file():
            return False
        known = asyncssh.read_known_hosts(str(path))
        port = None if self._port == 22 else self._port
        # addr="" — we match on hostname + port only. asyncssh treats addr as an IP and
        # would call ip_address() on it; passing the (possibly non-IP) host there raises.
        # At connect time asyncssh fills addr with the resolved peer IP; here, pre-connect,
        # we only have the host, and matching a pin by host is what TOFU needs.
        result = asyncssh.match_known_hosts(known, self._host, "", port)
        host_keys, ca_keys = result[0], result[1]  # (host_keys, ca_keys, revoked, ...)
        # A trusted host key OR a CA that would sign this host's key both count as pinned:
        # in either case the verified connect has something to trust, so TOFU must not
        # append a second key (which asyncssh would then ALSO trust).
        return bool(host_keys) or bool(ca_keys)

    def _known_hosts_line(self, key: "asyncssh.SSHKey") -> str:
        """Build one known_hosts line: ``<host-prefix> <algorithm> <base64>``. The
        openssh public-key export is ``algorithm base64 [comment]``; we keep only the
        first two fields (a comment does not belong in known_hosts)."""
        exported = key.export_public_key("openssh").decode("ascii").split()
        if len(exported) < 2:
            raise RemoteHostKeyError(
                f"could not format host key for {self._host}: unexpected export shape"
            )
        algo, b64 = exported[0], exported[1]
        return f"{self._host_key_line_prefix()} {algo} {b64}\n"

    def _append_known_host_atomically(self, new_line: str) -> None:
        """Append one line to known_hosts_path via write-temp-then-os.replace, so a
        crash or a concurrent reader never sees a torn file. Durability across an OS/power
        crash is NOT guaranteed (no fsync): a lost pin just means the next connect pins
        again, and known_hosts is reconstructible — not worth the fsync cost/complexity.
        Cross-process concurrent writers of the same file are out of scope (see the scope
        note above); within this process the pin lock serializes callers."""
        path = Path(self._known_hosts_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".known_hosts.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(existing + new_line)
            os.replace(tmp, str(path))
        except BaseException:
            # Never leave the temp file behind on any failure (including cancellation).
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    async def _tofu_pin_if_needed(self) -> None:
        """Trust on first use: if this host is not yet pinned, fetch the key the server
        presents and pin it, so the subsequent verified connect (strict against the same
        file) succeeds. The first-connect trust window is inherent to TOFU. A host whose
        key later CHANGES is not re-pinned (it stays pinned to the first key), so the
        verified connect rejects it like any strict mismatch — the whole value of pinning.

        The check->fetch->write runs under a lock and RE-checks inside it, so two callers
        racing the first connect cannot both pin (which would leave asyncssh trusting two
        keys for the host)."""
        if self._host_key_mode != "tofu" or self._host_is_pinned():
            return
        async with _pin_lock_for(self._known_hosts_path):
            if self._host_is_pinned():   # re-check: another caller may have pinned first
                return
            key = await asyncssh.get_server_host_key(
                self._host, self._port, config=None  # config=None: CR-21, never read system ssh config
            )
            if key is None:
                # A handshake with no server host key (e.g. GSS kex) cannot be pinned;
                # under tofu that means nothing to trust — fail closed rather than proceed.
                raise RemoteHostKeyError(
                    f"server {self._host} presented no host key to pin (tofu); refusing."
                )
            self._append_known_host_atomically(self._known_hosts_line(key))

    async def _connect_once(self) -> "asyncssh.SSHClientConnection":
        await self._tofu_pin_if_needed()
        kwargs = {
            "host": self._host,
            "port": self._port,
            "username": self._user,
            "known_hosts": self._known_hosts_arg(),
            "connect_timeout": CONNECT_TIMEOUT,
            "keepalive_interval": KEEPALIVE_INTERVAL,
            "keepalive_count_max": KEEPALIVE_MAX_MISSED,
            # config=None: never read the system ssh config (CR-21). Every connection
            # parameter is supplied explicitly from RemoteAdapterConfig, so a user's
            # ~/.ssh/config must not silently influence an Agora connection. It also
            # avoids asyncssh decoding that file with the platform locale — on a
            # Japanese Windows (cp932) that raises UnicodeDecodeError before the
            # handshake even starts.
            "config": None,
            **self._credentials(),
        }
        try:
            return await asyncssh.connect(**kwargs)
        except asyncssh.HostKeyNotVerifiable as exc:
            raise RemoteHostKeyError(f"host key not verifiable for {self._host}") from exc

    async def _ensure_conn(self) -> "asyncssh.SSHClientConnection":
        """Lazy connect, reused. Bounded retry with exponential backoff for transient
        connection failures; a host-key refusal is NOT retried (fail-closed)."""
        if self._conn is not None:
            return self._conn
        attempt = 0
        while True:
            try:
                self._conn = await self._connect_once()
                return self._conn
            except RemoteHostKeyError:
                raise  # security refusal — never retried
            except (OSError, asyncssh.Error) as exc:
                if attempt >= CONNECT_MAX_RETRIES:
                    raise
                await asyncio.sleep(CONNECT_RETRY_BACKOFF * (2 ** attempt))
                attempt += 1

    async def aclose(self) -> None:
        if self._conn is not None:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None

    async def _discard_conn(self) -> None:
        """Drop the current connection after a transport error, closing it so the
        channel/socket is reaped rather than leaked (a bare ``self._conn = None``
        would strand it beyond aclose's reach)."""
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
            try:
                await conn.wait_closed()
            except (OSError, asyncssh.Error):
                pass  # already broken — we only needed to release it

    # --- helpers ---

    @staticmethod
    def _remote_cmd(argv: Sequence[str], cwd: Path | None) -> str:
        """argv -> a single remote command line. Every element is shlex.quote'd so a
        prompt or path can never break out into extra remote words. cwd is normalized
        to a POSIX path (the remote is Linux even when the client is Windows)."""
        quoted = " ".join(shlex.quote(str(a)) for a in argv)
        if cwd is not None:
            return f"cd {shlex.quote(_posix(cwd))} && {quoted}"
        return quoted

    # --- AsyncExecutionEnvironment ---

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float,
        read_only: bool = False,
    ) -> CommandResult:
        if not argv:
            raise ValueError("argv must name a program to run")
        cmd = self._remote_cmd(argv, cwd)
        attempts = (CONNECT_MAX_RETRIES + 1) if read_only else 1
        for i in range(attempts):
            try:
                conn = await self._ensure_conn()
                completed = await asyncio.wait_for(
                    conn.run(cmd, encoding=None, check=False), timeout=timeout
                )
            except (TimeoutError, asyncio.TimeoutError):
                return CommandResult("timeout")
            except RemoteHostKeyError as exc:
                return CommandResult("unstartable", error=OSError(str(exc)))
            except (OSError, asyncssh.Error) as exc:
                # Transport error. Retry only a declared read-only op; otherwise the
                # command may have had a side effect and must not be re-run. Close the
                # broken connection so the next attempt reconnects cleanly.
                await self._discard_conn()
                if read_only and i < attempts - 1:
                    await asyncio.sleep(CONNECT_RETRY_BACKOFF * (2 ** i))
                    continue
                return CommandResult("unstartable", error=OSError(str(exc)))
            # bytes -> UTF-8 by us (never the remote locale).
            out = (completed.stdout or b"").decode("utf-8", errors="replace")
            err = (completed.stderr or b"").decode("utf-8", errors="replace")
            return CommandResult(
                "completed", returncode=completed.exit_status, stdout=out, stderr=err
            )

    async def probe(self, path: Path, *, follow_symlinks: bool = True) -> PathProbe:
        """What is at ``path`` on the remote, via SFTP — NOT shell text.

        A shell ``stat``/``test`` cannot keep fsprobe's core rule: a false ``[ -e ]``
        conflates ENOENT with permission/ENOTDIR/loop failures, reporting "absent" for
        things that are merely unobservable. SFTP gives TYPED errors: only
        ``SFTPNoSuchFile`` (ENOENT) is ``absent``; any other SFTP error is ``unknown``.

        follow_symlinks selects ``stat`` (dereference — a dangling link raises, which is
        NOT absent) vs ``lstat`` (the link itself). Kind comes from the st_mode
        permissions (``stat.S_IS*``), the same basis as local probe_path; size from the
        attrs. probe is read-only, so a transport error is retried."""
        attempts = CONNECT_MAX_RETRIES + 1
        for i in range(attempts):
            try:
                conn = await self._ensure_conn()
                async with conn.start_sftp_client() as sftp:
                    # lstat FIRST — it does not dereference, so only its SFTPNoSuchFile
                    # means the path ITSELF is absent. (sftp.stat on a dangling symlink
                    # also raises SFTPNoSuchFile — the TARGET is gone — but the link is
                    # present, which is 'other', not absent. Leading with stat would
                    # mislabel that as absent, exactly what fsprobe refuses.)
                    try:
                        link_attrs = await sftp.lstat(_posix(path))
                    except asyncssh.SFTPNoSuchFile:
                        return _PathProbe("absent")           # the path itself is gone
                    link_mode = link_attrs.permissions
                    if not follow_symlinks:
                        return _probe_from_mode(link_mode, link_attrs.size)
                    if link_mode is not None and stat.S_ISLNK(link_mode):
                        # follow requested & it's a symlink: resolve the target. A
                        # broken/unresolvable target is 'present but unresolvable' ->
                        # other, not absent. (Transport errors fall through to retry.)
                        try:
                            tgt = await sftp.stat(_posix(path))
                        except _SFTP_TRANSPORT_ERRORS:
                            raise  # let the outer transport handler discard+retry
                        except asyncssh.SFTPError as exc:
                            return _PathProbe("other", error=OSError(str(exc)))
                        return _probe_from_mode(tgt.permissions, tgt.size)
                    return _probe_from_mode(link_mode, link_attrs.size)
            except _SFTP_TRANSPORT_ERRORS as exc:
                # Connection lost / no connection — a transport failure, not a
                # filesystem answer. Discard and retry (probe is read-only).
                await self._discard_conn()
                if i < attempts - 1:
                    await asyncio.sleep(CONNECT_RETRY_BACKOFF * (2 ** i))
                    continue
                return _PathProbe("unknown", error=OSError(str(exc)))
            except asyncssh.SFTPError as exc:
                # A filesystem-status error (permission, ENOTDIR, loop, ...) — a real
                # answer that the path is unobservable, NOT absent. No retry.
                return _PathProbe("unknown", error=OSError(str(exc)))
            except RemoteHostKeyError as exc:
                return _PathProbe("unknown", error=OSError(str(exc)))
            except (OSError, asyncssh.Error) as exc:
                await self._discard_conn()
                if i < attempts - 1:
                    await asyncio.sleep(CONNECT_RETRY_BACKOFF * (2 ** i))
                    continue
                return _PathProbe("unknown", error=OSError(str(exc)))

    # --- Environment.launch (shared, async) ---

    async def launch(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ProcessHandle:
        if not argv:
            raise ValueError("argv must name a program to run")
        cmd = self._remote_cmd(argv, cwd)
        # launch is side-effecting: a connection failure is fail-closed (no retry).
        conn = await self._ensure_conn()
        # SECURITY: the caller passes {**os.environ, AGORA_*}. Forwarding os.environ
        # over SSH env requests would ship arbitrary local secrets (the secret_ref
        # credential, AWS_*, tokens, proxy creds, ...) to the REMOTE host. Treat the
        # caller's env as untrusted: forward ONLY an allowlist — the AGORA_* the agent
        # needs — and nothing else. (Widening the allowlist is a config-layer decision,
        # U5; the default is deny.)
        remote_env: dict[str, str] | None = None
        if env is not None:
            remote_env = {
                k: v for k, v in env.items()
                if k in _LAUNCH_ENV_ALLOWLIST                   # only the explicit few
                and not _env_name_eq(k, self._secret_ref)       # defense-in-depth: never the credential
            }
        proc = await conn.create_process(cmd, encoding=None, env=remote_env)
        return RemoteProcessHandle(proc)

    # --- AsyncFilesystem (CR-11) ---

    @property
    def fs(self) -> "RemoteFilesystem":
        """The remote filesystem seam (U3/U4). Shares this environment's ONE asyncssh
        connection (same instance, same event loop — CR-4); it does not open a second.
        A fresh SFTP client is started per operation (asyncssh multiplexes them over the
        one connection) and closed with the operation, so no handle outlives its use."""
        return RemoteFilesystem(self)


class RemoteFilesystem:
    """SFTP-backed :class:`agora.environments.AsyncFilesystem`, over a
    :class:`RemoteEnvironment`'s reused connection. See the protocol for the safety
    semantics each method upholds; this implements them on asyncssh SFTP.

    Every method reaches the connection through ``self._env._ensure_conn()`` (lazy,
    bounded-retry, host-key-checked — the same path run/probe/launch use) and opens a
    per-op SFTP client. Transport errors are NOT silently retried here the way probe is:
    a filesystem MUTATION (mkdir/rmdir/write) that half-happened must not be blindly
    repeated. Read-only ``realpath``/``is_empty_dir``/``read_file`` surface the error to
    the caller, which fails closed."""

    def __init__(self, env: "RemoteEnvironment") -> None:
        self._env = env

    async def realpath(self, path: Path) -> Path:
        conn = await self._env._ensure_conn()
        async with conn.start_sftp_client() as sftp:
            return await self._resolve_verified(sftp, _posix(path))

    async def _resolve_verified(self, sftp, target: str) -> "PurePosixPath":
        """Canonicalize ``target`` by walking it ONE component at a time with ``lstat``,
        so every component the result claims to exist is one we actually observed. This
        is a single state machine over a PENDING queue of components (not recursion):
        a symlink expands by pushing its target's components back onto the front of the
        queue, so the "not-yet-existing tail" state and the ``..`` rule live in exactly
        one place and cannot be lost across a recursion boundary.

        Why not ``sftp.realpath``: its default mode is NO_CHECK — asyncssh's built-in
        server canonicalizes with a non-strict ``os.path.realpath`` that can keep going
        past a permission-blocked or absent component and return a plausible string. A
        confinement check built on that would read a merely-unobservable component as
        absent, and let a dangling symlink whose target escapes the allowed root come
        back looking like an in-root path. So we resolve it ourselves, with these rules:

          - ``lstat`` (never dereferences) each existing component. ``SFTPNoSuchFile`` —
            the one status that means definite ENOENT — flips into ``exhausted``: this
            and every remaining component (INCLUDING those a symlink pushed on) is a
            not-yet-existing tail. ANY other SFTPError (permission, ENOTDIR, transport)
            RAISES — could-not-observe is never absence.
          - a symlink is resolved EXPLICITLY via ``readlink``; its target's components
            are pushed back onto the queue (absolute target also resets ``resolved`` to
            ``/``), so an escaping link is followed to where it really goes and then
            confinement-checked there — never masked. Bounded by a symlink budget.
          - an ``exhausted`` (not-yet-existing) component is appended literally; a
            symlink cannot exist under a not-yet-existing prefix, so no lstat is done
            once exhausted.
          - a component that exists but whose type cannot be determined
            (``permissions is None``) RAISES — an unknown type is not assumed to be a
            plain directory (it could be an escaping link).
          - descending INTO a component requires it to be a directory; a regular file
            (or other non-dir) as an intermediate component RAISES ENOTDIR rather than
            being silently traversed (which would swallow a ``..`` off a file).
          - ``..`` pops the resolved prefix (never below ``/``); ``.`` is a no-op.
            ``..`` while ``exhausted`` is an escape past the verified prefix -> raise.
        """
        if not target.startswith("/"):
            raise ValueError(f"remote path must be absolute: {target!r}")

        resolved = PurePosixPath("/")
        # Split the RAW target on "/" (NOT PurePosixPath.parts): parts collapses a
        # trailing "/." or "/" so "/file/." would lose the marker that its last real
        # component must be a directory. Keeping the literal ""/"." components lets the
        # ENOTDIR check fire for an initial input just as it does for a symlink target
        # (the two must not diverge — a gap in either is the same escape). The leading
        # "" from the absolute prefix is dropped.
        initial = target.split("/")[1:]
        pending: deque[str] = deque(initial)
        exhausted = False  # have we passed the last existing component?
        symlink_budget = 32

        # Verify the root itself is observable before trusting it as the prefix — the
        # method promises "unobservable raises", and returning "/" for an input of "/"
        # (empty queue) or a link that lands on "/" must not skip that promise.
        try:
            root_attrs = await sftp.lstat("/")
        except asyncssh.SFTPError as exc:
            raise OSError(f"cannot resolve remote root '/': {exc}") from exc
        root_mode = root_attrs.permissions
        if root_mode is None or not stat.S_ISDIR(root_mode):
            raise OSError("remote root '/' is not an observable directory")

        while pending:
            name = pending.popleft()
            if name in ("", "."):
                continue
            if name == "..":
                if exhausted:
                    # A '..' after a component we already proved absent would climb back
                    # over the verified prefix — the escape the confinement check exists
                    # to refuse.
                    raise ValueError(
                        f"remote path escapes via '..' after the existing prefix: {target!r}"
                    )
                if resolved != PurePosixPath("/"):
                    resolved = resolved.parent
                continue
            if exhausted:
                # Nothing under a not-yet-existing prefix can be lstat'd; append literally.
                resolved = resolved / name
                continue

            candidate = resolved / name
            try:
                attrs = await sftp.lstat(_posix(candidate))
            except asyncssh.SFTPNoSuchFile:
                exhausted = True
                resolved = candidate  # first not-yet-existing component
                continue
            except asyncssh.SFTPError as exc:
                # Permission, ENOTDIR, transport, ... — unobservable, NOT absent.
                raise OSError(
                    f"cannot resolve remote component {_posix(candidate)}: {exc}"
                ) from exc

            mode = attrs.permissions
            if mode is None:
                # Type undeterminable: do NOT assume "plain directory" — it could be an
                # escaping symlink. Fail closed rather than confinement-check a guess.
                raise OSError(
                    f"cannot determine type of remote component {_posix(candidate)}"
                )
            if stat.S_ISLNK(mode):
                symlink_budget -= 1
                if symlink_budget < 0:
                    raise OSError(f"too many symlink levels resolving {target!r}")
                link_target = _as_str(await sftp.readlink(_posix(candidate)))
                # Split the RAW target on "/" rather than through PurePosixPath.parts:
                # PurePosixPath collapses a trailing "/." or "/" ("/file/." -> parts
                # ('/','file')), which would hide that the target REQUIRES its last real
                # component to be a directory. Keeping the literal "." / "" components
                # lets the ENOTDIR check below fire when the target names a non-dir with
                # a trailing slash. The loop already treats "" and "." as no-ops except
                # that they keep `pending` non-empty, which is exactly the signal needed.
                is_absolute = link_target.startswith("/")
                link_parts = [c for c in link_target.split("/")]
                if is_absolute:
                    resolved = PurePosixPath("/")  # absolute target restarts from root
                    link_parts = link_parts[1:]  # drop the empty leading component
                # Push the link's components to the FRONT, ahead of the remaining tail,
                # so they are walked (and confinement-checked) before we continue.
                pending.extendleft(reversed(link_parts))
                continue
            if stat.S_ISDIR(mode):
                resolved = candidate
                continue
            # A regular file or other non-directory as an INTERMEDIATE component. If it
            # is the last component, it is a valid resolved path; if anything follows,
            # descending into it is ENOTDIR.
            if pending:
                raise OSError(
                    f"remote component is not a directory (ENOTDIR): {_posix(candidate)}"
                )
            resolved = candidate
        return resolved

    async def is_empty_dir(self, path: Path) -> bool:
        conn = await self._env._ensure_conn()
        async with conn.start_sftp_client() as sftp:
            # readdir enumerates the directory itself; SFTPError (permission, ENOTDIR,
            # transport) propagates rather than being read as an answer. A '.'/'..'-only
            # listing is empty; ANY other name (including hidden dotfiles) is content.
            names = await sftp.listdir(_posix(path))
            for name in names:
                n = _as_str(name)
                if n not in (".", ".."):
                    return False
            return True

    async def read_file(
        self, path: Path, *, max_bytes: int = MAX_FS_READ_BYTES, truncate: bool = False
    ) -> bytes:
        conn = await self._env._ensure_conn()
        async with conn.start_sftp_client() as sftp:
            # Two modes. Default (truncate=False): WHOLE file or raise — a file larger
            # than max_bytes is an error, never a silent short answer (config-file
            # semantics; U3 provisioner). truncate=True: a bounded PREVIEW — read at most
            # max_bytes and return what is there, never raising for size (U4 read_file,
            # which reports its own `truncated` flag). Absence/permission still raise in
            # both modes (fsprobe rule): SFTPNoSuchFile -> FileNotFoundError;
            # SFTPPermissionDenied -> PermissionError; other SFTP -> OSError.
            try:
                attrs = await sftp.stat(_posix(path))
            except asyncssh.SFTPNoSuchFile as exc:
                raise FileNotFoundError(_posix(path)) from exc
            except asyncssh.SFTPPermissionDenied as exc:
                raise PermissionError(_posix(path)) from exc
            except asyncssh.SFTPError as exc:
                raise OSError(f"cannot stat remote file {_posix(path)}: {exc}") from exc
            if not truncate and attrs.size is not None and attrs.size > max_bytes:
                raise OSError(
                    f"remote file {_posix(path)} is {attrs.size} bytes, exceeds cap {max_bytes}"
                )
            # Read cap: whole-file mode reads max_bytes+1 to DETECT overflow; preview mode
            # reads exactly max_bytes (a short read past it is the truncation, not an error).
            read_cap = max_bytes if truncate else max_bytes + 1
            try:
                async with sftp.open(_posix(path), "rb") as fh:
                    # SFTP read(n) may return a SHORT read that is NOT EOF (a server
                    # can cap its reply size); loop until EOF or the read cap.
                    chunks: list[bytes] = []
                    total = 0
                    while total < read_cap:
                        chunk = await fh.read(read_cap - total)
                        if not chunk:
                            break  # EOF
                        chunks.append(chunk)
                        total += len(chunk)
                    data = b"".join(chunks)
            except asyncssh.SFTPNoSuchFile as exc:
                raise FileNotFoundError(_posix(path)) from exc
            except asyncssh.SFTPPermissionDenied as exc:
                raise PermissionError(_posix(path)) from exc
            except asyncssh.SFTPError as exc:
                raise OSError(f"cannot read remote file {_posix(path)}: {exc}") from exc
            if not truncate and len(data) > max_bytes:
                raise OSError(
                    f"remote file {_posix(path)} exceeds cap {max_bytes} bytes"
                )
            return data

    async def write_file(self, path: Path, content: bytes) -> None:
        conn = await self._env._ensure_conn()
        async with conn.start_sftp_client() as sftp:
            # "wb" creates or truncates. Does NOT create parents (mkdir is the explicit,
            # auditable step); a missing parent surfaces as SFTPNoSuchFile -> OSError.
            try:
                async with sftp.open(_posix(path), "wb") as fh:
                    await fh.write(content)
            except asyncssh.SFTPPermissionDenied as exc:
                raise PermissionError(_posix(path)) from exc
            except asyncssh.SFTPError as exc:
                raise OSError(f"cannot write remote file {_posix(path)}: {exc}") from exc

    async def mkdir(self, path: Path) -> None:
        conn = await self._env._ensure_conn()
        async with conn.start_sftp_client() as sftp:
            # Single directory only (asyncssh mkdir is non-recursive — it does not
            # create parents, which is exactly what D2 form-2 needs: a missing parent
            # must fail, not silently rebuild a deleted root). Already-exists surfaces
            # as SFTPFailure; let it raise so the caller's probe/empty logic decides.
            try:
                await sftp.mkdir(_posix(path))
            except asyncssh.SFTPPermissionDenied as exc:
                raise PermissionError(_posix(path)) from exc
            except asyncssh.SFTPError as exc:
                raise OSError(f"cannot mkdir remote {_posix(path)}: {exc}") from exc

    async def rmdir(self, path: Path) -> None:
        conn = await self._env._ensure_conn()
        async with conn.start_sftp_client() as sftp:
            # asyncssh rmdir removes an EMPTY directory and fails on a non-empty one —
            # it never recurses. A non-empty failure means the world changed under the
            # provisioner; we surface it and STOP (FR-5: never delete content we did not
            # place). No _SFTP_TRANSPORT_ERRORS retry: a mutation must not blindly repeat.
            try:
                await sftp.rmdir(_posix(path))
            except asyncssh.SFTPPermissionDenied as exc:
                raise PermissionError(_posix(path)) from exc
            except asyncssh.SFTPError as exc:
                raise OSError(f"cannot rmdir remote {_posix(path)}: {exc}") from exc


#: The ONLY env vars forwarded to the remote agent process. A finite, explicit list —
#: NOT an "AGORA_*" prefix, because Agora-local secrets also carry that prefix (e.g.
#: AGORA_CONTROL_PLANE_TOKEN, the SSH credential secret_ref). The agent needs exactly
#: these three to identify its run; everything else (the caller's whole os.environ) is
#: untrusted and does not cross the SSH trust boundary. Widening this is a config-layer
#: decision (U5), still explicit — never a wildcard.
_LAUNCH_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {"AGORA_TASK_ID", "AGORA_RUN_ID", "AGORA_PROJECT_ID"}
)

#: SFTP errors that are TRANSPORT failures (connection gone), not filesystem answers.
#: These are retried (probe is read-only); every other SFTPError is a real status
#: answer (permission, ENOTDIR, loop, ...) meaning "unobservable", returned as-is.
_SFTP_TRANSPORT_ERRORS = (asyncssh.SFTPConnectionLost, asyncssh.SFTPNoConnection)


def _env_name_eq(a: str, b: str) -> bool:
    """Compare two environment-variable names by the local OS's rules. On Windows env
    names are case-insensitive and ``os.environ`` canonicalizes keys with ``str.upper``
    — so match with ``upper()`` (NOT ``os.path.normcase``, which is *path* normalization:
    it lowercases and treats ``/`` == ``\\``). Elsewhere names are case-sensitive."""
    return a.upper() == b.upper() if os.name == "nt" else a == b


def _as_str(value) -> str:
    """asyncssh SFTP methods (``realpath``, ``listdir``) return ``str`` or ``bytes``
    depending on the client's ``path_encoding`` — normalize to ``str`` so callers never
    branch on it. Bytes decode UTF-8 (never the remote locale, same rule as command
    output); a surrogateescape-safe decode keeps an odd byte from raising here (a
    filename we cannot decode is still a filename that occupies the directory)."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return value


def _posix(path) -> str:
    """A remote path is always POSIX. On a Windows client ``str(Path('/a/b'))`` is
    ``'\\a\\b'`` — backslashes the Linux remote would misread. Use the path's OWN
    ``as_posix()`` (its own flavor) rather than re-wrapping in a host-flavored
    ``PurePath``, which could mis-handle a foreign Windows path on a POSIX host. A
    ``str`` is passed through unchanged (already the caller's intended remote form)."""
    if isinstance(path, str):
        return path
    return path.as_posix()


def _probe_from_mode(mode: "int | None", size: "int | None") -> PathProbe:
    """Map an SFTP st_mode to a PathProbe kind, the same basis as local probe_path.
    ``mode is None`` (server omitted permissions) means the entry was observed but its
    type is unavailable -> 'other', not a guess."""
    if mode is None:
        return _PathProbe("other")
    if stat.S_ISDIR(mode):
        return _PathProbe("dir")
    if stat.S_ISREG(mode):
        return _PathProbe("file", size=size)
    return _PathProbe("other")  # symlink (lstat), socket, fifo, device, ...
