from __future__ import annotations

import os

import pytest

from src.application.documents.parser_config_service import DocumentParserConfigService, CONFIG_KEY
from src.application.documents.parser_process import DocumentParseProcess
from src.schemas.document_parsing import DocumentParsingSettings
from src.schemas.project import ProjectCreateRequest
from test_case_import_service import _environment
from test_document_parsers import actual_pdf

LIVE = pytest.param(True, marks=pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_PARSERS") != "1",
    reason="isolated actual MySQL/PostgreSQL/RustFS"))


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_parser_strategy_uses_versioned_mysql_project_config_and_fixed_preparation(tmp_path, live):
    async with _environment(tmp_path, live) as env:
        _, projects, _, _, configs, _, _, _, _ = env
        service = DocumentParserConfigService(config_store=configs, project_service=projects)
        await service.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="parse-config", name="解析配置"))
        other = await projects.create(ProjectCreateRequest(project_key="other-parse", name="其他项目"))
        path = tmp_path / "actual.pdf"
        actual_pdf(path)
        frozen, options, capability = await service.prepare(project.id, path.name)
        assert frozen.scope_key == "global" and options.parser_id == "pdf" and not capability.tables
        settings = DocumentParsingSettings()
        settings.options.pdf_extraction_mode = "layout"
        settings.policy.timeout_seconds = 15
        saved = await service.save_settings(project.id, settings)
        assert saved.scope_key == project.id and saved.payload.options.pdf_extraction_mode == "layout"
        assert (await service.get_settings(other.id)).payload.options.pdf_extraction_mode == "plain"
        assert frozen.payload.options.pdf_extraction_mode == "plain" and frozen.payload.policy.timeout_seconds == 60
        assert configs.get(CONFIG_KEY, scope_key=project.id).payload["policy"]["timeout_seconds"] == 15
        result = await DocumentParseProcess().parse(filename=path.name, content=path.read_bytes(), policy=frozen.payload.policy, options=options)
        assert result.options.pdf_extraction_mode == "plain" and "7 days" in result.text
        restarted = DocumentParserConfigService(config_store=configs, project_service=projects)
        await restarted.initialize()
        assert (await restarted.get_settings(project.id)).revision == saved.revision
        bad = settings.model_copy(deep=True)
        bad.parser_by_extension[".pdf"] = "docling_http"
        with pytest.raises(ValueError, match="不可用"):
            await service.save_settings(project.id, bad)
        assert (await service.get_settings(project.id)) == saved
        await projects.archive(project.id)
        with pytest.raises(ValueError, match="archived"):
            await service.save_settings(project.id, settings)
        with pytest.raises(ValueError, match="archived"):
            await service.prepare(project.id, path.name)
