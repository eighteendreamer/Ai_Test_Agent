"""Real network response correlation for recorder actions (metadata only)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import async_playwright

from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.postgres_runtime import postgres_connect
from test_execution_worker_cli import free_port, isolated_runtime, wait_api


HTML = '''<!doctype html><meta charset="utf-8"><button id="load">Load</button>
<section id="result"></section><script>
document.querySelector('#load').onclick = async () => {
  const first = await fetch('/api/data?q=redaction-probe');
  const missing = await fetch('/api/missing?q=redaction-probe');
  const xhr = new XMLHttpRequest();
  xhr.open('GET', '/api/xhr?q=redaction-probe');
  xhr.onload = () => { document.querySelector('#result').textContent =
    `${first.status}/${missing.status}/${xhr.status}:network response`; };
  xhr.send();
};
</script>'''


class FixtureHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self):  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        type(self).requests.append(self.path)
        if path == '/fixture':
            body, status = HTML.encode(), 200
        elif path == '/api/data':
            body, status = b'{"value":"response-body-probe"}', 200
        elif path == '/api/missing':
            body, status = b'{"error":"missing"}', 404
        elif path == '/api/xhr':
            body, status = b'{"value":"xhr-response-body"}', 201
        else:
            body, status = b'not found', 404
        self.send_response(status)
        self.send_header('Content-Type', 'text/html' if path == '/fixture' else 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_NETWORK") != "1", reason="actual Chromium/API/MySQL/PG/Redis/Memgraph")
async def test_network_responses_are_correlated_without_query_or_body_leaks(tmp_path, request):
    evidence = Path(os.environ["RECORDING_NETWORK_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    FixtureHandler.requests = []
    server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    graph = None
    project_id = None

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    def finalize():
        server.shutdown()
        server.server_close()
        if graph and project_id:
            graph.execute_write("MATCH (n {project_id:$project_id}) DETACH DELETE n", {"project_id": project_id})
            graph.close()
        for path in tmp_path.iterdir():
            if path.suffix in {'.stdout', '.stderr'} or path.name in {'docker.json', 'postgres-finally.json'}:
                shutil.copyfile(path, evidence / path.name)

    request.addfinalizer(finalize)
    fixture_url = f"http://127.0.0.1:{server.server_address[1]}/fixture"
    async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
        graph = MemgraphRuntimeProvider(settings)
        port = free_port()
        api = processes.start('recording-network-api', '-m', 'uvicorn', 'src.main:app', '--host', '127.0.0.1', '--port', str(port))
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}/api/v1', timeout=60) as client:
            await wait_api(client, api)
            response = await client.post('/projects', json={'project_key': 'network-' + uuid4().hex[:12], 'name': 'Network response acceptance'})
            response.raise_for_status()
            project_id = response.json()['id']
            async with async_playwright() as playwright:
                cdp = free_port()
                browser = await playwright.chromium.launch(headless=True, args=[f'--remote-debugging-port={cdp}'])
                try:
                    page = await browser.new_page()
                    response = await client.post('/recordings', json={'project_id': project_id, 'entry_url': fixture_url,
                        'driver': {'kind': 'cdp-attach', 'endpoint': f'http://127.0.0.1:{cdp}'}})
                    response.raise_for_status()
                    recording_id = response.json()['id']
                    async with asyncio.timeout(15):
                        while (await client.get(f'/recordings/{recording_id}')).json()['status'] != 'ready':
                            await asyncio.sleep(.1)
                    (await client.post(f'/recordings/{recording_id}/control', json={'action': 'start'})).raise_for_status()
                    await page.goto(fixture_url)
                    await page.locator('#load').click()
                    await page.locator('#result').wait_for(state='visible')
                    await page.wait_for_function("document.querySelector('#result').textContent.includes('network response')")
                    await page.wait_for_timeout(700)
                    response = await client.post(f'/recordings/{recording_id}/control', json={'action': 'stop'})
                    response.raise_for_status()
                    recorded = response.json()
                    save('recording.json', recorded)
                finally:
                    await browser.close()
            with postgres_connect(settings) as connection, connection.cursor() as cursor:
                cursor.execute(f'SELECT seq,type,payload FROM {settings.database.postgres_recording_event_table} WHERE recording_id=%s ORDER BY seq', (recording_id,))
                rows = [dict(row) for row in cursor.fetchall()]
            save('pg-events.json', rows)
            clicks = [row for row in rows if row['type'] == 'click']
            responses = [row for row in rows if row['type'] == 'page_response']
            assert len(clicks) == 1 and responses
            observed = responses[-1]['payload']['page_effect']['response']
            network = observed['network']
            facts = {'fixture_requests': FixtureHandler.requests, 'action_local_seqs': observed['action_local_seqs'],
                'network': network, 'query_or_body_leaked': any('probe' in json.dumps(item) for item in network), 'model_calls': 0}
            save('facts.json', facts)
            assert observed['network_correlation'] == 'instrumented_fetch_xhr_completion'
            assert observed['network_observation_status'] == 'observed'
            assert observed['action_local_seqs'] == [clicks[0]['payload']['page']['recording_scope']['local_seq']]
            assert {(item['pathname'], item['status'], item['ok']) for item in network} == {
                ('/api/data', 200, True), ('/api/missing', 404, False), ('/api/xhr', 201, True)}
            assert not facts['query_or_body_leaked']
            quality = recorded['finalize_metrics']['recording_quality']
            assert quality['response_action_associations'] == [{
                'response_event_sequence': responses[-1]['seq'],
                'action_event_sequences': [clicks[0]['seq']],
                'association': 'temporal_after_actions',
                'network_correlation': 'instrumented_fetch_xhr_completion',
                'network_observation_status': 'observed',
                'network_outcome_counts': {'response': 3},
            }]
            assert quality['unresolved_response_associations'] == []
