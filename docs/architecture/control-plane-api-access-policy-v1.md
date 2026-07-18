# Control Plane API Access Policy v1

Status: approved for the bounded Control Plane API increment

Approved: 2026-07-18

Scope: API planning and implementation after the reviewed Control Plane v2
Registry; this policy does not authorize UI work or repository-wide
invalidation routes.

## 1. Authentication boundary

The first API slice uses fail-closed Bearer authentication. Credentials are
configured outside the repository through secret references; raw bearer tokens
must never be checked in, returned, logged, stored in Task decisions, or copied
into audit events.

Configuration maps each credential reference to:

- one stable principal identity;
- an explicit set of Control Plane permissions;
- an explicit set of project memberships.

An empty, missing, malformed, or unresolved configuration authorizes nobody.
Token verification must use a constant-time comparison over derived values.
Request actors are derived only from the verified principal and never accepted
from a request body, query parameter, or caller-selected header.

The bounded permissions are:

```text
control_plane.read
control_plane.register
control_plane.evaluate
control_plane.approve
```

Every route requires both the relevant permission and membership in the path
project. Missing or invalid credentials return `401`; a verified principal
without permission or membership returns `403`; resource lookup after an
authorized scope check uses a non-enumerating `404` for project/Task mismatch.

## 2. Invalidation boundary

`ControlPlaneStore.invalidate_inventory` remains a repository/ref-wide
reconciliation operation. It must not be exposed by the bounded Task-scoped
API increment and must not be narrowed by filtering its effects to a path Task.

A future repository reconciler may expose it only through a separate,
explicitly approved permission and command boundary that reports every affected
project and Task. That future design is outside this policy version.

Clients must never submit a Stage dependency graph. Until an authoritative,
versioned methodology supplies that graph, repository-wide downstream
invalidation remains unavailable through the API.

## 3. Projection and audit boundary

The first Task projection exposes authoritative Task, Stage, Gate, Artifact,
Evidence, Approval, Attention, budget, and next-safe-action dimensions
separately. It must not invent a completion percentage or synthetic result.

Authentication and authorization failures must not disclose raw credentials,
configured secret references, SQL details, filesystem paths, or identifiers
outside the authorized scope. Successful mutations derive their audit actor
from the verified principal.
