from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
from pathlib import Path
import subprocess

import httpx
import psutil
import pytest
from fastapi import FastAPI
from docx import Document

from test_case_planning import environment, LIVE
from test_document_parsers import actual_docx
from src.api.routes.document_git import router
from src.application.documents.git_process import GitDocumentProcess
from src.application.documents.git_service import CONFIG_KEY, GitDocumentService
from src.runtime import limited_parser_process
from src.runtime.document_git_worker import GitReader
from src.runtime.limited_parser_process import ParserProcessError
from src.schemas.document_git import GitDocumentImportRequest, GitDocumentPolicy, GitDocumentResolveRequest, GitDocumentSettings
from src.schemas.project import ProjectCreateRequest


def git(root, *arguments):
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=False)
    assert result.returncode == 0, (arguments, result.stdout, result.stderr)
    return result.stdout


def commit(root, message):
    git(root, "-c", "core.autocrlf=false", "add", "--all")
    git(root, "-c", "user.name=QA fixture", "-c", "user.email=qa@example.test", "commit", "-m", message)
    return git(root, "rev-parse", "HEAD").decode().strip()


def test_binary_timeout_diagnostic_keeps_hash_without_document_bytes(tmp_path, monkeypatch, capsys):
    partial = b"private document content from interrupted blob"

    def interrupted(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=partial, stderr=b"reader interrupted")

    monkeypatch.setattr("src.runtime.document_git_worker.subprocess.run", interrupted)
    reader = GitReader(str(tmp_path), GitDocumentPolicy())
    with pytest.raises(ValueError, match="超过时间限制"):
        reader.run("cat-file", "blob", "a" * 40, binary=True)
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert "stdout" not in diagnostic
    assert partial.decode() not in captured.err
    assert diagnostic["stdout_size_bytes"] == len(partial)
    assert diagnostic["stdout_sha256"] == hashlib.sha256(partial).hexdigest()
    assert diagnostic["stderr"] == "reader interrupted"
    assert diagnostic["timeout"] is True


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "真实仓库"
    root.mkdir()
    git(root, "init")
    folder = root / "需求 文档"
    folder.mkdir()
    path = folder / "规则[1].md"
    original = "# 订单\r\n有效期：7 天。\r\n".encode("utf-8-sig")
    path.write_bytes(original)
    docx = folder / "订单.docx"
    actual_docx(docx)
    binary = docx.read_bytes()
    (root / "large.md").write_bytes(b"# original\n" + b"large line\n" * 10000)
    (root / "lfs.md").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "a"*64 + "\nsize 99\n")
    first = commit(root, "actual v1")
    second_bytes = "# 订单\n有效期：9 天。\n".encode()
    path.write_bytes(second_bytes)
    revised = Document(docx)
    revised.tables[0].cell(1, 1).text = "9 天"
    revised.save(docx)
    second = commit(root, "actual v2")
    path.write_bytes(b"uncommitted workspace must not be read")
    docx.write_bytes(b"staged bytes must not be read")
    git(root, "add", "--", docx.relative_to(root).as_posix())
    return root, first, second, original, second_bytes, binary


def state(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()}


