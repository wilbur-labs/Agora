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
import socket
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


#: User-level bin directories agent CLIs are commonly installed into (``pip --user`` /
#: ``pipx`` / the Claude Code installer use ~/.local/bin; npm with a user prefix, bun,
#: cargo, volta). An SSH exec runs a non-login shell, so a PATH set in ~/.profile is
#: missing and ``claude`` there is "command not found" (CR-23). Prepended as shell text
#: on the remote side so ``$HOME`` expands THERE; the list is constant, never input.
_AGENT_PATH_DIRS = (
    "$HOME/.local/bin",
    "$HOME/bin",
    "$HOME/.npm-global/bin",
    "$HOME/.bun/bin",
    "$HOME/.cargo/bin",
    "$HOME/.volta/bin",
)
#: Markers around the probed login PATH, so whatever a profile script prints (banners,
#: "no job control" warnings) cannot be mistaken for it.
_PATH_BEGIN = "__AGORA_LOGIN_PATH_BEGIN__"
_PATH_END = "__AGORA_LOGIN_PATH_END__"
#: The login-shell probe runs the user's profile; a profile that blocks must not stall
#: a run. On timeout the probe is skipped, not failed.
LOGIN_PATH_PROBE_TIMEOUT = 10.0
_UNPROBED = object()


def _parse_login_path(stdout: str) -> str | None:
    """The PATH printed between the markers, or None when the probe produced none."""
    start = stdout.rfind(_PATH_BEGIN)
    if start < 0:
        return None
    start += len(_PATH_BEGIN)
    end = stdout.find(_PATH_END, start)
    if end < 0:
        return None
    path = stdout[start:end].strip()
    return path or None


def _agent_path_word(login_path: str | None) -> str:
    """One shell word ``PATH=<user bins>:<base>`` for ``env``. The user-bin part is
    double-quoted so ``$HOME`` expands on the remote; the probed login PATH is a literal
    and goes in single quotes via shlex; without a probe the remote's own ``$PATH`` is
    the base. Adjacent quoted strings join into one word in POSIX sh."""
    bins = ":".join(_AGENT_PATH_DIRS)
    if login_path:
        return f'PATH="{bins}"' + shlex.quote(":" + login_path)
    return f'PATH="{bins}:$PATH"'


def _user_known_hosts() -> Path:
    """The operator's own OpenSSH known_hosts (``~/.ssh/known_hosts``; on Windows
    %USERPROFILE%\.ssh). TOFU consults it as well as Agora's own pin file, so a host the
    operator already trusts is never re-pinned to whatever key it presents now."""
    return Path.home() / ".ssh" / "known_hosts"


