"""Run existing live Agent contracts using the existing owned storage fixture."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'Agent_Server/tests'))
from test_execution_worker_cli import isolated_runtime, child_environment
from src.application.projects.project_store import PostgresProjectStore


async def run():
    output = Path(os.environ.get('QA_API_AGENT_REGRESSION_DIR',
        str(root / 'docs/upgrade_2026-10-03/p2-api-agent-worker-isolated-regression'))).resolve()
    assert output.is_relative_to(root / 'docs/upgrade_2026-10-03')
    output.mkdir(exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='qa-api-agent-regression-') as temporary:
        directory = Path(temporary)
        try:
            async with isolated_runtime(directory) as (settings, processes, _, _, _):
                await PostgresProjectStore(settings).initialize()
                processes.env = child_environment(settings)
                processes.env['RUN_LIVE_POSTGRES_TESTS'] = '1'
                child = processes.start('agent-regression', '-m', 'pytest',
                    'tests/test_agent_task_dispatch.py', 'tests/test_coordinator_durable_children.py',
                    'tests/test_coordinator_claim_snapshot_save.py', 'tests/test_lifespan_resource_cleanup.py',
                    'tests/test_redis_worker_entrypoint.py', '-q', '--tb=short',
                    '--junitxml=' + str(output / 'regression.xml'))
                result = await processes.wait(child, timeout=180)
        finally:
            for p in directory.iterdir():
                if p.suffix in {'.stdout', '.stderr'} or p.name == 'docker.json':
                    shutil.copyfile(p, output / p.name)
    return result


raise SystemExit(asyncio.run(run()))
