"""Observe committed checkpoints, then run the unchanged production CLI.

Only the owned test process installs these observers. No model response, query
result, lease or deadline is replaced; the parent kills after the real commit.
"""
import asyncio
import json
import os
from pathlib import Path
import runpy
import sys

from src.application.test_cases.planning_sources import ProjectPlanningSources
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore


def install(directory, fault):
    root = Path(directory)
    original_save = PostgresPlanningTaskStore.save_checkpoint
    original_read = ProjectPlanningSources.read_scene_basis

    async def observe_read(self, project, basis):
        result = await original_read(self, project, basis)
        with (root / "scene-cli-reads.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"project_id":project, "basis":basis.model_dump(mode="json"),
                "evidence_ids":[e.id for e in result[0]], "pid":os.getpid()}, ensure_ascii=False) + "\n")
        return result

    async def observe_save(self, claim, checkpoint, *, message):
        await original_save(self, claim, checkpoint, message=message)
        pending = checkpoint.pending_decision
        if (fault and checkpoint.phase == "query" and pending and pending.queries[0].kind == "scene"
            and checkpoint.query_index == (0 if fault == "pending-read" else 1)):
            marker = root / "scene-cli-commit-marker.tmp"
            marker.write_text(json.dumps({
                "pid":os.getpid(), "parent_pid":os.getppid(), "task_id":claim.task.id, "attempt_id":claim.attempt_id,
                "checkpoint":checkpoint.model_dump(mode="json"), "sql_commit_returned":True},
                ensure_ascii=False, indent=2), encoding="utf-8")
            marker.replace(root / "scene-cli-commit-marker.json")
            # Keep the original heartbeat running until the parent kills us.
            await asyncio.Event().wait()

    ProjectPlanningSources.read_scene_basis = observe_read
    PostgresPlanningTaskStore.save_checkpoint = observe_save


if __name__ == "__main__":
    install(os.environ["QA_SCENE_CLI_DIR"], os.environ.get("QA_SCENE_CLI_FAULT", ""))
    sys.argv = ["src.cli.run_case_planning_worker", *sys.argv[1:]]
    runpy.run_module("src.cli.run_case_planning_worker", run_name="__main__", alter_sys=True)
