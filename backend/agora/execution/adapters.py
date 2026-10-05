"""Safe argv builders for supported coding-agent CLIs."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import AdapterCapability, RemoteTarget


#: SSH host-key verification mode (contract config-and-tests.py). ``strict`` is the
#: NFR-1 default and is fully implemented; ``tofu`` is accepted at config time but
#: fails closed at connect (CR-6 — full TOFU awaits a real host to verify against).
HostKeyMode = Literal["strict", "tofu"]
#: Where the SSH credential comes from (task adr/0001). ``env_var`` is the only one a
#: config.yaml adapter uses; the other two arrive with a registered environment.
CredentialSourceName = Literal["env_var", "key_path", "keyring"]


class RemoteAdapterConfig(BaseModel):
    """SSH remote section of an execution adapter (FROZEN contract:
    ``contracts/config-and-tests.py``). Present only when ``bridge_mode == 'ssh'``.

    Credentials are NEVER stored here: ``secret_ref`` names the env var holding the
    key path or password (NFR-2, SECURITY-12) — no plaintext secret / host / IP is
    committed (the repo is public). ``extra='forbid'`` fails closed at load time on
    an unknown key rather than silently ignoring a misspelled or unsupported field.
    """

    ssh_host: str
    ssh_port: int = 22
    ssh_user: str
    remote_workspace: str                       # per-adapter remote worktree dir
    remote_project_root: str                    # user pre-created; Agora inits repo here (D2 form-2)
    secret_ref: str                             # env-var name, e.g. AGORA_SSH_KEY_<adapter>
    host_key_mode: HostKeyMode = "strict"       # NFR-1 default strict; tofu opt-in
    known_hosts_path: Optional[str] = None      # used/required under strict
    cleanup_worktree: bool = False              # Q5=C: default keep; true = teardown after handoff
    # Set only when this config comes from a registered environment (CR-4): which
    # credential source to use, and the registry id that keys a keyring password.
    credential_source: CredentialSourceName = "env_var"
    environment_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")   # unknown keys fail-closed at load


#: Where a provider's session id comes from.
#:
#: ``caller`` — Agora chooses the id and hands it to the CLI (``claude
#: --session-id <uuid>``). The control plane can record the session before the
#: process starts, so a run that dies in its first second still leaves a session
#: to resume, and nothing has to be parsed out of a child process's stdout.
#:
#: ``observed`` — the provider mints the id and announces it in its event stream
#: (codex ``thread.started.thread_id``, kiro ``metadata.sessionId``). Agora can
#: only learn it by reading, so a first turn whose output is lost leaves no
#: session id, and the honest reading of that is "unknown" rather than "none".
SESSION_ID_CALLER = "caller"
SESSION_ID_OBSERVED = "observed"

#: Which parser reads this adapter's stdout. Each value has exactly one reader in
#: ``transcript.py``; a value with no reader is refused at registry build time
#: rather than silently producing an unreadable transcript.
EVENT_FORMAT_CODEX_JSONL = "codex_jsonl"
EVENT_FORMAT_CLAUDE_STREAM_JSON = "claude_stream_json"
EVENT_FORMAT_KIRO_STREAM_JSON = "kiro_stream_json"
EVENT_FORMAT_PLAIN = "plain"

EVENT_FORMATS = frozenset({
    EVENT_FORMAT_CODEX_JSONL,
    EVENT_FORMAT_CLAUDE_STREAM_JSON,
    EVENT_FORMAT_KIRO_STREAM_JSON,
    EVENT_FORMAT_PLAIN,
})

#: The argv placeholders a command template carries.
SESSION_ID_PLACEHOLDER = "{session_id}"
PROMPT_PLACEHOLDER = "{prompt}"

#: ``model_flag`` in :data:`DEFAULT_ADAPTERS` — ``(flag, index)`` — declares that this
#: CLI takes a model override, and *where* in its argv the flag belongs. Only an
#: adapter whose flag has been run against the installed CLI carries one; a
#: ``model`` configured for an adapter without it is refused at load rather than
#: guessed at, because the position is not portable between CLIs (``claude --model X
#: -p …`` takes it before the prompt flag, and a subcommand CLI does not).
#:
#: This exists because `execution.adapters.<name>.command` is the wrong tool for
#: pinning a model: replacing the whole argv opts that adapter out of the verified
#: default *and* out of its resume command (see the config.yaml note), which is how
#: turn two of a conversation quietly stopped being one. A one-flag insertion keeps
#: both. Pinning matters in practice: unpinned, the CLI follows whatever default the
#: user's own `~/.claude/settings.json` holds, so a dispatched run can hit a model
#: limit the person never chose for it and exit in three seconds (2026-09-21).
MODEL_FLAG_KEY = "model_flag"


@dataclass(frozen=True)
class ExecutionAdapter:
    name: str
    command_template: tuple[str, ...]
    workspace_key: str
    bridge_mode: str = "cli"
    app_server_command: tuple[str, ...] = ("codex", "app-server", "--listen", "stdio://")
    #: The command that continues an existing provider session. Empty means this
    #: adapter cannot continue one, which is a different answer from "the resume
    #: failed" and is reported as such rather than quietly starting a new session.
    resume_template: tuple[str, ...] = ()
    session_id_source: str = SESSION_ID_OBSERVED
    event_format: str = EVENT_FORMAT_PLAIN
    #: SSH remote config, present IFF ``bridge_mode == 'ssh'`` (contract
    #: config-and-tests.py). ``None`` for local adapters. The dispatcher reads this
    #: to build a RemoteEnvironment/RemoteWorkspaceProvisioner instead of the local
    #: ones; a local adapter leaves it ``None`` and runs on ``LocalEnvironment``.
    remote: Optional[RemoteAdapterConfig] = None

    def build_command(
        self, prompt: str, *, session_id: str | None = None, resume: bool = False
    ) -> list[str]:
        """Expand only the dedicated argv elements; never invoke a shell.

        ``resume`` selects the resume template rather than appending an id to the
        start command: the two are not the same shape. ``codex exec resume``
        rejects ``--sandbox`` outright (verified against codex 0.149.1 on
        2026-09-08), so a resume built from the start argv would die on its first
        flag; and ``claude`` swaps ``--session-id`` for ``--resume``.
        """
        template = self.command_template
        if resume:
            if not self.resume_template:
                raise ValueError(f"adapter {self.name} cannot resume a session")
            if not session_id:
                raise ValueError(f"adapter {self.name} resume needs a session id")
            template = self.resume_template
        elif self.session_id_source == SESSION_ID_CALLER and not session_id:
            raise ValueError(f"adapter {self.name} needs a caller-chosen session id")
        return [self._expand(part, prompt, session_id) for part in template]

    @staticmethod
    def _expand(part: str, prompt: str, session_id: str | None) -> str:
        if part == PROMPT_PLACEHOLDER:
            return prompt
        if part == SESSION_ID_PLACEHOLDER:
            if session_id is None:
                # Unreachable through build_command, which refuses a missing id
                # before expanding either template. Raised rather than asserted
                # so a future caller gets the reason instead of a literal
                # "{session_id}" argv element reaching a CLI.
                raise ValueError("session id placeholder expanded with no session id")
            return session_id
        return part

    def stored_command(self, *, resume: bool = False) -> list[str]:
        """Return audit-safe argv without expanding user prompt content."""
        if self.bridge_mode == "codex_app_server":
            return list(self.app_server_command)
        if resume and self.resume_template:
            return list(self.resume_template)
        return list(self.command_template)

    def can_resume(self) -> bool:
        return bool(self.resume_template)

    def workspace(self, workspaces: dict[str, Path]) -> Path:
        if self.workspace_key not in workspaces:
            raise KeyError(f"Project has no {self.workspace_key} workspace")
        return workspaces[self.workspace_key].expanduser().resolve()

    def capability(self) -> AdapterCapability:
        """What this adapter can do, and where it runs (W3-a U5): a remote adapter
        names its host, account and project root — never the credential reference."""
        base = self._capability_base()
        if self.remote is None:
            return base
        return base.model_copy(update={"remote": RemoteTarget(
            host=self.remote.ssh_host, port=self.remote.ssh_port, user=self.remote.ssh_user,
            project_root=self.remote.remote_project_root,
        )})

    def _capability_base(self) -> AdapterCapability:
        if self.bridge_mode == "codex_app_server":
            return AdapterCapability(
                name=self.name, execution_mode=self.bridge_mode, attention_mode="bidirectional",
                supports_tool_approval=True, supports_user_questions=False,
                detail="Stable command and file approvals are delivered through Codex app-server; user questions remain experimental.",
            )
        details = {
            "claude": "Claude Code runs through the subscription CLI. Hooks may capture attention, but Agora cannot return delayed decisions to this process.",
            "kiro": "Kiro CLI 2.19 streams ACP events on the v2 engine; it exposes no verified machine-readable approval channel, so tools are pre-authorised.",
            "codex": "Codex uses the non-interactive CLI fallback; no approval delivery channel is active.",
        }
        return AdapterCapability(
            name=self.name, execution_mode="cli", attention_mode="capture_only",
            supports_tool_approval=False, supports_user_questions=False,
            detail=details.get(self.name, "CLI execution with no verified bidirectional attention channel."),
        )


# Every command below was verified against the installed CLIs on 2026-09-08
# (codex 0.149.1 / claude 2.1.246 / kiro-cli 2.19.1). Three of the flags are not
# obvious, and each was found by a CLI refusing the obvious spelling:
#
# - ``codex exec resume`` accepts **no** ``--sandbox`` (nor ``-C/--cd``), so the
#   sandbox policy travels as a config override instead. Without it a resumed turn
#   runs under whatever the user's config says, which for editing work is the
#   difference between the product working and every follow-up turn being unable
#   to touch a file.
# - ``claude -p --output-format stream-json`` **requires** ``--verbose``; it exits
#   1 with exactly that message otherwise.
# - ``kiro-cli --output-format stream-json`` **requires** ``--agent-engine v2``;
#   the v1 engine refuses it and reports the refusal as a ``runError`` event.
#
# ``--ephemeral`` (codex) and ``--no-session-persistence`` (claude) are gone on
# purpose: both discard the session file, which is the thing a second turn
# resumes, and ``--no-session-persistence`` is mutually exclusive with resume.
DEFAULT_ADAPTERS: dict[str, dict[str, Any]] = {
    "codex": {
        "command": [
            "codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
            "--json", "{prompt}",
        ],
        "resume_command": [
            "codex", "exec", "resume", "{session_id}", "--skip-git-repo-check",
            "-c", "sandbox_mode=\"workspace-write\"", "--json", "{prompt}",
        ],
        "session_id_source": SESSION_ID_OBSERVED,
        "event_format": EVENT_FORMAT_CODEX_JSONL,
        "workspace_key": "codex",
    },
    "claude": {
        "command": [
            "claude", "-p", "{prompt}", "--session-id", "{session_id}",
            "--output-format", "stream-json", "--include-partial-messages",
            "--verbose", "--permission-mode", "auto",
        ],
        "resume_command": [
            "claude", "-p", "{prompt}", "--resume", "{session_id}",
            "--output-format", "stream-json", "--include-partial-messages",
            "--verbose", "--permission-mode", "auto",
        ],
        "session_id_source": SESSION_ID_CALLER,
        "event_format": EVENT_FORMAT_CLAUDE_STREAM_JSON,
        "workspace_key": "claude",
        "model_flag": ("--model", 1),
    },
    "kiro": {
        "command": [
            "kiro-cli", "chat", "--agent-engine", "v2", "--output-format", "stream-json",
            "--no-interactive", "--trust-all-tools", "{prompt}",
        ],
        "resume_command": [
            "kiro-cli", "chat", "--agent-engine", "v2", "--output-format", "stream-json",
            "--no-interactive", "--trust-all-tools", "--resume-id", "{session_id}", "{prompt}",
        ],
        "session_id_source": SESSION_ID_OBSERVED,
        "event_format": EVENT_FORMAT_KIRO_STREAM_JSON,
        "workspace_key": "kiro",
    },
}


def _validated_argv(name: str, field: str, command: Any) -> tuple[str, ...]:
    if not isinstance(command, list) or not command or any(not isinstance(part, str) for part in command):
        raise ValueError(f"execution.adapters.{name}.{field} must be a non-empty string list")
    if command.count(PROMPT_PLACEHOLDER) != 1:
        raise ValueError(
            f"execution adapter {name} {field} must contain exactly one {PROMPT_PLACEHOLDER} element"
        )
    return tuple(command)


def _with_model(
    name: str, command: tuple[str, ...], model: str, model_flag: tuple[str, int] | None
) -> tuple[str, ...]:
    """Insert ``--model <model>`` at the position this CLI takes it.

    Refuses rather than guesses for an adapter with no declared position: an
    inserted flag that lands in the wrong place either fails loudly at launch or,
    worse, is swallowed as a positional argument by a CLI that takes one.
    """
    if model_flag is None:
        raise ValueError(
            f"execution.adapters.{name}.model is not supported: no verified --model "
            f"position for this adapter. Pin the model in the CLI's own configuration."
        )
    flag, index = model_flag
    return command[:index] + (flag, model) + command[index:]


def build_adapter_registry(config: dict[str, Any]) -> dict[str, ExecutionAdapter]:
    configured = config.get("execution", {}).get("adapters", {})
    registry: dict[str, ExecutionAdapter] = {}
    for name, defaults in DEFAULT_ADAPTERS.items():
        values = configured.get(name, {})
        if values.get("enabled", True) is False:
            # A disabled adapter never enters the registry, so its config is never
            # consumed — its ssh/command/etc. are deliberately NOT validated. The
            # point of load-time validation is to prevent a config that LOOKS active
            # but is wrong; a disabled adapter makes no such claim. (An invalid ssh
            # section under enabled:false is inert, not a silent acceptance of remote
            # execution that will happen.)
            continue
        overridden = "command" in values
        command = _validated_argv(name, "command", values.get("command", defaults["command"]))
        declared_source = values.get("session_id_source")
        if declared_source is None and overridden:
            # A config that replaces the command is describing a different
            # executable — a stub in tests, a wrapper script in an install — and
            # the placeholder in *its* argv is the declaration of whether that
            # executable takes a caller-chosen id. Read from the override rather
            # than inherited from the CLI it replaced, whose flags do not apply.
            session_id_source = (
                SESSION_ID_CALLER if SESSION_ID_PLACEHOLDER in command else SESSION_ID_OBSERVED
            )
        else:
            session_id_source = str(declared_source or defaults["session_id_source"])
            if session_id_source not in {SESSION_ID_CALLER, SESSION_ID_OBSERVED}:
                raise ValueError(f"execution adapter {name} has unsupported session_id_source")
            if (SESSION_ID_PLACEHOLDER in command) != (session_id_source == SESSION_ID_CALLER):
                # Stated and contradicted. The two must agree in both directions:
                # a caller-chosen id with no placeholder is recorded by the
                # control plane and never reaches the CLI, so Agora would believe
                # in a session the provider never heard of; a placeholder with no
                # caller-chosen id has nothing to expand.
                raise ValueError(
                    f"execution adapter {name} command must carry {SESSION_ID_PLACEHOLDER} "
                    f"exactly when session_id_source is {SESSION_ID_CALLER}"
                )
        if "resume_command" in values:
            raw_resume = values["resume_command"]
        elif overridden:
            # No default resume command for a replaced executable. Inheriting one
            # would send the *second* turn of a conversation to a completely
            # different program: a config that stubs ``claude`` with ``echo``
            # would resume by running the real claude CLI. Without a resume
            # command the adapter reports that it cannot continue a session,
            # which is both true and safe.
            raw_resume = None
        else:
            raw_resume = defaults.get("resume_command")
        if raw_resume is None:
            resume_command: tuple[str, ...] = ()
        else:
            resume_command = _validated_argv(name, "resume_command", raw_resume)
            if resume_command.count(SESSION_ID_PLACEHOLDER) != 1:
                raise ValueError(
                    f"execution adapter {name} resume_command must contain exactly one "
                    f"{SESSION_ID_PLACEHOLDER} element"
                )
        if "event_format" in values:
            event_format = str(values["event_format"])
        elif overridden:
            # A replaced executable does not emit the CLI's event format. Assuming
            # it did would point a JSON Lines reader at, say, `echo`'s output —
            # the transcript would show nothing it understood, and usage capture
            # would be *claimed* (the capability payload reads this field) while
            # producing nothing. Plain text is the honest default: the stdout is
            # shown verbatim and no usage is asserted. Declare `event_format`
            # explicitly for a wrapper that really does emit a known format.
            event_format = EVENT_FORMAT_PLAIN
        else:
            event_format = str(defaults["event_format"])
        if event_format not in EVENT_FORMATS:
            raise ValueError(f"execution adapter {name} has unsupported event_format")
        app_server_command = values.get(
            "app_server_command", ["codex", "app-server", "--listen", "stdio://"]
        )
        if not isinstance(app_server_command, list) or not app_server_command or any(
            not isinstance(part, str) or not part for part in app_server_command
        ):
            raise ValueError(f"execution.adapters.{name}.app_server_command must be a non-empty string list")
        if "model" in values:
            model = values["model"]
            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"execution.adapters.{name}.model must be a non-empty string")
            if overridden or "resume_command" in values:
                # The override's argv is the declaration of what that executable
                # takes; the default's flag position says nothing about it. Put the
                # flag in the override rather than have this insert one blind.
                #
                # `resume_command` counts on its own, not only alongside `command`:
                # it can be overridden by itself, and then turn two runs a different
                # program from turn one. Checking only `command` inserted the default
                # CLI's flag position into that wrapper's argv — where it is either
                # rejected or, worse, swallowed as a positional argument.
                raise ValueError(
                    f"execution.adapters.{name}.model cannot be combined with a command or "
                    f"resume_command override — put the flag in the override's own argv"
                )
            model_flag = defaults.get(MODEL_FLAG_KEY)
            command = _with_model(name, command, model, model_flag)
            if resume_command:
                # Both, always. A model pinned on the first turn and absent on the
                # second is a conversation that changes model halfway through, which
                # is exactly the surprise pinning exists to prevent.
                resume_command = _with_model(name, resume_command, model, model_flag)
        registry[name] = ExecutionAdapter(
            name=name,
            command_template=command,
            workspace_key=str(values.get("workspace_key", defaults["workspace_key"])),
            bridge_mode=str(values.get("bridge_mode", "cli")),
            app_server_command=tuple(app_server_command),
            resume_template=resume_command,
            session_id_source=session_id_source,
            event_format=event_format,
        )
        if registry[name].bridge_mode not in {"cli", "codex_app_server", "ssh"}:
            raise ValueError(f"execution adapter {name} has unsupported bridge_mode")
        if registry[name].bridge_mode == "codex_app_server" and name != "codex":
            raise ValueError("codex_app_server bridge_mode is supported only for codex")
        # SSH remote config: required exactly when bridge_mode is 'ssh', forbidden
        # otherwise. A missing 'ssh' section on an ssh adapter, or an 'ssh' section
        # on a local one, is a config error caught at load — not a runtime surprise
        # when the dispatcher tries to pick an environment. Presence is keyed on
        # 'ssh' in values (not values.get, which cannot tell an absent key from an
        # explicit ssh: null); RemoteAdapterConfig's extra='forbid' rejects unknown
        # keys and pydantic reports a missing required field. Both surface as a
        # ValueError naming the adapter.
        has_ssh_section = "ssh" in values
        ssh_values = values.get("ssh")
        if registry[name].bridge_mode == "ssh":
            if not isinstance(ssh_values, dict):
                raise ValueError(f"execution adapter {name} with bridge_mode 'ssh' needs an 'ssh' config section")
            # The two registry-only fields are not config.yaml's to set: a keyring or
            # key-path source needs the registry's id, and an environment_id would make
            # a configured remote pose as a registered one. Refused at load, not at the
            # first run (review).
            for registry_only in ("credential_source", "environment_id"):
                if registry_only in ssh_values:
                    raise ValueError(
                        f"execution adapter {name} ssh config may not set {registry_only!r}: "
                        f"it belongs to a registered remote environment"
                    )
            try:
                remote = RemoteAdapterConfig(**ssh_values)
            except ValidationError as exc:
                raise ValueError(f"execution adapter {name} ssh config invalid: {exc}") from exc
            registry[name] = replace(registry[name], remote=remote)
        elif has_ssh_section:
            raise ValueError(f"execution adapter {name} has an 'ssh' section but bridge_mode is not 'ssh'")
    return registry
