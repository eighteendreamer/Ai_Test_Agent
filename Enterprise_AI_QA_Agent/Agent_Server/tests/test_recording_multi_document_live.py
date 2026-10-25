"""One recording session spanning two real documents: action→response must stay
associated with the document that produced it."""
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


PAGE_A = '''<!doctype html><meta charset="utf-8"><button id="load-a">Load A</button>
<section id="result"></section><script>
document.querySelector('#load-a').onclick = async () => {
  const response = await fetch('/api/a');
  document.querySelector('#result').textContent = `A-${response.status}-response`;
};
</script>'''

PAGE_B = '''<!doctype html><meta charset="utf-8"><button id="load-b">Load B</button>
<section id="result"></section><script>
document.querySelector('#load-b').onclick = async () => {
  const response = await fetch('/api/b');
  document.querySelector('#result').textContent = `B-${response.status}-response`;
};
</script>'''


class MultiDocHandler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self):  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        type(self).requests.append(self.path)
        if path == '/doc-a':
            body, status = PAGE_A.encode(), 200
        elif path == '/doc-b':
            body, status = PAGE_B.encode(), 200
        elif path == '/api/a':
            body, status = b'{"document":"a"}', 200
        elif path == '/api/b':
            body, status = b'{"document":"b"}', 201
        else:
            body, status = b'not found', 404
        self.send_response(status)
        self.send_header('Content-Type', 'text/html' if path.startswith('/doc-') else 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECORDING_MULTI_DOCUMENT") != "1",
    reason="actual Chromium/API/MySQL/PG/Redis/Memgraph")
