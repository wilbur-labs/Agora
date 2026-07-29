"""Export the reviewed AWS AI-DLC activation metadata from a pinned checkout."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agora.orchestration.aws_aidlc import AWS_AIDLC_V2_3_SOURCE_GRAPH  # noqa: E402


TARGET = (
    BACKEND_ROOT
    / "agora"
    / "orchestration"
    / "activation_manifests"
    / "aws_aidlc_v2_3_activation.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(upstream: Path) -> dict:
    graph = AWS_AIDLC_V2_3_SOURCE_GRAPH
    if not upstream.is_dir():
        raise ValueError(f"upstream checkout is unavailable: {upstream}")

    artifacts_by_role = {
        artifact.role: artifact
        for artifact in graph.source.artifacts
        if artifact.role != "stage" and artifact.role != "scope"
    }
    compiled_artifact = artifacts_by_role["compiled_graph"]
    compiled_path = upstream / compiled_artifact.path
    if _sha256(compiled_path) != compiled_artifact.content_sha256:
        raise ValueError("compiled source graph hash does not match the pinned source")

    for artifact in graph.source.artifacts:
        if _sha256(upstream / artifact.path) != artifact.content_sha256:
            raise ValueError(f"source artifact hash drift: {artifact.path}")

    raw_stages = json.loads(compiled_path.read_text(encoding="utf-8"))
    if not isinstance(raw_stages, list):
        raise ValueError("compiled source graph must be a Stage list")
    expected_keys = [stage.stage_key for stage in graph.stages]
    if [stage.get("slug") for stage in raw_stages] != expected_keys:
        raise ValueError("compiled source Stage order does not match the sealed graph")

    stages: list[dict] = []
    for raw, source_stage in zip(raw_stages, graph.stages, strict=True):
        if raw.get("for_each") != source_stage.for_each_artifact:
            raise ValueError(f"for-each drift for Stage {source_stage.stage_key}")
        sensor_ids = list(raw.get("sensors", []))
        sensor_specs = list(raw.get("sensors_applicable", []))
        if sensor_ids != [sensor.get("id") for sensor in sensor_specs]:
            raise ValueError(f"sensor manifest drift for Stage {source_stage.stage_key}")
        bound_sensors = []
        for sensor in sensor_specs:
            sensor_source = upstream / "dist" / "codex" / sensor["path"]
            bound_sensors.append(
                {
                    "id": sensor["id"],
                    "source_path": (
                        Path("dist") / "codex" / sensor["path"]
                    ).as_posix(),
                    "runtime_path": sensor["path"],
                    "matches": sensor["matches"],
                    "content_sha256": _sha256(sensor_source),
                }
            )

        required_outputs = list(raw.get("produces", []))
        optional_outputs = list(raw.get("optional_produces", []))
        if set(required_outputs) & set(optional_outputs):
            raise ValueError(f"required/optional output overlap for {source_stage.stage_key}")
        output_ids = set(required_outputs) | set(optional_outputs)
        produces_kinds = dict(raw.get("produces_kinds", {}))
        if set(produces_kinds) - output_ids:
            raise ValueError(f"unknown output kind binding for {source_stage.stage_key}")

        reviewer = raw.get("reviewer")
        reviewer_max_iterations = raw.get("reviewer_max_iterations", 0)
        if (reviewer is None) != (reviewer_max_iterations == 0):
            raise ValueError(f"reviewer iteration drift for {source_stage.stage_key}")

        stages.append(
            {
                "source_stage_key": source_stage.stage_key,
                "condition": raw["condition"],
                "inputs_text": raw["inputs"],
                "outputs_text": raw["outputs"],
                "lead_role": raw["lead_agent"],
                "support_roles": list(raw.get("support_agents", [])),
                "source_reviewer_role": reviewer,
                "source_reviewer_max_iterations": reviewer_max_iterations,
                "workspace_required": bool(raw.get("workspace_requires", False)),
                "for_each_artifact": raw.get("for_each"),
                "input_artifacts": list(raw.get("consumes", [])),
                "required_outputs": required_outputs,
                "optional_outputs": optional_outputs,
                "produces_kinds": produces_kinds,
                "sensors": bound_sensors,
            }
        )

    return {
        "schema_version": "1.0",
        "source_compiled_graph_sha256": compiled_artifact.content_sha256,
        "stages": stages,
    }


def render_manifest(payload: dict) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rendered = render_manifest(build_manifest(args.upstream.resolve()))
    if args.check:
        try:
            current = TARGET.read_text(encoding="utf-8")
        except OSError:
            return 1
        return 0 if current == rendered else 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
