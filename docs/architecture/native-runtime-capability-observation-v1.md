# Native runtime capability observation v1

Status: reviewed implementation baseline.

This increment adds one read-only observation boundary for the configured
native Codex, Claude Code, and Kiro adapters. It reports local installation and
native version-probe facts plus explicitly declared model and capability facts.
It does not select or substitute a runtime or model, mutate Control Plane state,
or change the sealed grouped Stage inventory and pinned route.

## Versioned contract

`NativeRuntimeCapabilityObservation@1.0` is a hash-sealed snapshot containing:

- a timezone-aware collection time, collector identity/version, and platform;
- a hash of the configured runtime registry;
- the exact ID, version, and hash of the checked-in capability declaration;
- one canonically ordered adapter observation; and
- an explicit `routing_authority: false` marker.

Each adapter observation separates four concepts:

1. whether its configured executable was found and could be resolved through
   Agora's audited no-shell launcher boundary;
2. whether a native version command returned one exact bounded version line;
3. which models were explicitly declared in local configuration; and
4. which capabilities are declared by the reviewed pinned routing policy.

Raw executable paths, configured command arguments, and version output are not
serialized. Their canonical or byte hashes bind collection provenance without
turning machine-local paths or output into a routing input.

## Collection semantics

Default adapters use these bounded native probes:

```text
codex --version
claude --version
kiro-cli --version
```

The probes execute directly without a shell, receive no Task prompt, close
stdin, remove inherited proxy variables, capture at most 8 KiB from each output
stream, and time out after 10 seconds. A successful exit with one bounded
non-control version line is
`exact`. Missing executables, unsupported Windows wrappers, timeouts, launch
errors, non-zero exits, empty output, and malformed lines remain
`unavailable`; Agora never guesses a version.

The output hash covers only the bounded captured bytes. Oversized output is
marked unavailable and retains the bounded-prefix hash; if post-stop pipe
drain itself times out, no output hash is claimed. Failure of one parallel
probe cancels and awaits every sibling probe so no observation error can leave
another adapter process running.

A custom runtime command has no implicit version probe. Its operator must
configure a bounded `version_command` explicitly, otherwise installation may
be observed while version remains `not_configured`. Version commands allow at
most 32 non-empty arguments, contain no `{prompt}`, and run through the same
audited direct-executable/Windows-wrapper resolver.

## Declared models and capabilities

Models come only from
`orchestration.runtimes.<adapter>.declared_models`. The list is bounded to 50
unique model identifiers and is reported as `declared`; an empty list is
`unavailable`. Agora does not query a provider catalog, infer a default model
from human output, or claim that a declared model is currently authenticated or
serviceable.

Capabilities come from the hash-covered
`agora-foundation-routing-policy@1.0` declarations. The observation records
that policy hash, but it is not fresh capability discovery and cannot satisfy,
override, or repair the dispatch policy. The existing pinned policy continues
to derive routing from checked-in facts in the Run-claim transaction.

## Read-only CLI boundary

`agora task capabilities` emits the checked and sealed JSON contract directly.
The command builds only the configured runtime registry and version probes. It
does not initialize Task storage, create a database, persist the observation,
or bump the unified Task projection schema.

## Deferred boundaries

Live provider/model catalog discovery, authentication or serviceability probes,
dynamic runtime/model substitution, feeding observations into routing, policy
migration, authenticated HTTP exposure, historical observation persistence,
the missing authoritative AI-DLC graph, parallel/DAG routing, and Task
Workbench UI remain separate reviewed increments.