async def test_one_recording_keeps_cross_document_actions_and_responses_associated(tmp_path, request):
    evidence = Path(os.environ["RECORDING_MULTI_DOCUMENT_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    MultiDocHandler.requests = []
    server = ThreadingHTTPServer(('127.0.0.1', 0), MultiDocHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    graph = None
    project_id = None
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding='utf-8')

    def finalize():
        server.shutdown()
        server.server_close()
        if graph and project_id:
            graph.execute_write("MATCH (n {project_id:$project_id}) DETACH DELETE n",
                {"project_id": project_id})
            graph.close()
        for path in tmp_path.iterdir():
            if path.suffix in {'.stdout', '.stderr'}:
                shutil.copyfile(path, evidence / path.name)

    request.addfinalizer(finalize)
    async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
        graph = MemgraphRuntimeProvider(settings)
        port = free_port()
        api = processes.start('recording-multi-doc-api', '-m', 'uvicorn', 'src.main:app',
            '--host', '127.0.0.1', '--port', str(port))
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}/api/v1', timeout=60) as client:
            await wait_api(client, api)
            response = await client.post('/projects', json={
                'project_key': 'multi-document-' + uuid4().hex[:12],
                'name': '跨文档录制验收'})
            response.raise_for_status()
            project_id = response.json()['id']
            async with async_playwright() as playwright:
                cdp = free_port()
                browser = await playwright.chromium.launch(headless=True,
                    args=[f'--remote-debugging-port={cdp}'])
                try:
                    page = await browser.new_page()
                    response = await client.post('/recordings', json={
                        'project_id': project_id, 'entry_url': base_url + '/doc-a',
                        'driver': {'kind': 'cdp-attach', 'endpoint': f'http://127.0.0.1:{cdp}'}})
                    response.raise_for_status()
                    recording_id = response.json()['id']
                    async with asyncio.timeout(15):
                        while (await client.get(f'/recordings/{recording_id}')).json()['status'] != 'ready':
                            await asyncio.sleep(.1)
                    (await client.post(f'/recordings/{recording_id}/control',
                        json={'action': 'start'})).raise_for_status()
                    await page.goto(base_url + '/doc-a')
                    await page.locator('#load-a').click()
                    await page.wait_for_function(
                        "document.querySelector('#result').textContent.includes('A-200-response')")
                    await page.goto(base_url + '/doc-b')
                    await page.locator('#load-b').click()
                    await page.wait_for_function(
                        "document.querySelector('#result').textContent.includes('B-201-response')")
                    await page.wait_for_timeout(700)
                    response = await client.post(f'/recordings/{recording_id}/control',
                        json={'action': 'stop'})
                    response.raise_for_status()
                    recorded = response.json()
                    save('recording.json', recorded)
                finally:
                    await browser.close()
            with postgres_connect(settings) as connection, connection.cursor() as cursor:
                cursor.execute(f'SELECT seq,type,payload FROM {settings.database.postgres_recording_event_table} '
                    'WHERE recording_id=%s ORDER BY seq', (recording_id,))
                rows = [dict(row) for row in cursor.fetchall()]
            save('pg-events.json', rows)
            clicks = [row for row in rows if row['type'] == 'click']
            responses = [row for row in rows if row['type'] == 'page_response']
            assert len(clicks) == 2, [row['type'] for row in rows]
            click_docs = [row['payload']['page']['recording_scope']['document_id'] for row in clicks]
            click_urls = [row['payload']['page']['url'] for row in clicks]
            assert len(set(click_docs)) == 2, click_docs
            assert click_urls[0].endswith('/doc-a') and click_urls[1].endswith('/doc-b'), click_urls
            associated = {}
            for response_row in responses:
                observed = response_row['payload'].get('page_effect', {}).get('response') or {}
                network = observed.get('network') or []
                paths = {item.get('pathname') for item in network}
                if not paths & {'/api/a', '/api/b'}:
                    continue
                document_id = response_row['payload']['page']['recording_scope']['document_id']
                associated[document_id] = {
                    'response_sequence': response_row['seq'],
                    'response_document_id': document_id,
                    'action_local_seqs': observed.get('action_local_seqs'),
                    'network': network,
                    'paths': sorted(paths),
                }
            assert set(associated) == set(click_docs), (associated, click_docs)
            for click in clicks:
                scope = click['payload']['page']['recording_scope']
                document_id = scope['document_id']
                entry = associated[document_id]
                assert entry['action_local_seqs'] == [scope['local_seq']], (entry, scope)
                expected = '/api/a' if click['payload']['page']['url'].endswith('/doc-a') else '/api/b'
                assert expected in entry['paths'], entry
            quality = recorded['finalize_metrics']['recording_quality']
            assert quality['unresolved_response_associations'] == [], quality
            pairs = {(item['response_event_sequence'], tuple(item['action_event_sequences']))
                for item in quality['response_action_associations']}
            assert pairs == {
                (associated[click_docs[0]]['response_sequence'], (clicks[0]['seq'],)),
                (associated[click_docs[1]]['response_sequence'], (clicks[1]['seq'],)),
            }, quality
            assert quality['omission']['status'] == 'verified', quality['omission']
            receipt = recorded['finalize_metrics']['finish_receipt']
            assert receipt['protocol'] == 'recording-scopes-v2', receipt
            scopes = receipt['scopes']
            # The recorder also registers the pre-navigation document; the two
            # documents that carried actions must be the ones with events.
            delivered = [scope for scope in scopes if (scope.get('next_seq') or 0) > 0]
            assert len(delivered) == 2, scopes
            assert set(scope['document_id'] for scope in delivered) == set(click_docs), (scopes, click_docs)
            assert {scope['document_url'].rsplit('/', 1)[-1] for scope in delivered} == {
                'doc-a', 'doc-b'}, delivered
            assert receipt['frames_unconfirmed'] == 0, receipt
            assert all(scope['pg_verified'] and scope['confirmed'] for scope in scopes), scopes
            save('facts.json', {
                'fixture_requests': MultiDocHandler.requests,
                'click_documents': click_docs,
                'click_urls': click_urls,
                'associations': associated,
                'quality_associations': quality['response_action_associations'],
                'unresolved': quality['unresolved_response_associations'],
                'scopes': scopes,
                'model_calls': 0,
            })