def evidence(name, value, originals=()):
    if not os.getenv("QA_DOCUMENT_GIT_EVIDENCE_DIR"):
        return
    folder = Path(os.environ["QA_DOCUMENT_GIT_EVIDENCE_DIR"]) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "verification.json").write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for filename, raw in originals:
        (folder / filename).write_bytes(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
async def test_real_bare_and_full_object_format(tmp_path, object_format):
    root = tmp_path / object_format
    root.mkdir()
    git(root, "init", "--object-format=" + object_format)
    raw = b"# exact object bytes\r\n"
    (root / "requirements.md").write_bytes(raw)
    first = commit(root, "actual format")
    bare = tmp_path / (object_format + ".git")
    git(root, "clone", "--bare", str(root), str(bare))
    before = state(bare)
    result = await GitDocumentProcess().run({"operation":"read", "root_path":str(bare),
        "reference":first, "path":"requirements.md"}, GitDocumentPolicy())
    assert result["bare"] and len(result["commit"]) == {"sha1":40,"sha256":64}[object_format]
    assert base64.b64decode(result["content_base64"]) == raw
    assert state(bare) == before


@pytest.mark.asyncio
async def test_real_replace_refs_are_ignored_and_special_modes_rejected(repository):
    root, first, second, raw, *_ = repository
    git(root,"replace",first,second)
    before=state(root)
    result=await GitDocumentProcess().run({"operation":"read","root_path":str(root),
        "reference":first,"path":"需求 文档/规则[1].md"},GitDocumentPolicy())
    assert base64.b64decode(result["content_base64"])==raw and state(root)==before
    blob=result["blob"]
    git(root,"update-index","--add","--cacheinfo",f"120000,{blob},link.md")
    git(root,"update-index","--add","--cacheinfo",f"160000,{second},module")
    git(root,"-c","user.name=QA fixture","-c","user.email=qa@example.test","commit","-m","special modes")
    special=git(root,"--no-replace-objects","rev-parse","HEAD").decode().strip()
    for path in ("link.md","module"):
        with pytest.raises(ParserProcessError,match="普通文件"):
            await GitDocumentProcess().run({"operation":"resolve","root_path":str(root),
                "reference":special,"path":path},GitDocumentPolicy())


@pytest.mark.asyncio
async def test_real_missing_blob_is_local_failure_without_repository_mutation(repository):
    root,first,*_=repository
    resolved=await GitDocumentProcess().run({"operation":"resolve","root_path":str(root),
        "reference":first,"path":"需求 文档/规则[1].md"},GitDocumentPolicy())
    blob=resolved["blob"]
    # Delete only this fixture's own loose object to reproduce an incomplete local repository.
    object_path=root/".git"/"objects"/blob[:2]/blob[2:]
    assert object_path.is_file() and object_path.resolve().is_relative_to(root.resolve())
    object_path.chmod(stat.S_IWRITE)
    object_path.unlink()
    before=state(root)
    with pytest.raises(ParserProcessError,match="cat-file") as caught:
        await GitDocumentProcess().run({"operation":"read","root_path":str(root),
            "reference":first,"path":"需求 文档/规则[1].md"},GitDocumentPolicy())
    assert "fatal" in caught.value.stderr and state(root)==before


@pytest.mark.parametrize("changes", [
    {"path":"../outside.md"},{"path":"a/../b.md"},{"path":"a//b.md"},
    {"path":"/absolute.md"},{"path":"C:\\private.md"},{"path":"a\x00.md"},
    {"document_id":"doc"},{"expected_parent_version_id":"parent"},{"commit":"main"},
])
def test_import_rejects_unsafe_paths_moving_commit_and_incomplete_target(changes):
    with pytest.raises(ValueError):
        GitDocumentImportRequest.model_validate({"request_id":"r","repository_id":"repo","config_revision":1,
            "commit":"a"*40,"blob":"b"*40,"path":"normal.md",**changes})


@pytest.mark.asyncio
async def test_real_fixed_objects_dirty_workspace_literal_paths_and_binary(repository):
    root, first, second, raw, new, binary = repository
    before = state(root)
    process = GitDocumentProcess()
    outputs = []
    for path, expected in (("需求 文档/规则[1].md", raw), ("需求 文档/订单.docx", binary)):
        request = {"operation": "resolve", "root_path": str(root), "reference": first, "path": path}
        resolved = await process.run(request, GitDocumentPolicy())
        received = await process.run({**request, "operation": "read", "expected_blob": resolved["blob"]}, GitDocumentPolicy())
        assert base64.b64decode(received["content_base64"]) == expected
        assert received["commit"] == first and received["sha256"] == hashlib.sha256(expected).hexdigest()
        assert any(c["command"][0] == "cat-file" for c in received["commands"])
        assert all(c["exit_code"] == 0 for c in received["commands"])
        outputs.append({k:v for k,v in received.items() if k != "content_base64"})
    assert state(root) == before
    for path in ("需求 文档", "需求 文档/规则*.md", "missing.md", "lfs.md"):
        with pytest.raises(ParserProcessError):
            await process.run({"operation": "resolve", "root_path": str(root), "reference": first, "path": path}, GitDocumentPolicy())
    with pytest.raises(ParserProcessError, match="根目录"):
        await process.run({"operation": "validate", "root_path": str(root / "需求 文档")}, GitDocumentPolicy())
    with pytest.raises(ParserProcessError, match="blob"):
        await process.run({"operation": "read", "root_path": str(root), "reference": first,
            "path": "需求 文档/规则[1].md", "expected_blob": "a"*40}, GitDocumentPolicy())
    assert state(root) == before
    evidence("fixed-objects", {"outputs":outputs,"before":before,"after":state(root)},
        (("v1.md",raw),("v2.md",new),("original.docx",binary)))


@pytest.mark.asyncio
async def test_real_failed_git_preserves_error_and_returns_no_partial_success(repository):
    root, *_ = repository
    with pytest.raises(ParserProcessError) as caught:
        await GitDocumentProcess().run({"operation":"resolve","root_path":str(root),
            "reference":"missing-reference","path":"large.md"}, GitDocumentPolicy())
    assert caught.value.exit_code == 1
    assert '"exit_code": 128' in caught.value.stderr
    assert "rev-parse" in caught.value.stderr and "fatal" in caught.value.stderr
    evidence("failed-read", {"exit_code":caught.value.exit_code,"stdout":caught.value.stdout,"stderr":caught.value.stderr})


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,reason", [
    ({"timeout_seconds":0.000001},"时间限制"),({"max_memory_bytes":1},"内存限制"),
    ({"max_cpu_seconds":0.000001},"CPU 限制"),({"max_output_bytes":1},"大小限制"),
    ({"max_blob_bytes":1},"文件大小限制"),
])
async def test_real_git_limits_reap_child_and_temporary_files(repository, monkeypatch, changes, reason):
    root, first, *_ = repository
    popen = subprocess.Popen
    observed = []
    def track(command, **kwargs):
        process = popen(command, **kwargs)
        observed.append((process,psutil.Process(process.pid),Path(command[-2]).parent))
        return process
    monkeypatch.setattr(limited_parser_process.subprocess,"Popen",track)
    with pytest.raises(ParserProcessError,match=reason):
        await GitDocumentProcess().run({"operation":"read","root_path":str(root),"reference":first,
            "path":"large.md"}, GitDocumentPolicy(**changes))
    process,handle,temporary = observed[0]
    assert process.poll() is not None and not handle.is_running() and not temporary.exists()


