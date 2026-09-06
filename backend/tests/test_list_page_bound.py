"""The `limit` bound the frontend's paging depends on, and the mirror of it.

Every list route declares `limit: int = Query(default=100, ge=1, le=500)`, and the
frontend reads one row beyond the page it shows, so that "there is more" is a fact rather
than an inference drawn from a full page (`frontend/src/lib/paged.ts`). That probe has to
fit inside this bound, so the frontend's largest page is `500 - 1` and it asks for 500.

Which makes 500 part of the contract, not an implementation detail. It is pinned here
because the frontend cannot check it: `node --test` never calls the API, and tsc, eslint
and `next build` have no opinion about a query bound. The first version of that probe
asked for 501 at four sites; every one of those reads would have returned 422 on every
load, and the whole frontend gate stayed green. Review caught it by reading the routers.

Read off the route declarations rather than through a client, deliberately: the assertion
is about what the routes *declare*, and going through `TestClient` would run the app
lifespan, which this suite's conftest exists to keep away from real state.

**And it reads the frontend's own declaration, rather than a second copy of the number.**
The first version of these guards asserted `MAX_PAGE + PROBE == SERVER_PAGE_MAX` on one
side and `bound == 500` on the other, which review pointed out is two tautologies: editing
only `paged.ts` to `SERVER_PAGE_MAX = 501` left both suites green and reproduced exactly
the 422 they were written to prevent. A guard that cannot fail on the change it describes
is worse than none, because it reads as proof. So the comparison is made here, across the
languages, between the routes and the literal `paged.ts` declares.

If the bound is lowered, or if either side of the mirror is edited alone, this fails --
which is the point, because the frontend's page size is derived from it and would
otherwise start 422-ing silently. Raising both together is a deliberate two-file change.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from annotated_types import Le

from agora.attention.router import router as attention_router
from agora.execution.router import router as execution_router
from agora.tasks.router import router as tasks_router

# What `frontend/src/lib/paged.ts` calls SERVER_PAGE_MAX. Its MAX_PAGE is this minus the
# one probe row, and `readPage` asks for MAX_PAGE + 1 == this.
FRONTEND_SERVER_PAGE_MAX = 500

# The routers whose list routes the frontend pages against. The routes themselves are
# *discovered* below rather than listed: an allowlist of paths passes when a fifth
# bounded route is added and then consumed with the shared frontend maximum, which is
# the same "covers what I remembered" defect this slice exists to remove.
#
# Two things are still narrower than "every bounded list read", and are stated rather
# than implied: this list of *routers* (a new module goes unwatched, and no import-time
# hook would catch it), and a limit supplied by a `Depends(...)` rather than declared on
# the endpoint (discovery reads the signature, so it would not see one).
WATCHED_ROUTERS = [
    (tasks_router, "tasks"),
    (attention_router, "attention"),
    (execution_router, "execution"),
]

PAGED_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "paged.ts"


def bounded_list_routes() -> list[tuple[str, str, int]]:
    """Every GET route on the watched routers that takes a `limit`, with its bound.

    Discovered, not listed, so a route added later is covered without anyone
    remembering to add it here.
    """
    found: list[tuple[str, str, int]] = []
    for router, name in WATCHED_ROUTERS:
        for route in router.routes:
            if "GET" not in getattr(route, "methods", set()):
                continue
            for parameter in inspect.signature(route.endpoint).parameters.values():
                # By name *or* by alias: `page_size: int = Query(..., alias="limit")` is
                # the same query parameter to every caller, and only looking for the
                # Python name would skip it -- the kind of gap that makes a discovery
                # narrower than the inventory it claims to be.
                query = parameter.default
                alias = getattr(query, "alias", None)
                if parameter.name != "limit" and alias != "limit":
                    continue
                bounds = [
                    int(constraint.le)
                    for constraint in getattr(query, "metadata", [])
                    if isinstance(constraint, Le)
                ]
                assert bounds, (
                    f"{name} {route.path} takes a `limit` with no upper bound; the "
                    f"frontend pages against these bounds, so an unbounded one is its "
                    f"own problem"
                )
                found.append((name, route.path, bounds[0]))
    assert found, "no bounded list route found at all -- this test has stopped testing"
    return found


# An active top-level declaration of the mirror, and nothing else. Deliberately strict:
# it must be the whole line, the value must be plain digits, and the semicolon must be
# there. Anything else -- a type annotation, a numeric separator, an expression -- is a
# failure rather than something to interpret.
DECLARATION = re.compile(r"export const SERVER_PAGE_MAX = (\d+);")


def declared_in_paged_ts() -> int:
    """The bound `frontend/src/lib/paged.ts` believes the server enforces.

    Read out of that file rather than duplicated here, so that editing either side
    alone fails this test. A literal in this file would only ever agree with itself.

    **Two earlier versions of this function had false-green paths, both found by
    review, and they are the reason it no longer pattern-matches at all.** The first
    searched anywhere, so `// export const SERVER_PAGE_MAX = 500` above an active
    `= 501` was read as the declaration. The second anchored to a line start but still
    took a numeric prefix and still tolerated other spellings, so `= 500_000` read as
    `500`, and a block-comment line plus an active `SERVER_PAGE_MAX: number = 501`
    passed as "exactly one match".

    So this collects *active declaration lines* -- a comment line starts with `//` or
    `*` and is not one -- requires exactly one, and requires it to be exactly the
    strict form. Any other spelling fails loudly instead of being parsed.

    What it still cannot see: a declaration built across lines or re-exported from
    elsewhere. That is stated rather than covered.
    """
    lines = [
        line.strip() for line in PAGED_TS.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("export const SERVER_PAGE_MAX")
    ]
    assert len(lines) == 1, (
        f"expected exactly one active SERVER_PAGE_MAX declaration in {PAGED_TS}, "
        f"found {len(lines)}: {lines}"
    )
    match = DECLARATION.fullmatch(lines[0])
    assert match, (
        f"SERVER_PAGE_MAX is declared as {lines[0]!r}, which this guard will not "
        f"interpret. It must read exactly `export const SERVER_PAGE_MAX = <digits>;` "
        f"so that the number compared against the routes is unambiguous"
    )
    return int(match.group(1))


@pytest.mark.parametrize(
    "name,path,bound",
    bounded_list_routes(),
    ids=[f"{name}{path}" for name, path, _ in bounded_list_routes()],
)
def test_list_route_bound_matches_the_frontend_mirror(name, path, bound):
    assert bound == declared_in_paged_ts(), (
        f"GET {path} ({name}) bounds `limit` at {bound}, but paged.ts pages against "
        f"{declared_in_paged_ts()} and asks for exactly that many -- the two sides "
        f"disagreeing makes every maximum-sized read a 422"
    )


def test_the_mirror_is_the_number_this_suite_was_written_against():
    """A sanity floor, so a coordinated edit of both sides is still a deliberate act
    rather than something the parametrised test above would quietly accept."""
    assert declared_in_paged_ts() == FRONTEND_SERVER_PAGE_MAX
