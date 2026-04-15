"""
接口文件管理 API 路由

上传 Markdown 接口文件到 MinIO，解析并入库索引

作者: 程序员Eighteen
"""
import hashlib
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel, HttpUrl

from database.connection import get_db, ApiSpec, ApiSpecVersion, ApiEndpoint, resolve_project_context, get_active_project_by_id
from Api_Spec.minio_client import get_minio_client, get_bucket_name, ensure_bucket
from Api_Spec.parser import parse_api_markdown
from Api_Spec.importers import PostmanImporter, OpenAPIImporter, HARImporter, CurlImporter, ApiFoxImporter, URLImporter
from Api_Spec.converter import to_markdown

router = APIRouter(
    prefix="/api/specs",
    tags=["接口文件管理"]
)


class ImportFromURLRequest(BaseModel):
    """URL 导入请求"""
    url: HttpUrl
    service_name: Optional[str] = None
    base_url: Optional[str] = None
    enable_llm_enhance: bool = False
    project_id: Optional[int] = None


class ImportFromTextRequest(BaseModel):
    """文本粘贴导入请求"""
    content: str
    format_hint: Optional[str] = None  # curl, json, markdown
    service_name: Optional[str] = None
    base_url: Optional[str] = None
    project_id: Optional[int] = None


@router.get("/list")
def list_spec_versions(
    service_name: Optional[str] = None,
    project_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """获取接口文件列表"""
    # 如果没有指定项目，使用默认项目
    if project_id is None:
        default_project = resolve_project_context(db, project_id)
        if not default_project:
            # 没有启用的项目，返回空列表
            return {"success": True, "data": [], "total": 0}
        project_id = default_project.id
    else:
        # 验证指定的项目是否启用（不报错，只是过滤）
        project = get_active_project_by_id(db, project_id)
        if not project:
            # 项目未启用，返回空列表
            return {"success": True, "data": [], "total": 0}
    
    query = db.query(ApiSpecVersion).filter(ApiSpecVersion.project_id == project_id)

    if service_name:
        spec_ids = [s.id for s in db.query(ApiSpec).filter(
            ApiSpec.service_name == service_name,
            ApiSpec.project_id == project_id
        ).all()]
        if spec_ids:
            query = query.filter(ApiSpecVersion.spec_id.in_(spec_ids))
        else:
            return {"success": True, "data": [], "total": 0}

    total = query.count()
    versions = query.order_by(ApiSpecVersion.id.desc()).limit(limit).offset(offset).all()

    data = []
    for v in versions:
        spec = db.query(ApiSpec).filter(ApiSpec.id == v.spec_id).first()
        data.append({
            "id": v.id,
            "spec_id": v.spec_id,
            "project_id": v.project_id,
            "service_name": spec.service_name if spec else None,
            "base_url": spec.base_url if spec else None,
            "original_filename": v.original_filename,
            "minio_key": v.minio_key,
            "file_size": v.file_size,
            "file_hash": v.file_hash,
            "endpoint_count": v.endpoint_count,
            "parse_summary": v.parse_summary,
            "parse_warnings": v.parse_warnings,
            "created_at": v.created_at.isoformat() if v.created_at else None
        })

    return {"success": True, "data": data, "total": total}



@router.get("/{version_id}")
def get_spec_version_detail(
    version_id: int,
    db: Session = Depends(get_db)
):
    """获取接口文件详情（含 endpoints）"""
    version = db.query(ApiSpecVersion).filter(ApiSpecVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="接口文件不存在")
    
    # 验证版本所属项目是否启用
    if version.project_id:
        project = get_active_project_by_id(db, version.project_id)
        if not project:
            raise HTTPException(status_code=400, detail="接口文件所属项目未启用")

    spec = db.query(ApiSpec).filter(ApiSpec.id == version.spec_id).first()
    endpoints = db.query(ApiEndpoint).filter(
        ApiEndpoint.spec_version_id == version_id
    ).all()

    return {
        "success": True,
        "data": {
            "id": version.id,
            "spec_id": version.spec_id,
            "service_name": spec.service_name if spec else None,
            "original_filename": version.original_filename,
            "minio_key": version.minio_key,
            "file_size": version.file_size,
            "endpoint_count": version.endpoint_count,
            "parse_summary": version.parse_summary,
            "parse_warnings": version.parse_warnings,
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "endpoints": [
                {
                    "id": ep.id,
                    "method": ep.method,
                    "path": ep.path,
                    "summary": ep.summary,
                    "description": ep.description,
                    "params": ep.params,
                    "success_example": ep.success_example,
                    "error_example": ep.error_example,
                    "notes": ep.notes
                }
                for ep in endpoints
            ]
        }
    }


@router.delete("/{version_id}")
def delete_spec_version(
    version_id: int,
    db: Session = Depends(get_db)
):
    """删除接口文件（MinIO + 数据库）"""
    version = db.query(ApiSpecVersion).filter(ApiSpecVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="接口文件不存在")
    
    # 验证版本所属项目是否启用
    if version.project_id:
        project = get_active_project_by_id(db, version.project_id)
        if not project:
            raise HTTPException(status_code=400, detail="接口文件所属项目未启用")

    # 删除 MinIO 对象
    try:
        client = get_minio_client()
        client.remove_object(version.minio_bucket, version.minio_key)
    except Exception as e:
        print(f"[Spec] MinIO 删除失败(非致命): {e}")

    # 删除关联 endpoints
    db.query(ApiEndpoint).filter(ApiEndpoint.spec_version_id == version_id).delete()
    db.delete(version)

    # 如果该 spec 下没有其他版本了，也删除 spec
    remaining = db.query(ApiSpecVersion).filter(
        ApiSpecVersion.spec_id == version.spec_id
    ).count()
    if remaining == 0:
        db.query(ApiSpec).filter(ApiSpec.id == version.spec_id).delete()

    db.commit()

    return {"success": True, "message": "删除成功"}


@router.get("/{version_id}/content")
def get_spec_content(
    version_id: int,
    db: Session = Depends(get_db)
):
    """从 MinIO 获取接口文件原始内容"""
    version = db.query(ApiSpecVersion).filter(ApiSpecVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="接口文件不存在")

    try:
        client = get_minio_client()
        response = client.get_object(version.minio_bucket, version.minio_key)
        content = response.read().decode('utf-8')
        response.close()
        response.release_conn()
        return {"success": True, "data": {"content": content, "filename": version.original_filename}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")



@router.post("/import-from-url")
async def import_from_url(
    request: ImportFromURLRequest,
    db: Session = Depends(get_db)
):
    """从 URL 导入接口文档"""
    # 获取默认项目
    default_project = resolve_project_context(db, request.project_id)
    if not default_project:
        raise HTTPException(status_code=400, detail="没有可用的项目，请先创建并启用一个项目")

    try:
        # 使用 URLImporter 导入
        result = await URLImporter.import_from_url(str(request.url))
        
        # 转换为 Markdown
        md_content = to_markdown(result)
        content_bytes = md_content.encode('utf-8')
        file_hash = hashlib.sha256(content_bytes).hexdigest()
        file_size = len(content_bytes)
        
        # 检查是否已上传过相同文件（项目级别）
        existing = db.query(ApiSpecVersion).filter(
            ApiSpecVersion.file_hash == file_hash,
            ApiSpecVersion.project_id == default_project.id
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"该项目中已上传过相同文件 (版本ID: {existing.id})")
        
        # 上传到 MinIO
        client, bucket = ensure_bucket()
        svc = request.service_name or result.get('service_name', 'default')
        ts = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f"url_import_{ts}.md"
        minio_key = f"api-md/{svc}/{ts}/{filename}"
        
        client.put_object(
            bucket,
            minio_key,
            io.BytesIO(content_bytes),
            length=file_size,
            content_type='text/markdown'
        )
        
        # 解析接口
        endpoints = result.get('endpoints', [])
        warnings = result.get('warnings', [])
        
        # 生成摘要
        ep_summaries = [f"{ep['method']} {ep['path']}" + (f" - {ep.get('summary', '')}" if ep.get('summary') else '')
                        for ep in endpoints[:20]]
        parse_summary = f"共 {len(endpoints)} 个接口\n" + "\n".join(ep_summaries)
        
        # 创建 ApiSpec
        spec = db.query(ApiSpec).filter(
            ApiSpec.service_name == svc,
            ApiSpec.project_id == default_project.id
        ).first()
        if not spec:
            spec = ApiSpec(
                service_name=svc,
                project_id=default_project.id,
                base_url=request.base_url
            )
            db.add(spec)
            db.flush()
        else:
            if request.base_url:
                spec.base_url = request.base_url
                db.flush()
        
        # 创建 ApiSpecVersion
        version = ApiSpecVersion(
            spec_id=spec.id,
            project_id=default_project.id,
            original_filename=filename,
            minio_bucket=bucket,
            minio_key=minio_key,
            file_hash=file_hash,
            file_size=file_size,
            parse_summary=parse_summary,
            endpoint_count=len(endpoints),
            parse_warnings=warnings if warnings else None
        )
        db.add(version)
        db.flush()
        
        # 写入 ApiEndpoint
        for ep in endpoints:
            db.add(ApiEndpoint(
                spec_version_id=version.id,
                method=ep['method'],
                path=ep['path'],
                summary=ep.get('summary'),
                description=ep.get('description'),
                params=ep.get('params'),
                success_example=ep.get('success_example'),
                error_example=ep.get('error_example'),
                notes=ep.get('notes')
            ))
        
        db.commit()
        
        return {
            "success": True,
            "data": {
                "spec_version_id": version.id,
                "spec_id": spec.id,
                "original_filename": filename,
                "minio_key": minio_key,
                "file_size": file_size,
                "endpoint_count": len(endpoints),
                "parse_summary": parse_summary,
                "base_url": spec.base_url,
                "warnings": warnings
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@router.post("/import-from-text")
async def import_from_text(
    request: ImportFromTextRequest,
    db: Session = Depends(get_db)
):
    """从文本粘贴导入接口文档"""
    # 获取默认项目
    default_project = resolve_project_context(db, request.project_id)
    if not default_project:
        raise HTTPException(status_code=400, detail="没有可用的项目，请先创建并启用一个项目")

    try:
        # 根据格式提示选择导入器
        result = None
        if request.format_hint == 'curl':
            importer = CurlImporter()
            result = importer.import_from_text(request.content)
        elif request.format_hint == 'json':
            # 尝试检测是 Postman 还是 OpenAPI
            try:
                data = json.loads(request.content)
                if 'info' in data and 'item' in data:
                    importer = PostmanImporter()
                    result = importer.import_collection(data)
                elif 'openapi' in data or 'swagger' in data:
                    importer = OpenAPIImporter()
                    result = importer.import_spec(data)
                else:
                    raise HTTPException(status_code=400, detail="无法识别的 JSON 格式")
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="JSON 格式错误")
        elif request.format_hint == 'markdown':
            # 直接解析 Markdown
            parse_result = parse_api_markdown(request.content)
            result = {
                'endpoints': parse_result['endpoints'],
                'warnings': parse_result['warnings'],
                'service_name': 'default'
            }
        else:
            # 自动检测
            content = request.content.strip()
            if content.startswith('curl '):
                importer = CurlImporter()
                result = importer.import_from_text(content)
            elif content.startswith('{'):
                try:
                    data = json.loads(content)
                    if 'info' in data and 'item' in data:
                        importer = PostmanImporter()
                        result = importer.import_collection(data)
                    elif 'openapi' in data or 'swagger' in data:
                        importer = OpenAPIImporter()
                        result = importer.import_spec(data)
                    else:
                        raise HTTPException(status_code=400, detail="无法识别的 JSON 格式")
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail="JSON 格式错误")
            else:
                # 尝试作为 Markdown 解析
                parse_result = parse_api_markdown(content)
                result = {
                    'endpoints': parse_result['endpoints'],
                    'warnings': parse_result['warnings'],
                    'service_name': 'default'
                }
        
        if not result or not result.get('endpoints'):
            raise HTTPException(status_code=400, detail="未能解析出任何接口")
        
        # 转换为 Markdown
        md_content = to_markdown(result)
        content_bytes = md_content.encode('utf-8')
        file_hash = hashlib.sha256(content_bytes).hexdigest()
        file_size = len(content_bytes)
        
        # 检查是否已上传过相同文件（项目级别）
        existing = db.query(ApiSpecVersion).filter(
            ApiSpecVersion.file_hash == file_hash,
            ApiSpecVersion.project_id == default_project.id
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"该项目中已上传过相同文件 (版本ID: {existing.id})")
        
        # 上传到 MinIO
        client, bucket = ensure_bucket()
        svc = request.service_name or result.get('service_name', 'default')
        ts = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f"text_import_{ts}.md"
        minio_key = f"api-md/{svc}/{ts}/{filename}"
        
        client.put_object(
            bucket,
            minio_key,
            io.BytesIO(content_bytes),
            length=file_size,
            content_type='text/markdown'
        )
        
        # 解析接口
        endpoints = result.get('endpoints', [])
        warnings = result.get('warnings', [])
        
        # 生成摘要
        ep_summaries = [f"{ep['method']} {ep['path']}" + (f" - {ep.get('summary', '')}" if ep.get('summary') else '')
                        for ep in endpoints[:20]]
        parse_summary = f"共 {len(endpoints)} 个接口\n" + "\n".join(ep_summaries)
        
        # 创建 ApiSpec
        spec = db.query(ApiSpec).filter(
            ApiSpec.service_name == svc,
            ApiSpec.project_id == default_project.id
        ).first()
        if not spec:
            spec = ApiSpec(
                service_name=svc,
                project_id=default_project.id,
                base_url=request.base_url
            )
            db.add(spec)
            db.flush()
        else:
            if request.base_url:
                spec.base_url = request.base_url
                db.flush()
        
        # 创建 ApiSpecVersion
        version = ApiSpecVersion(
            spec_id=spec.id,
            project_id=default_project.id,
            original_filename=filename,
            minio_bucket=bucket,
            minio_key=minio_key,
            file_hash=file_hash,
            file_size=file_size,
            parse_summary=parse_summary,
            endpoint_count=len(endpoints),
            parse_warnings=warnings if warnings else None
        )
        db.add(version)
        db.flush()
        
        # 写入 ApiEndpoint
        for ep in endpoints:
            db.add(ApiEndpoint(
                spec_version_id=version.id,
                method=ep['method'],
                path=ep['path'],
                summary=ep.get('summary'),
                description=ep.get('description'),
                params=ep.get('params'),
                success_example=ep.get('success_example'),
                error_example=ep.get('error_example'),
                notes=ep.get('notes')
            ))
        
        db.commit()
        
        return {
            "success": True,
            "data": {
                "spec_version_id": version.id,
                "spec_id": spec.id,
                "original_filename": filename,
                "minio_key": minio_key,
                "file_size": file_size,
                "endpoint_count": len(endpoints),
                "parse_summary": parse_summary,
                "base_url": spec.base_url,
                "warnings": warnings
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@router.post("/import-md")
async def import_md_file(
    file: UploadFile = File(...),
    service_name: Optional[str] = Form(None),
    base_url: Optional[str] = Form(None),
    project_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    """上传 Markdown 接口文件到 MinIO 并解析入库"""
    if not file.filename.endswith('.md'):
        raise HTTPException(status_code=400, detail="仅支持 .md 文件")

    content_bytes = await file.read()
    content_str = content_bytes.decode('utf-8')
    file_hash = hashlib.sha256(content_bytes).hexdigest()
    file_size = len(content_bytes)

    # 获取默认项目
    default_project = resolve_project_context(db, project_id)
    if not default_project:
        raise HTTPException(status_code=400, detail="没有可用的项目，请先创建并启用一个项目")

    # 检查是否已上传过相同文件（项目级别）
    existing = db.query(ApiSpecVersion).filter(
        ApiSpecVersion.file_hash == file_hash,
        ApiSpecVersion.project_id == default_project.id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"该项目中已上传过相同文件 (版本ID: {existing.id})")

    # 上传到 MinIO
    try:
        client, bucket = ensure_bucket()
        svc = service_name or 'default'
        ts = datetime.now().strftime('%Y%m%d%H%M%S')
        minio_key = f"api-md/{svc}/{ts}/{file.filename}"

        client.put_object(
            bucket,
            minio_key,
            io.BytesIO(content_bytes),
            length=file_size,
            content_type='text/markdown'
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MinIO 上传失败: {str(e)}")

    # 解析 Markdown
    parse_result = parse_api_markdown(content_str)
    endpoints = parse_result['endpoints']
    warnings = parse_result['warnings']

    # 生成摘要
    ep_summaries = [f"{ep['method']} {ep['path']}" + (f" - {ep['summary']}" if ep.get('summary') else '')
                    for ep in endpoints[:20]]
    parse_summary = f"共 {len(endpoints)} 个接口\n" + "\n".join(ep_summaries)

    # 创建 ApiSpec（按 service_name 分组）
    spec = db.query(ApiSpec).filter(
        ApiSpec.service_name == svc,
        ApiSpec.project_id == default_project.id
    ).first()
    if not spec:
        spec = ApiSpec(
            service_name=svc,
            project_id=default_project.id,
            base_url=base_url
        )
        db.add(spec)
        db.flush()
    else:
        # 如果提供了新的 base_url，更新它
        if base_url:
            spec.base_url = base_url
            db.flush()

    # 创建 ApiSpecVersion
    version = ApiSpecVersion(
        spec_id=spec.id,
        project_id=default_project.id,
        original_filename=file.filename,
        minio_bucket=bucket,
        minio_key=minio_key,
        file_hash=file_hash,
        file_size=file_size,
        parse_summary=parse_summary,
        endpoint_count=len(endpoints),
        parse_warnings=warnings if warnings else None
    )
    db.add(version)
    db.flush()

    # 写入 ApiEndpoint
    for ep in endpoints:
        db.add(ApiEndpoint(
            spec_version_id=version.id,
            method=ep['method'],
            path=ep['path'],
            summary=ep.get('summary'),
            description=ep.get('description'),
            params=ep.get('params'),
            success_example=ep.get('success_example'),
            error_example=ep.get('error_example'),
            notes=ep.get('notes')
        ))

    db.commit()

    return {
        "success": True,
        "data": {
            "spec_version_id": version.id,
            "spec_id": spec.id,
            "original_filename": file.filename,
            "minio_key": minio_key,
            "file_size": file_size,
            "endpoint_count": len(endpoints),
            "parse_summary": parse_summary,
            "base_url": spec.base_url,
            "warnings": warnings
        }
    }