@pytest.mark.asyncio
async def test_real_git_cancellation_waits_for_os_cleanup(repository, monkeypatch):
    root,first,*_ = repository
    popen,observed = subprocess.Popen,[]
    def track(command,**kwargs):
        process=popen(command,**kwargs)
        observed.append((process,psutil.Process(process.pid),Path(command[-2]).parent))
        return process
    monkeypatch.setattr(limited_parser_process.subprocess,"Popen",track)
    task=asyncio.create_task(GitDocumentProcess().run({"operation":"read","root_path":str(root),
        "reference":first,"path":"large.md"},GitDocumentPolicy()))
    async with asyncio.timeout(5):
        while not observed:
            await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process,handle,temporary=observed[0]
    assert process.poll() is not None and not handle.is_running() and not temporary.exists()


def body(resolution,request_id,**updates):
    origin=resolution["origin"]
    return {"request_id":request_id,**{key:origin[key] for key in
        ("repository_id","config_revision","commit","blob","path")},**updates}


@pytest.mark.asyncio
@pytest.mark.parametrize("live",[False,LIVE])
async def test_concurrent_replays_conflicts_and_rebound_document_do_not_read_source(tmp_path,repository,live):
    root,first,*_=repository
    async with environment(tmp_path,live) as env:
        project=await env.projects.create(ProjectCreateRequest(project_key="git-concurrent",name="并发 Git"))
        other=await env.projects.create(ProjectCreateRequest(project_key="git-rebound",name="改绑目标"))
        service=GitDocumentService(config_store=env.configs,project_service=env.projects,documents=env.docs)
        await service.save_settings(project.id,GitDocumentSettings(repositories=[
            {"id":"repo","name":"repo","root_path":str(root)}]))
        resolved=await service.resolve(project.id,GitDocumentResolveRequest(repository_id="repo",reference=first,
            path="需求 文档/规则[1].md"))
        request=GitDocumentImportRequest.model_validate(body(resolved.model_dump(mode="json"),"concurrent-original"))
        one,two=await asyncio.gather(service.import_document(project.id,request),service.import_document(project.id,request))
        assert one==two and len(await env.docs.list_versions(one.document.id))==1
        assert (await env.docs._catalog_store.get_submission(one.document.id,request.request_id)).source_request_hash==service.request_hash(project.id,request)
        # Config changes reject fresh imports before consulting unavailable disk.
        await service.save_settings(project.id,GitDocumentSettings())
        moved=root.with_name(root.name+"-unavailable")
        root.rename(moved)
        try:
            with pytest.raises(ValueError,match="配置已变化"):
                await service.import_document(project.id,request.model_copy(update={"request_id":"fresh-stale"}))
            assert await service.import_document(project.id,request)==one
            with pytest.raises(ValueError,match="不同"):
                await service.import_document(project.id,request.model_copy(update={"title":"changed target title"}))
            await env.docs.update_document(one.document.id,project_id=other.id)
            with pytest.raises((ValueError,KeyError),match="项目"):
                await service.import_document(project.id,request)
            with pytest.raises((ValueError,KeyError),match="项目"):
                await service.get_submission(project.id,request.request_id)
        finally:
            moved.rename(root)