def _load_known_hosts(paths: Sequence[Path]) -> "asyncssh.SSHKnownHosts | None":
    """Parse the existing files among ``paths`` as ONE trust store, decoded as UTF-8.
    asyncssh's own ``read_known_hosts`` opens files in the platform encoding — cp932 on
    a Japanese Windows — so a UTF-8 file with a non-ASCII comment would fail every
    connection. None when none of the files exists. An undecodable or unparseable file
    raises: an unreadable trust store is never treated as empty."""
    texts = [p.read_text(encoding="utf-8") for p in paths if p.is_file()]
    if not texts:
        return None
    return asyncssh.import_known_hosts("\n".join(texts) + "\n")


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
        credential_source: str = "env_var",
        environment_id: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._secret_ref = secret_ref
        if credential_source not in ("env_var", "key_path", "keyring", "ssh_default"):
            # A credential source we cannot interpret is a security setting; refuse
            # rather than guess (fail-closed, same posture as host_key_mode).
            raise ValueError(
                f"credential_source must be 'env_var', 'key_path', 'keyring' or 'ssh_default', "
                f"got {credential_source!r}"
            )
        if credential_source == "keyring" and not environment_id:
            # keyring credentials are keyed by environment_id; without it there is
            # nothing to look up. Refuse at construction, not at connect.
            raise ValueError(
                "credential_source 'keyring' requires environment_id (the keyring key)"
            )
        self._credential_source = credential_source
        self._environment_id = environment_id
        if host_key_mode not in ("strict", "tofu"):
            # A misconfigured host_key_mode is a security setting we cannot interpret;
            # refuse rather than guess (fail-closed).
            raise ValueError(
                f"host_key_mode must be 'strict' or 'tofu', got {host_key_mode!r}"
            )
        self._host_key_mode = host_key_mode
        self._known_hosts_path = known_hosts_path
        self._conn: "asyncssh.SSHClientConnection | None" = None
        #: False for a connection that must only verify — the environment page's test
        #: connection — so connecting never records a host key nobody confirmed.
        self.pin_on_connect = True
        # The remote login shell's PATH, probed once per instance (= per run); None when
        # the probe produced nothing usable (CR-23).
        self._login_path: object = _UNPROBED

    # --- connection ---

    def _credentials(self) -> dict:
        """Resolve credentials by ``credential_source`` (adr/0001). Never inline, never
        logged. Three sources:

        - ``keyring``: the password lives in the OS keyring, keyed by environment_id
          (never in the DB). Fetched here at connect time; a missing keyring entry or
          an unavailable backend is a fail-closed refusal, not a silent fallback.
        - ``key_path``: ``secret_ref`` holds a private-key file PATH (not a secret).
        - ``env_var``: ``secret_ref`` names an env var (slice-1 mechanism). Its value
          is a key path if it points at a file, otherwise a password.
        """
        if self._credential_source == "keyring":
            # Imported lazily: keeps the asyncssh module usable in contexts (tests,
            # tooling) that never touch the keyring, and mirrors how this module is
            # imported only where a remote environment is actually built.
            from agora.execution import credential_store

            try:
                password = credential_store.get_password(self._environment_id)
            except credential_store.KeyringUnavailableError as exc:
                # Must surface as a refusal the run/probe paths already handle; a bare
                # RuntimeError would escape them as an unhandled crash.
                raise RemoteHostKeyError(
                    f"OS keyring unavailable for environment {self._environment_id!r}; "
                    f"refusing to connect ({exc})"
                ) from exc
            if not password:
                raise RemoteHostKeyError(
                    f"no keyring password set for environment {self._environment_id!r}; "
                    f"refusing to connect"
                )
            return {"password": password}
        if self._credential_source == "ssh_default":
            # This machine's own SSH setup, as the `ssh` command uses it (CR-24): pass no
            # credential, and asyncssh loads the default key files from ~/.ssh (skipping
            # passphrase-protected ones) and an agent if SSH_AUTH_SOCK is set. Nothing is
            # read here, and nothing crosses to the remote.
            return {}
        if self._credential_source == "key_path":
            key_path = self._secret_ref
            if not key_path or not Path(key_path).expanduser().is_file():
                raise RemoteHostKeyError(
                    f"key-path credential {key_path!r} is not a readable file; "
                    f"refusing to connect"
                )
            return {"client_keys": [key_path]}
        # env_var (slice-1): the env var named by secret_ref holds a key path or a
        # password.
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
        be a definite file to pin into, so refuse rather than pin somewhere implicit.
        When the operator's own ``~/.ssh/known_hosts`` exists, tofu verifies against it
        too: a host already trusted there keeps that key (see ``_host_is_pinned``)."""
        if self._host_key_mode == "tofu":
            if self._known_hosts_path is None:
                raise RemoteHostKeyError(
                    "host_key_mode 'tofu' requires known_hosts_path: there must be a "
                    "definite file to pin the first-seen key into; refusing to connect."
                )
            own = Path(self._known_hosts_path).expanduser()
            loaded = _load_known_hosts([own, _user_known_hosts()])
            # Nothing on disk yet: hand asyncssh the path, which it rejects as unknown.
            return loaded if loaded is not None else self._known_hosts_path
        # strict: the explicit file, else the operator's default — read as UTF-8 here
        # rather than by asyncssh in the platform encoding.
        path = (
            Path(self._known_hosts_path).expanduser()
            if self._known_hosts_path is not None
            else _user_known_hosts()
        )
        loaded = _load_known_hosts([path])
        if loaded is not None:
            return loaded
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
        """True if Agora's pin file OR the operator's own ``~/.ssh/known_hosts`` already
        trusts a key for this host, decided by asyncssh's own matcher (the one the
        verified connect uses). Checking the operator's file is what stops TOFU from
        pinning a key that differs from one they already trust — the exact signature of
        a man in the middle; the verified connect then rejects it. A missing file is
        'not pinned'. A file we cannot read/parse is NOT silently treated as empty —
        the error propagates, because overwriting an unreadable trust store is exactly
        the failure this mode must not cause."""
        return any(
            self._file_trusts_host(path)
            for path in (Path(self._known_hosts_path).expanduser(), _user_known_hosts())
        )

    def _file_trusts_host(self, path: Path) -> bool:
        known = _load_known_hosts([path])
        if known is None:
            return False
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

    # --- looking before trusting (environment page: test connection) ---

    def _trust_files(self) -> list[Path]:
        """The files a verified connect trusts for this host, in this mode."""
        if self._host_key_mode == "tofu":
            return [Path(self._known_hosts_path).expanduser(), _user_known_hosts()] if self._known_hosts_path else []
        return [Path(self._known_hosts_path).expanduser() if self._known_hosts_path else _user_known_hosts()]

    def _trusted_for_host(self, addresses: "Sequence[str]" = ("",)) -> "tuple[list, bool, list]":
        """The host keys this environment's files trust for the host, and whether a CA is.
        Matched by name and by each of the host's addresses, as the connection itself
        matches by name and peer address: a key recorded under the IP is trusted too."""
        port = None if self._port == 22 else self._port
        trusted: list = []
        revoked: list = []
        has_ca = False
        for path in self._trust_files():
            known = _load_known_hosts([path])
            if known is None:
                continue
            for addr in addresses or ("",):
                result = asyncssh.match_known_hosts(known, self._host, addr, port)
                trusted += list(result[0])
                has_ca = has_ca or bool(result[1])
                revoked += list(result[2])
        return trusted, has_ca, revoked

    async def _addresses(self) -> list[str]:
        """The host's addresses as the connection would resolve them; empty when it cannot."""
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(self._host, self._port, type=socket.SOCK_STREAM)
        except OSError:
            return [""]
        return list(dict.fromkeys(info[4][0] for info in infos)) or [""]

    async def presented_host_key(self, *, timeout: float = 15.0) -> "tuple[asyncssh.SSHKey, str]":
        """The key the server presents and how this environment's files regard it —
        without authenticating and without pinning anything.

        A server usually holds several host keys (rsa, ecdsa, ed25519) and offers the one
        the client prefers. Asked with default preferences it may show a different one
        from the key on file, and comparing those would call a trusted host a mismatch
        (measured on the test host). So when keys are on file, the server is asked for
        exactly those algorithms; only a server that cannot present any of them, or
        presents a different key of the same algorithm, is a mismatch.

        Status: ``trusted``, ``mismatch``, ``revoked``, ``ca`` (a CA is trusted: the
        connect decides), or ``unknown``."""
        trusted, has_ca, revoked = self._trusted_for_host(await self._addresses())
        algs: list[str] = []
        for k in trusted:
            alg = k.get_algorithm()
            algs += ["rsa-sha2-512", "rsa-sha2-256", "ssh-rsa"] if alg == "ssh-rsa" else [alg]
        kwargs = {"server_host_key_algs": list(dict.fromkeys(algs))} if algs else {}
        try:
            key = await asyncio.wait_for(
                asyncssh.get_server_host_key(self._host, self._port, config=None, **kwargs), timeout=timeout,
            )
        except asyncssh.KeyExchangeFailed:
            if not algs:
                raise
            # It holds none of the trusted algorithms: show what it does present.
            key = await asyncio.wait_for(
                asyncssh.get_server_host_key(self._host, self._port, config=None), timeout=timeout,
            )
        if key is None:
            raise RemoteHostKeyError(f"server {self._host} presented no host key")
        if any(k.public_data == key.public_data for k in revoked):
            # Explicitly revoked (@revoked): never offered for trust, whatever else is on file.
            return key, "revoked"
        if any(k.public_data == key.public_data for k in trusted):
            return key, "trusted"
        if has_ca:
            # A CA is trusted for this host: a replaced key with a valid certificate is
            # accepted by the connection, so this check does not overrule it.
            return key, "ca"
        if trusted:
            return key, "mismatch"
        return key, "unknown"

    async def pin_confirmed_host_key(self, fingerprint: str) -> str:
        """Pin the presented key, but only the one the person saw: the key is fetched
        again and must carry ``fingerprint``, and must still be ``unknown``. TOFU only —
        a strict environment's trust is the operator's own known_hosts, not Agora's to edit."""
        if self._host_key_mode != "tofu" or not self._known_hosts_path:
            raise RemoteHostKeyError("only a TOFU environment with a known_hosts file can record a key here")
        async with _pin_lock_for(self._known_hosts_path):
            key, trust = await self.presented_host_key()
            presented = key.get_fingerprint("sha256")
            if presented != fingerprint:
                raise RemoteHostKeyError(
                    f"the host now presents {presented}, not the {fingerprint} that was confirmed; nothing recorded")
            if trust == "trusted":
                return presented
            if trust != "unknown":
                raise RemoteHostKeyError(f"the host key is {trust} for this environment; nothing recorded")
            self._append_known_host_atomically(self._known_hosts_line(key))
            return presented

    async def _connect_once(self) -> "asyncssh.SSHClientConnection":
        if self.pin_on_connect:
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
    def _remote_cmd(
        argv: Sequence[str],
        cwd: Path | None,
        env: Mapping[str, str] | None = None,
        path_word: str | None = None,
    ) -> str:
        """argv -> a single remote command line. Every element is shlex.quote'd so a
        prompt or path can never break out into extra remote words. cwd is normalized
        to a POSIX path (the remote is Linux even when the client is Windows).

        ``env`` is prefixed as ``env NAME=VALUE ...`` rather than sent as an SSH env
        request, because a request is only advisory: sshd forwards a variable only if
        its ``AcceptEnv`` allows it, and the OpenSSH default is ``AcceptEnv LANG LC_*``
        — which dropped every ``AGORA_*`` silently, leaving the agent on the host with
        no run id at all (CR-22, measured against the test host: ``env | grep -c
        '^AGORA_'`` came back 0). Putting the assignments in the command makes the
        local and remote paths agree without asking anything of the server's config.
        Each ``NAME=VALUE`` is quoted as one word; the names come from the caller's
        allowlist, so nothing here can turn into an option or an extra command.

        ``path_word`` is a pre-built ``PATH=...`` word from :func:`_agent_path_word`
        (constant text plus a shlex-quoted literal), placed first."""
        quoted = " ".join(shlex.quote(str(a)) for a in argv)
        words = [path_word] if path_word else []
        if env:
            words += [shlex.quote(f"{k}={v}") for k, v in sorted(env.items())]
        if words:
            quoted = f"env {' '.join(words)} {quoted}"
        if cwd is not None:
            return f"cd {shlex.quote(_posix(cwd))} && {quoted}"
        return quoted

    async def _login_path_for(self, conn: "asyncssh.SSHClientConnection") -> str | None:
        """The remote user's login-shell PATH, probed once and cached. Runs the login
        shell (``-ilc``, so the profile and an interactive-guarded rc such as nvm's are
        read) and prints PATH between markers. Any failure — timeout, non-zero exit, no
        markers — yields None and the caller falls back to the remote ``$PATH``: the
        user bin dirs are still added, so this is an improvement, never a requirement."""
        if self._login_path is not _UNPROBED:
            return self._login_path  # type: ignore[return-value]
        script = f"printf '\\n{_PATH_BEGIN}%s{_PATH_END}\\n' \"$PATH\""
        cmd = f'"${{SHELL:-/bin/sh}}" -ilc {shlex.quote(script)} </dev/null'
        login_path: str | None = None
        proc = None

        async def _probe() -> bytes:
            # One deadline covers opening the channel as well as reading it: a server
            # that stalls on the exec request must not hold up the launch either.
            nonlocal proc
            proc = await conn.create_process(cmd, encoding=None)
            out, _ = await proc.communicate()
            return out

        try:
            out = await asyncio.wait_for(_probe(), timeout=LOGIN_PATH_PROBE_TIMEOUT)
            login_path = _parse_login_path((out or b"").decode("utf-8", errors="replace"))
        except Exception:
            # Optional by design: whatever goes wrong here, the launch proceeds on the
            # remote $PATH plus the user bin dirs.
            login_path = None
        finally:
            # A wait_for timeout (or a cancellation) does not stop the remote shell: a
            # profile that hangs would keep its process and channel open beside the
            # agent for the whole run. End it explicitly, whatever happened above.
            if proc is not None:
                try:
                    if proc.returncode is None:
                        proc.terminate()
                    proc.close()
                except Exception:
                    pass
        self._login_path = login_path
        return login_path

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
        # SECURITY: the caller passes {**os.environ, AGORA_*}. Forwarding os.environ
        # to the REMOTE host would ship arbitrary local secrets (the secret_ref
        # credential, AWS_*, tokens, proxy creds, ...). Treat the caller's env as
        # untrusted: forward ONLY an allowlist — the AGORA_* the agent needs — and
        # nothing else. (Widening the allowlist is a config-layer decision, U5; the
        # default is deny.)
        remote_env: dict[str, str] = {}
        if env is not None:
            remote_env = {
                k: v for k, v in env.items()
                if k in _LAUNCH_ENV_ALLOWLIST                   # only the explicit few
                and not _env_name_eq(k, self._secret_ref)       # defense-in-depth: never the credential
            }
        # launch is side-effecting: a connection failure is fail-closed (no retry).
        conn = await self._ensure_conn()
        # The assignments travel inside the command (see _remote_cmd): an SSH env
        # request is advisory and the server's AcceptEnv default drops them (CR-22).
        # PATH gains the login shell's PATH and the user bin dirs, so an agent CLI
        # installed under ~/.local/bin is found (CR-23).
        path_word = _agent_path_word(await self._login_path_for(conn))
        cmd = self._remote_cmd(argv, cwd, remote_env, path_word)
        proc = await conn.create_process(cmd, encoding=None)
        return RemoteProcessHandle(proc)

    async def open_terminal(self, line: str, cwd: str, *, rows: int, cols: int) -> "asyncssh.SSHClientProcess":
        """An interactive PTY on the host (CR-30 slice 2): ``line`` run in ``cwd`` by the
        remote login shell, with the same PATH a remote run gets (CR-23).

        ``line`` is the caller's — built only from an enumerated command, never from a
        request — and goes in after the directory change has succeeded: a project root
        that is gone or unreadable ends the terminal with that said, rather than leaving
        a home-directory shell under a label that names the project."""
        conn = await self._ensure_conn()
        path_word = _agent_path_word(await self._login_path_for(conn))
        root = _posix(cwd)
        fail = shlex.quote(f"进不了项目目录：{root}")
        script = f"cd {shlex.quote(root)} || {{ echo {fail}; exit 1; }}; export {path_word}; {line}"
        return await conn.create_process(
            f'"${{SHELL:-/bin/sh}}" -lc {shlex.quote(script)}',
            term_type="xterm-256color", term_size=(cols, rows), encoding="utf-8", errors="replace",
        )

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

    async def listdir(self, path: Path) -> list[str]:
        """The names in a remote directory, without '.' and '..'. SFTP errors propagate as
        the same OSError family ``read_file`` uses — never an empty list for "could not read"."""
        conn = await self._env._ensure_conn()
        async with conn.start_sftp_client() as sftp:
            try:
                names = await sftp.listdir(_posix(path))
            except asyncssh.SFTPNoSuchFile as exc:
                raise FileNotFoundError(_posix(path)) from exc
            except asyncssh.SFTPPermissionDenied as exc:
                raise PermissionError(_posix(path)) from exc
            except asyncssh.SFTPError as exc:
                raise OSError(f"cannot list remote directory {_posix(path)}: {exc}") from exc
        return [n for n in (_as_str(name) for name in names) if n not in (".", "..")]

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