@pytest.mark.asyncio
@pytest.mark.parametrize("live",[False,LIVE])
async def test_registered_source_import_versions_project_isolation_and_replay(tmp_path,repository,live):
    root,first,second,raw,new,binary=repository
    async with environment(tmp_path,live) as env:
        project=await env.projects.create(ProjectCreateRequest(project_key="git-owner",name="Git 来源"))
        other=await env.projects.create(ProjectCreateRequest(project_key="git-other",name="其他项目"))
        service=GitDocumentService(config_store=env.configs,project_service=env.projects,documents=env.docs)
        app=FastAPI()
        app.state.document_git_service=service
        app.include_router(router,prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test/api/v1") as client:
            base=f"/projects/{project.id}"
            config={"repositories":[{"id":"repo","name":"已登记仓库","root_path":str(root)}]}
            # Even a populated global setting does not authorize another project.
            env.configs.save(CONFIG_KEY,config)
            default=(await client.get(base+"/document-git-settings")).json()
            assert default["revision"]==0 and default["payload"]["repositories"]==[]
            response=await client.put(base+"/document-git-settings",json=config)
            assert response.status_code==200,response.text
            assert response.json()["scope_key"]==project.id
            saved_config=env.configs.get(CONFIG_KEY,scope_key=project.id)
            assert saved_config.payload["repositories"][0]["root_path"]==str(root.resolve())
            query={"repository_id":"repo","reference":first,"path":"需求 文档/规则[1].md"}
            assert (await client.post(f"/projects/{other.id}/document-git/resolve",json=query)).status_code==409
            resolved=await client.post(base+"/document-git/resolve",json=query)
            assert resolved.status_code==200,resolved.text
            initial=body(resolved.json(),"import-first")
            created=await client.post(base+"/document-git/import",json=initial)
            assert created.status_code==200,created.text
            value=created.json()
            doc_id,v1=value["document"]["id"],value["version"]["id"]
            assert value["source_origin"]==resolved.json()["origin"]
            assert value["version"]["snapshot"]["source_origin"]==value["source_origin"]
            assert (await env.docs.read_asset(doc_id,value["document"]["original_asset"]["id"]))[1]==raw
            frozen_v1=await env.docs.get_version(doc_id,v1)
            next_source=(await client.post(base+"/document-git/resolve",json={**query,"reference":second})).json()
            update=body(next_source,"import-second",document_id=doc_id,expected_parent_version_id=v1)
            result=await client.post(base+"/document-git/import",json=update)
            assert result.status_code==200,result.text
            v2=result.json()["version"]["id"]
            assert result.json()["version"]["parent_version_id"]==v1
            assert (await env.docs.get_version(doc_id,v1))==frozen_v1
            assert (await env.docs.read_asset(doc_id,result.json()["document"]["original_asset"]["id"]))[1]==new
            assert (await client.post(base+"/document-git/import",json={**update,"request_id":"wrong-parent"})).status_code==409
            (root/"same.md").write_bytes(new)
            third=commit(root,"same bytes from another path")
            same_source=(await client.post(base+"/document-git/resolve",json={**query,
                "reference":third,"path":"same.md"})).json()
            unchanged=body(same_source,"same-bytes",document_id=doc_id,expected_parent_version_id=v2)
            same=await client.post(base+"/document-git/import",json=unchanged)
            assert same.status_code==200 and not same.json()["created_new"]
            assert same.json()["version"]["id"]==v2
            assert same.json()["source_origin"]["commit"]==third
            assert same.json()["version"]["snapshot"]["source_origin"]["commit"]==second
            assert len(await env.docs.list_versions(doc_id))==2
            assert (await client.post(base+"/document-git/import",json={**initial,"title":"changed"})).status_code==409
            assert (await client.post(f"/projects/{other.id}/document-git/import",json=update)).status_code==404
            # Lost response recovery requires neither a still-present source nor an active project.
            await client.put(base+"/document-git-settings",json={"repositories":[]})
            moved=root.with_name(root.name+"-removed")
            root.rename(moved)
            try:
                await env.projects.archive(project.id)
                app.state.document_git_service=GitDocumentService(config_store=env.configs,
                    project_service=env.projects,documents=env.docs)
                replay=await client.post(base+"/document-git/import",json=initial)
                assert replay.status_code==200 and replay.json()==value
                reread=await client.get(base+"/document-git/submissions",params={"request_id":"import-first"})
                assert reread.status_code==200 and reread.json()==value
                same_replay=await client.post(base+"/document-git/import",json=unchanged)
                assert same_replay.status_code==200 and same_replay.json()==same.json()
                assert (await client.post(base+"/document-git/import",json={**initial,"request_id":"new"})).status_code==409
                evidence("real-stores" if live else "local-stores",{"initial":value,"v2":result.json(),
                    "same_bytes":same.json(),"restored":replay.json(),"settings":saved_config.payload},
                    (("v1.md",raw),("v2.md",new)))
            finally:
                moved.rename(root)
