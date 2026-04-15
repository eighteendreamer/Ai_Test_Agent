"""
接口测试服务

实现：智能匹配接口文件、endpoint 级匹配、DSL 生成、HTTP Runner、结果入库

作者: 程序员Eighteen
"""
import json
import time
import asyncio
import traceback
import logging
import re
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime

import requests as http_requests
from sqlalchemy.orm import Session

from database.connection import (
    ExecutionCase, ApiSpecVersion, ApiEndpoint, ApiSpec,
    TestRecord, ExecutionBatch, TestReport, BugReport,
    Contact, EmailConfig, EmailRecord
)
from llm import get_llm_client

logger = logging.getLogger(__name__)


class ApiTestService:
    """接口测试服务"""

    @staticmethod
    def match_spec(
        test_case_ids: List[int],
        db: Session,
        top_k: int = 5,
        service_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        智能匹配接口文件

        两段式匹配：
        1. 粗筛：关键词 overlap 筛选候选 spec versions
        2. 精排：LLM 从候选中选出 top1 推荐
        """
        # 获取测试用例
        cases = db.query(ExecutionCase).filter(
            ExecutionCase.id.in_(test_case_ids)
        ).all()
        if not cases:
            return {"success": False, "message": "未找到指定的测试用例"}

        # 合并用例文本
        case_texts = []
        for c in cases:
            text = f"标题: {c.title}"
            if c.steps:
                text += f"\n步骤: {c.steps}"
            if c.expected:
                text += f"\n预期: {c.expected}"
            if c.keywords:
                text += f"\n关键词: {c.keywords}"
            case_texts.append(text)
        combined_text = "\n---\n".join(case_texts)

        # 获取所有 spec versions
        query = db.query(ApiSpecVersion)
        if service_name:
            spec_ids = [s.id for s in db.query(ApiSpec).filter(
                ApiSpec.service_name == service_name
            ).all()]
            if spec_ids:
                query = query.filter(ApiSpecVersion.spec_id.in_(spec_ids))

        versions = query.all()
        if not versions:
            return {"success": False, "message": "暂无已上传的接口文件，请先上传"}

        # 粗筛：关键词 overlap 打分
        candidates = []
        for v in versions:
            spec = db.query(ApiSpec).filter(ApiSpec.id == v.spec_id).first()
            score = _keyword_overlap_score(combined_text, v.parse_summary or '', v.original_filename)
            endpoints = db.query(ApiEndpoint).filter(
                ApiEndpoint.spec_version_id == v.id
            ).all()
            ep_list = [{"method": ep.method, "path": ep.path, "summary": ep.summary or ''} for ep in endpoints]
            candidates.append({
                "spec_version_id": v.id,
                "original_filename": v.original_filename,
                "minio_key": v.minio_key,
                "service_name": spec.service_name if spec else None,
                "endpoint_count": v.endpoint_count,
                "parse_summary": v.parse_summary,
                "score": score,
                "endpoints": ep_list
            })

        # 按分数排序取 topK
        candidates.sort(key=lambda x: x['score'], reverse=True)
        top_candidates = candidates[:top_k]

        # 如果只有一个候选，直接返回
        if len(top_candidates) == 1:
            rec = top_candidates[0]
            return {
                "success": True,
                "data": {
                    "recommended": {
                        "spec_version_id": rec["spec_version_id"],
                        "original_filename": rec["original_filename"],
                        "minio_key": rec["minio_key"],
                        "service_name": rec["service_name"],
                        "endpoint_count": rec["endpoint_count"],
                        "confidence": 0.9,
                        "reason": "仅有一个候选接口文件，自动推荐"
                    },
                    "candidates": [],
                    "preview_endpoints": rec["endpoints"]
                }
            }

        # 精排：LLM 选择最佳
        try:
            llm_result = _llm_rank_specs(combined_text, top_candidates)
        except Exception as e:
            logger.warning(f"LLM 精排失败，使用粗筛结果: {e}")
            llm_result = None

        if llm_result and llm_result.get("recommended"):
            rec_id = llm_result["recommended"]["spec_version_id"]
            rec_item = next((c for c in top_candidates if c["spec_version_id"] == rec_id), top_candidates[0])
            others = [c for c in top_candidates if c["spec_version_id"] != rec_id]
            return {
                "success": True,
                "data": {
                    "recommended": {
                        "spec_version_id": rec_item["spec_version_id"],
                        "original_filename": rec_item["original_filename"],
                        "minio_key": rec_item["minio_key"],
                        "service_name": rec_item["service_name"],
                        "endpoint_count": rec_item["endpoint_count"],
                        "confidence": llm_result["recommended"].get("confidence", 0.8),
                        "reason": llm_result["recommended"].get("reason", "")
                    },
                    "candidates": [
                        {
                            "spec_version_id": c["spec_version_id"],
                            "original_filename": c["original_filename"],
                            "minio_key": c["minio_key"],
                            "service_name": c["service_name"],
                            "endpoint_count": c["endpoint_count"],
                            "confidence": 0.5,
                            "reason": ""
                        } for c in others
                    ],
                    "preview_endpoints": rec_item["endpoints"]
                }
            }

        # fallback: 用粗筛第一名
        rec = top_candidates[0]
        others = top_candidates[1:]
        return {
            "success": True,
            "data": {
                "recommended": {
                    "spec_version_id": rec["spec_version_id"],
                    "original_filename": rec["original_filename"],
                    "minio_key": rec["minio_key"],
                    "service_name": rec["service_name"],
                    "endpoint_count": rec["endpoint_count"],
                    "confidence": 0.7,
                    "reason": "基于关键词匹配推荐"
                },
                "candidates": [
                    {
                        "spec_version_id": c["spec_version_id"],
                        "original_filename": c["original_filename"],
                        "minio_key": c["minio_key"],
                        "service_name": c["service_name"],
                        "endpoint_count": c["endpoint_count"],
                        "confidence": round(c["score"] / max(rec["score"], 1) * 0.7, 2),
                        "reason": ""
                    } for c in others
                ],
                "preview_endpoints": rec["endpoints"]
            }
        }

    @staticmethod
    def match_case_endpoints(
        test_case_ids: List[int],
        spec_version_id: int,
        db: Session
    ) -> Dict[str, Any]:
        """
        为每条用例匹配具体的 endpoint（在用户确认接口文件后调用）

        返回 case_endpoint_map: { case_id: { method, path, summary } }
        """
        cases = db.query(ExecutionCase).filter(
            ExecutionCase.id.in_(test_case_ids)
        ).all()
        if not cases:
            return {"success": False, "message": "未找到指定的测试用例"}

        endpoints = db.query(ApiEndpoint).filter(
            ApiEndpoint.spec_version_id == spec_version_id
        ).all()
        if not endpoints:
            return {"success": False, "message": "该接口文件中没有解析出接口"}

        ep_list = [{"method": ep.method, "path": ep.path, "summary": ep.summary or ''} for ep in endpoints]

        # 构建用例描述
        case_descs = []
        for c in cases:
            desc = f"case_id={c.id}, 标题={c.title}"
            if c.keywords:
                desc += f", 关键词={c.keywords}"
            if c.steps:
                steps_str = c.steps if isinstance(c.steps, str) else json.dumps(c.steps, ensure_ascii=False)
                desc += f", 步骤={steps_str[:200]}"
            case_descs.append(desc)

        ep_desc = "\n".join([f"- {ep['method']} {ep['path']} ({ep['summary']})" for ep in ep_list])

        llm = get_llm_client()

        system_prompt = """你是一个接口测试专家。根据测试用例列表和可用接口列表，为每条用例选择最合适的接口。

返回严格 JSON（不要包含注释）:
{
  "mappings": [
    { "case_id": <int>, "method": "POST", "path": "/xxx", "reason": "简短理由" }
  ]
}

规则：
- 每条用例必须匹配一个接口
- 根据用例标题、关键词、步骤来判断应该调用哪个接口
- 如果用例明确提到了某个操作（如"登录"、"删除"、"查询"），优先匹配对应功能的接口"""

        user_prompt = f"""测试用例:
{chr(10).join(case_descs)}

可用接口:
{ep_desc}

请为每条用例选择最合适的接口。"""

        try:
            response = llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=2000
            )

            cleaned = response.strip()
            if cleaned.startswith('```'):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'```\s*$', '', cleaned)
            cleaned = re.sub(r'<think>[\s\S]*?</think>', '', cleaned).strip()

            result = json.loads(cleaned)
            mappings = result.get("mappings", [])

            # 构建 map
            case_endpoint_map = {}
            for m in mappings:
                cid = m.get("case_id")
                if cid is not None:
                    case_endpoint_map[str(cid)] = {
                        "method": m.get("method", "GET"),
                        "path": m.get("path", "/"),
                        "reason": m.get("reason", "")
                    }

            # 补全未匹配的用例（fallback 到第一个 endpoint）
            for c in cases:
                if str(c.id) not in case_endpoint_map:
                    case_endpoint_map[str(c.id)] = {
                        "method": ep_list[0]["method"],
                        "path": ep_list[0]["path"],
                        "reason": "默认匹配"
                    }

            # 补充 summary
            ep_summary_map = {f"{ep['method']} {ep['path']}": ep['summary'] for ep in ep_list}
            for cid, ep_info in case_endpoint_map.items():
                key = f"{ep_info['method']} {ep_info['path']}"
                ep_info["summary"] = ep_summary_map.get(key, "")

            return {"success": True, "data": {"case_endpoint_map": case_endpoint_map}}

        except Exception as e:
            logger.warning(f"LLM endpoint 匹配失败，使用默认: {e}")
            # fallback: 全部用第一个 endpoint
            case_endpoint_map = {}
            for c in cases:
                case_endpoint_map[str(c.id)] = {
                    "method": ep_list[0]["method"],
                    "path": ep_list[0]["path"],
                    "summary": ep_list[0]["summary"],
                    "reason": "默认匹配（LLM 调用失败）"
                }
            return {"success": True, "data": {"case_endpoint_map": case_endpoint_map}}

    @staticmethod
    async def execute_api_test(
        test_case_ids: List[int],
        spec_version_id: int,
        db: Session,
        environment: Optional[Dict] = None,
        mode: str = "llm_enhanced",
        case_endpoint_map: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        执行接口测试

        流程：
        1. 读取用例 + 接口文件
        2. 为每条用例匹配 endpoint + 生成 DSL
        3. Runner 执行 HTTP 请求
        4. 结果入库 test_records
        5. 生成报告 test_reports
        6. 失败用例生成 bug_reports + 邮件通知
        """
        # 读取用例
        cases = db.query(ExecutionCase).filter(
            ExecutionCase.id.in_(test_case_ids)
        ).all()
        if not cases:
            return {"success": False, "message": "未找到指定的测试用例"}

        # 读取接口文件
        version = db.query(ApiSpecVersion).filter(
            ApiSpecVersion.id == spec_version_id
        ).first()
        if not version:
            return {"success": False, "message": "未找到指定的接口文件版本"}

        endpoints = db.query(ApiEndpoint).filter(
            ApiEndpoint.spec_version_id == spec_version_id
        ).all()
        ep_list = [{
            "method": ep.method,
            "path": ep.path,
            "summary": ep.summary or '',
            "description": ep.description,
            "params": ep.params,
            "success_example": ep.success_example,
            "error_example": ep.error_example,
            "notes": ep.notes
        } for ep in endpoints]

        # 自动修复：如果 endpoint 的 notes/params 全为空，从 MinIO 重新解析补充
        has_missing = any(
            (not ep.get("notes") and not ep.get("params"))
            or ep.get("params") in (None, 'null', 'None')
            for ep in ep_list
        )
        if has_missing and version.minio_key:
            ep_list = _repair_endpoints_from_minio(ep_list, version, db)

        base_url = (environment or {}).get("base_url", "http://localhost:8080")
        headers = (environment or {}).get("headers", {})
        variables = (environment or {}).get("variables", {})

        # 创建批次
        batch_no = f"API-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        results = []
        test_record_ids = []
        failed_records = []
        total_duration = 0

        for case in cases:
            start_time = time.time()
            try:
                # 如果有预匹配的 endpoint map，只传该用例对应的单个 endpoint
                if case_endpoint_map and str(case.id) in case_endpoint_map:
                    matched = case_endpoint_map[str(case.id)]
                    # 从完整 ep_list 中找到对应的 endpoint（带完整文档信息）
                    target_ep = None
                    for ep in ep_list:
                        if ep["method"] == matched["method"] and ep["path"] == matched["path"]:
                            target_ep = ep
                            break
                    if target_ep:
                        case_ep_list = [target_ep]
                    else:
                        # fallback: 构造基础信息
                        case_ep_list = [{"method": matched["method"], "path": matched["path"],
                                         "summary": matched.get("summary", ""), "description": None,
                                         "params": None, "success_example": None, "error_example": None, "notes": None}]
                    dsl = await _match_endpoint_and_generate_dsl(case, case_ep_list, mode)
                else:
                    dsl = await _match_endpoint_and_generate_dsl(case, ep_list, mode)

                # 执行 HTTP 请求（放入线程池避免阻塞事件循环）
                loop = asyncio.get_event_loop()
                exec_result = await loop.run_in_executor(
                    None, _execute_http_request, dsl, base_url, headers, variables
                )

                duration = int(time.time() - start_time)
                total_duration += duration

                status = "pass" if exec_result["passed"] else "fail"
                error_msg = exec_result.get("error_message", "")

                # 写入 execution_batches
                batch = ExecutionBatch(
                    execution_case_id=case.id,
                    batch=batch_no
                )
                db.add(batch)
                db.flush()

                # 写入 test_records
                record = TestRecord(
                    batch_id=batch.id,
                    test_case_id=case.id,
                    execution_mode='接口测试',
                    total_cases=1,
                    passed_cases=1 if status == "pass" else 0,
                    failed_cases=0 if status == "pass" else 1,
                    execution_log=json.dumps({
                        "spec_version_id": spec_version_id,
                        "minio_key": version.minio_key,
                        "endpoint": dsl.get("endpoint", {}),
                        "request": exec_result.get("request_info", {}),
                        "response": exec_result.get("response_info", {}),
                        "assertions": exec_result.get("assertions", [])
                    }, ensure_ascii=False),
                    status=status,
                    error_message=error_msg if error_msg else None,
                    duration=duration,
                    test_steps=len(exec_result.get("assertions", []))
                )
                db.add(record)
                db.flush()
                test_record_ids.append(record.id)

                case_result = {
                    "test_case_id": case.id,
                    "title": case.title,
                    "status": status,
                    "duration": duration,
                    "endpoint": dsl.get("endpoint", {}),
                    "error_message": error_msg,
                    "record_id": record.id,
                    "request_info": exec_result.get("request_info", {}),
                    "response_info": {
                        "status_code": exec_result.get("response_info", {}).get("status_code"),
                        "body_trunc": exec_result.get("response_info", {}).get("body_trunc", "")[:1500]
                    },
                    "assertions": exec_result.get("assertions", [])
                }
                results.append(case_result)

                if status == "fail":
                    failed_records.append({
                        "record": record,
                        "case": case,
                        "exec_result": exec_result,
                        "dsl": dsl
                    })

            except Exception as e:
                duration = int(time.time() - start_time)
                total_duration += duration
                logger.error(f"用例 {case.id} 执行异常: {e}\n{traceback.format_exc()}")

                batch = ExecutionBatch(execution_case_id=case.id, batch=batch_no)
                db.add(batch)
                db.flush()

                record = TestRecord(
                    batch_id=batch.id,
                    test_case_id=case.id,
                    execution_mode='接口测试',
                    total_cases=1,
                    passed_cases=0,
                    failed_cases=1,
                    execution_log=json.dumps({"error": str(e)}, ensure_ascii=False),
                    status="fail",
                    error_message=str(e),
                    duration=duration,
                    test_steps=0
                )
                db.add(record)
                db.flush()
                test_record_ids.append(record.id)

                results.append({
                    "test_case_id": case.id,
                    "title": case.title,
                    "status": "fail",
                    "duration": duration,
                    "endpoint": {},
                    "error_message": str(e),
                    "record_id": record.id
                })
                failed_records.append({
                    "record": record,
                    "case": case,
                    "exec_result": {"error_message": str(e)},
                    "dsl": {}
                })

        db.commit()

        pass_count = sum(1 for r in results if r["status"] == "pass")
        fail_count = len(results) - pass_count

        # 生成测试报告
        report_id = None
        try:
            report_id = await _generate_api_test_report(
                results, test_record_ids, total_duration, db
            )
        except Exception as e:
            logger.error(f"生成报告失败: {e}")

        # 处理失败用例：生成 Bug + 邮件通知
        bug_report_ids = []
        if failed_records:
            try:
                bug_report_ids = _create_bug_reports(failed_records, db)
                db.commit()
            except Exception as e:
                logger.error(f"生成 Bug 报告失败: {e}")

            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, _send_bug_email_notification, failed_records, bug_report_ids, db
                )
            except Exception as e:
                logger.error(f"发送 Bug 邮件通知失败: {e}")

        return {
            "success": True,
            "data": {
                "test_record_ids": test_record_ids,
                "report_id": report_id,
                "bug_report_ids": bug_report_ids,
                "summary": {
                    "total": len(results),
                    "pass": pass_count,
                    "fail": fail_count,
                    "pass_rate": round(pass_count / len(results) * 100, 2) if results else 0,
                    "duration": total_duration
                },
                "results": results
            }
        }


# ============================================
# 辅助函数
# ============================================

def _keyword_overlap_score(case_text: str, parse_summary: str, filename: str) -> float:
    """关键词 overlap 打分"""
    case_words = set(re.findall(r'[\w/]+', case_text.lower()))
    summary_words = set(re.findall(r'[\w/]+', (parse_summary + ' ' + filename).lower()))
    if not case_words or not summary_words:
        return 0.0
    overlap = case_words & summary_words
    return len(overlap) / max(len(case_words), 1)


def _llm_rank_specs(case_text: str, candidates: List[Dict]) -> Optional[Dict]:
    """LLM 精排候选接口文件"""
    llm = get_llm_client()

    candidates_desc = []
    for c in candidates:
        desc = f"- spec_version_id={c['spec_version_id']}, 文件名={c['original_filename']}, 接口数={c['endpoint_count']}"
        if c.get('parse_summary'):
            desc += f"\n  摘要: {c['parse_summary'][:300]}"
        candidates_desc.append(desc)

    system_prompt = """你是一个接口测试专家。根据测试用例文本，从候选接口文件中选出最匹配的一个。
返回严格 JSON 格式:
{
  "recommended": { "spec_version_id": <int>, "confidence": <0-1>, "reason": "<简短理由>" }
}"""

    user_prompt = f"""测试用例:
{case_text[:2000]}

候选接口文件:
{chr(10).join(candidates_desc)}

请选出最匹配的接口文件。"""

    response = llm.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.1,
        max_tokens=500
    )

    # 解析 JSON
    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'```\s*$', '', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _repair_endpoints_from_minio(ep_list: List[Dict], version, db) -> List[Dict]:
    """
    从 MinIO 重新读取原始 markdown，重新解析，补充 ep_list 中缺失的 notes/params。
    同时更新数据库中的 api_endpoints 记录。
    """
    try:
        from Api_Spec.minio_client import get_minio_client, get_bucket_name
        from Api_Spec.parser import parse_api_markdown

        client = get_minio_client()
        bucket = get_bucket_name()
        response = client.get_object(bucket, version.minio_key)
        content = response.read().decode('utf-8')
        response.close()
        response.release_conn()

        parsed = parse_api_markdown(content)
        parsed_eps = parsed.get("endpoints", [])

        # 构建 method+path → parsed endpoint 的映射
        parsed_map = {}
        for pep in parsed_eps:
            key = f"{pep['method'].upper()} {pep['path']}"
            parsed_map[key] = pep

        # 补充缺失字段并更新数据库
        for ep in ep_list:
            key = f"{ep['method'].upper()} {ep['path']}"
            if key not in parsed_map:
                continue
            pep = parsed_map[key]
            changed = False
            for field in ("notes", "params", "success_example", "error_example", "description"):
                old_val = ep.get(field)
                new_val = pep.get(field)
                if (not old_val or old_val in ('null', 'None')) and new_val:
                    ep[field] = new_val
                    changed = True
            if changed:
                try:
                    db.query(ApiEndpoint).filter(
                        ApiEndpoint.spec_version_id == version.id,
                        ApiEndpoint.method == ep["method"],
                        ApiEndpoint.path == ep["path"]
                    ).update({
                        "notes": ep.get("notes"),
                        "params": ep.get("params"),
                        "success_example": ep.get("success_example"),
                        "error_example": ep.get("error_example"),
                        "description": ep.get("description"),
                    })
                except Exception as ue:
                    logger.warning(f"更新 endpoint 失败: {ue}")
        try:
            db.commit()
            logger.info(f"自动修复: 从 MinIO 重新解析并补充了 endpoint 数据")
        except Exception:
            db.rollback()

    except Exception as e:
        logger.warning(f"从 MinIO 修复 endpoint 数据失败: {e}")

    return ep_list


async def _match_endpoint_and_generate_dsl(
    case: ExecutionCase,
    endpoints: List[Dict],
    mode: str
) -> Dict[str, Any]:
    """
    为单条用例匹配 endpoint 并生成可执行 DSL

    核心逻辑：把接口文档中解析出的完整信息（params、请求体示例、响应示例、notes）
    全部传给 LLM，让它基于文档中的真实数据来构造请求，而不是凭空猜测。
    """
    case_text = f"标题: {case.title}\n步骤: {case.steps}\n预期: {case.expected}"
    if case.test_data:
        case_text += f"\n测试数据: {json.dumps(case.test_data, ensure_ascii=False)}"

    # 构建完整的接口文档描述（包含 params、示例、notes）
    ep_details = []
    for ep in endpoints:
        detail = f"### {ep['method']} {ep['path']}"
        if ep.get('summary'):
            detail += f"\n功能: {ep['summary']}"
        if ep.get('description'):
            detail += f"\n描述: {ep['description']}"
        if ep.get('params'):
            detail += f"\n请求参数:\n{ep['params']}"
        if ep.get('success_example'):
            detail += f"\n成功响应示例:\n```json\n{ep['success_example']}\n```"
        if ep.get('error_example'):
            detail += f"\n错误响应示例:\n```json\n{ep['error_example']}\n```"
        if ep.get('notes'):
            detail += f"\n说明: {ep['notes']}"
        ep_details.append(detail)

    ep_doc = "\n\n".join(ep_details)

    llm = get_llm_client()

    single_ep = len(endpoints) == 1
    system_prompt = """你是一个接口测试 DSL 生成专家。
你的任务是根据测试用例和接口文档，""" + ("基于指定的接口" if single_ep else "选择最合适的接口，并") + """基于文档中的参数定义和示例构造真实的 HTTP 请求。

⚠️ 最高优先级规则（违反即为错误）：
1. 请求体的字段名（key）必须与接口文档中"请求体"或"请求参数"部分定义的字段名完全一致，严禁自行编造或替换字段名。
   例如：文档写 "userName" 就必须用 "userName"，绝对不能用 "account"、"username"、"user_name" 等替代。
   例如：文档写 "passWord" 就必须用 "passWord"，绝对不能用 "password"、"pass_word"、"pwd" 等替代。
2. 必须仔细阅读接口文档中的"说明"部分，严格遵守其中的特殊处理要求。
   例如：如果说明写"密码使用MD5加密"，则 body 中的密码值必须是 MD5 哈希后的字符串（32位十六进制），不能传明文。
3. 测试用例中的测试数据（如 account=2022900116）提供的是原始值，需要根据文档说明进行转换后再填入对应字段。
   例如：用例给出 account=2022900116，文档字段名是 userName → body 中用 "userName": "2022900116"
   例如：用例给出 password=900116，文档说明要求 MD5 加密 → body 中用 "passWord": "<900116的MD5值>"

其他规则：
4. GET 请求的参数放在 query_params 中
5. POST/PUT/PATCH 请求的参数放在 body 中（JSON 格式）
6. 如果文档中有请求参数说明（如 `id: 作业记录ID (必填)`），根据测试用例上下文填入合理值
7. assertions 断言规则：
   - 第一条断言必须是 status_code == 200
   - 如果文档的成功响应示例中有具体的数值字段（如 code: 200），可以加 json_path 断言
   - 如果响应示例中的值是中文占位符（如 "用户ID"、"学生姓名"），使用 json_not_null 断言检查字段存在即可，不要用 json_path 精确匹配
   - 不要假设响应中有 $.code 字段，除非文档示例明确包含 code 字段
   - 断言宁少勿多，status_code 通过即可视为基本成功""" + ("""
8. endpoint 必须使用指定的接口，不要更改 method 和 path""" if single_ep else "") + """

返回严格 JSON（不要包含注释）:
{
  "endpoint": { "method": "GET|POST|PUT|DELETE", "path": "/xxx" },
  "request": {
    "query_params": {},
    "headers": {},
    "body": null
  },
  "assertions": [
    { "type": "status_code", "expected": 200 },
    { "type": "json_path", "path": "$.code", "expected": 200 }
  ]
}"""

    # 额外提取请求体和说明信息，在 user prompt 中重点强调
    body_schema_hints = []
    for ep in endpoints:
        hint_parts = []
        if ep.get('params'):
            hint_parts.append(f"请求体字段定义:\n{ep['params']}")
        if ep.get('notes'):
            hint_parts.append(f"特殊说明（必须遵守）:\n{ep['notes']}")
        if hint_parts:
            body_schema_hints.append(f"[{ep['method']} {ep['path']}]\n" + "\n".join(hint_parts))

    schema_reminder = ""
    if body_schema_hints:
        schema_reminder = "\n\n⚠️ 重要提醒 - 以下字段名和说明必须严格遵守:\n" + "\n\n".join(body_schema_hints)

    if len(endpoints) == 1:
        user_prompt = f"""测试用例:
{case_text}

指定接口:
{ep_doc[:6000]}
{schema_reminder}

请严格按照接口文档中定义的字段名（不要替换为其他名称）和说明要求，为该测试用例生成可执行的测试 DSL。"""
    else:
        user_prompt = f"""测试用例:
{case_text}

接口文档:
{ep_doc[:6000]}
{schema_reminder}

请严格按照接口文档中定义的字段名（不要替换为其他名称）和说明要求，为该测试用例生成可执行的测试 DSL。"""

    response = await llm.achat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.1,
        max_tokens=2000
    )

    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'```\s*$', '', cleaned)

    # 去除 think 标签
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', cleaned).strip()

    try:
        dsl = json.loads(cleaned)
    except json.JSONDecodeError:
        # fallback: 用第一个 endpoint，基于文档信息构造基础请求
        ep = endpoints[0] if endpoints else {"method": "GET", "path": "/"}
        dsl = _build_fallback_dsl(ep, case)

    # 确保 DSL 结构完整
    dsl.setdefault("endpoint", {})
    dsl["endpoint"].setdefault("method", "GET")
    dsl["endpoint"].setdefault("path", "/")
    dsl.setdefault("request", {})
    dsl["request"].setdefault("query_params", {})
    dsl["request"].setdefault("headers", {})
    dsl["request"].setdefault("body", None)
    dsl.setdefault("assertions", [{"type": "status_code", "expected": 200}])

    # 后处理：根据文档说明应用转换（如 MD5 加密），用用例真实数据覆盖
    dsl = _postprocess_dsl(dsl, endpoints, case, ep_doc)

    return dsl


def _extract_test_values_from_case(case) -> Dict[str, str]:
    """
    从用例的 test_data 和 steps 中提取实际的测试数据值。
    test_data 优先级最高，steps 只补充 test_data 中没有的。
    """
    values = {}
    
    # 1. 从 steps 文本中提取（先提取，后面被 test_data 覆盖）
    steps_text = case.steps if isinstance(case.steps, str) else json.dumps(case.steps or '', ensure_ascii=False)
    
    patterns = [
        r'在[「「]?(\S+?)[」」]?(?:输入框|框|栏|字段)?中?(?:输入|填写|填入)[：:\s]+(\S+)',
        r'(?:输入|填写|填入)\s*(\S+?)[：:\s]+(\S+)',
        r'(\S+?)(?:输入框|框|栏|字段)\s*(?:输入|填写|填入)[：:\s]+(\S+)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, steps_text)
        for label, value in matches:
            label_clean = label.strip('：:，, 。.、"\'[]').lower()
            # 清理值中的引号、方括号等 JSON 残留字符
            value_clean = value.strip('：:，, 。.、"\'[]\\')
            if label_clean and value_clean:
                values[label_clean] = value_clean
    
    # 2. 从 test_data 提取（优先级最高，覆盖 steps 提取的值）
    if case.test_data:
        td = case.test_data if isinstance(case.test_data, dict) else {}
        for k, v in td.items():
            if v is not None:
                values[k.lower()] = str(v).strip('"\'')
    
    return values


def _map_case_values_to_body(
    body: Dict, 
    case_values: Dict[str, str], 
    params_text: str
) -> Dict:
    """
    根据文档 params 中的字段描述，将用例中提取的值映射到 body 的正确字段。
    
    例如文档 params: {"userName": "学号", "passWord": "密码"}
    用例值: {"账号": "2022900116", "密码": "900116"}
    → body: {"userName": "2022900116", "passWord": "900116"}
    """
    if not case_values or not params_text:
        return body
    
    # 从 params 中提取 字段名→描述 的映射
    # 匹配 "userName": "学号" 或 userName: 学号 等格式
    field_desc_map = {}
    
    # JSON 格式: "userName": "学号"
    json_matches = re.findall(r'"(\w+)"\s*[:：]\s*"([^"]+)"', params_text)
    for field, desc in json_matches:
        field_desc_map[field] = desc
    
    # Markdown 格式: - userName: 学号(必填) 或 `userName`: 学号
    if not field_desc_map:
        md_matches = re.findall(r'[`\-*]\s*(\w+)[`]?\s*[:：]\s*(.+?)(?:\n|$)', params_text)
        for field, desc in md_matches:
            field_desc_map[field] = desc.strip()
    
    if not field_desc_map:
        return body
    
    # 构建描述关键词 → 字段名的映射
    # 例如 "学号" → "userName", "密码" → "passWord"
    desc_to_field = {}
    for field, desc in field_desc_map.items():
        desc_lower = desc.lower().strip()
        # 提取核心描述词
        desc_keywords = re.sub(r'[（(].*?[）)]', '', desc_lower).strip()
        desc_to_field[desc_keywords] = field
        # 也加入常见别名
        if '学号' in desc_keywords or '账号' in desc_keywords or '用户名' in desc_keywords:
            for alias in ['账号', '学号', '用户名', 'account', 'username', 'user']:
                desc_to_field[alias] = field
        if '密码' in desc_keywords or '口令' in desc_keywords:
            for alias in ['密码', '口令', 'password', 'pwd', 'pass']:
                desc_to_field[alias] = field
    
    # 用用例值覆盖 body
    for case_key, case_val in case_values.items():
        case_key_lower = case_key.lower()
        if case_key_lower in desc_to_field:
            target_field = desc_to_field[case_key_lower]
            if target_field in body:
                old_val = body[target_field]
                if str(old_val) != str(case_val):
                    logger.info(f"用例数据覆盖: {target_field} = {old_val} → {case_val}")
                    body[target_field] = case_val
            else:
                # 字段不在 body 中，添加
                logger.info(f"用例数据填充: {target_field} = {case_val}")
                body[target_field] = case_val
    
    return body


def _direct_map_case_values(body: Dict, case_values: Dict[str, str]) -> Dict:
    """
    当 params_text 为空时，直接通过语义匹配将用例数据覆盖到 body 字段。
    
    匹配规则：用例 key（中文/英文）→ body key（英文）
    例如：用例 {"账号": "2022900116"} → body {"userName": "2022900116"}
    """
    # 中文语义 → body 字段名的常见映射
    SEMANTIC_MAP = {
        # 账号/用户名类
        '账号': ['username', 'user_name', 'userName', 'account', 'loginName', 'login_name', 'userId', 'user_id'],
        '学号': ['username', 'user_name', 'userName', 'studentId', 'student_id', 'account'],
        '用户名': ['username', 'user_name', 'userName', 'account', 'loginName'],
        '手机号': ['phone', 'mobile', 'phoneNumber', 'phone_number', 'tel'],
        '邮箱': ['email', 'mail', 'emailAddress'],
        # 密码类
        '密码': ['password', 'pass_word', 'passWord', 'pwd', 'passwd'],
        '新密码': ['newPassword', 'new_password', 'newPassWord'],
        '旧密码': ['oldPassword', 'old_password', 'oldPassWord'],
        # 通用
        '姓名': ['name', 'realName', 'real_name', 'userName', 'nickName'],
        '标题': ['title', 'name', 'subject'],
        '内容': ['content', 'body', 'text', 'description'],
    }
    
    for case_key, case_val in case_values.items():
        case_key_lower = case_key.lower()
        
        # 查找语义映射
        possible_fields = []
        for cn_key, en_fields in SEMANTIC_MAP.items():
            if cn_key in case_key_lower or case_key_lower in cn_key:
                possible_fields.extend(en_fields)
                break
        
        # 也尝试英文直接匹配
        if not possible_fields:
            possible_fields = [case_key, case_key_lower]
        
        # 在 body 中找到匹配的字段并覆盖
        for body_key in body.keys():
            body_key_lower = body_key.lower()
            for pf in possible_fields:
                if body_key_lower == pf.lower():
                    old_val = body[body_key]
                    if str(old_val) != str(case_val):
                        logger.info(f"直接语义覆盖: {body_key} = {old_val} → {case_val}")
                        body[body_key] = case_val
                    break
    
    return body


def _postprocess_dsl(dsl: Dict, endpoints: List[Dict], case=None, ep_doc: str = "") -> Dict:
    """
    根据接口文档的说明（notes）对 DSL 进行后处理：
    1. 校正字段名：确保 body 中的 key 与文档 params 一致
    2. 用用例中的真实测试数据覆盖 LLM 可能编造的值
    3. 检测 MD5 加密要求，对密码类字段自动 MD5
    """
    body = dsl.get("request", {}).get("body")
    if not body or not isinstance(body, dict):
        return dsl

    # 找到匹配的 endpoint 文档
    dsl_method = dsl.get("endpoint", {}).get("method", "").upper()
    dsl_path = dsl.get("endpoint", {}).get("path", "")
    matched_ep = None
    for ep in endpoints:
        if ep["method"].upper() == dsl_method and ep["path"] == dsl_path:
            matched_ep = ep
            break
    if not matched_ep and endpoints:
        matched_ep = endpoints[0]
    if not matched_ep:
        return dsl

    notes = (matched_ep.get("notes") or "").lower()
    params_text = matched_ep.get("params") or ""

    # 步骤 1: 校正字段名
    if params_text:
        doc_fields = re.findall(r'"(\w+)"', params_text)
        if not doc_fields:
            doc_fields = re.findall(r'`(\w+)`|[-*]\s*(\w+)\s*[:：]', params_text)
            doc_fields = [m[0] or m[1] for m in doc_fields if (m[0] or m[1]) not in ('必填', '选填', 'required', 'optional')]

        if doc_fields:
            doc_field_map = {f.lower(): f for f in doc_fields}
            corrected_body = {}
            for key, value in body.items():
                lower_key = key.lower()
                if lower_key in doc_field_map and key != doc_field_map[lower_key]:
                    logger.info(f"DSL 字段名校正: {key} → {doc_field_map[lower_key]}")
                    corrected_body[doc_field_map[lower_key]] = value
                else:
                    mapped = False
                    for doc_f in doc_fields:
                        dl = doc_f.lower()
                        if (lower_key in ('account', 'username', 'user', 'login', 'loginname') and
                            dl in ('username', 'user_name', 'loginname', 'account')):
                            corrected_body[doc_f] = value
                            mapped = True
                            break
                        if (lower_key in ('password', 'pwd', 'pass', 'passwd') and
                            dl in ('password', 'pass_word', 'pwd', 'passwd')):
                            corrected_body[doc_f] = value
                            mapped = True
                            break
                    if not mapped:
                        corrected_body[key] = value
            body = corrected_body
            dsl["request"]["body"] = body

    # 步骤 2: 用用例中的真实测试数据覆盖 LLM 编造的值
    if case:
        case_values = _extract_test_values_from_case(case)
        if case_values:
            logger.info(f"从用例中提取到测试数据: {case_values}")
            if params_text:
                body = _map_case_values_to_body(body, case_values, params_text)
            else:
                # params 为空时，直接通过语义匹配覆盖 body
                body = _direct_map_case_values(body, case_values)
            dsl["request"]["body"] = body

    # 步骤 3: MD5 加密处理
    # 从 notes、params、description、完整文档文本中检测是否需要 MD5
    all_text = ' '.join([
        notes,
        params_text.lower() if params_text else '',
        (matched_ep.get("description") or "").lower(),
        ep_doc.lower() if ep_doc else ''
    ])
    need_md5 = 'md5' in all_text
    
    if need_md5:
        password_keys = [k for k in body.keys() if k.lower() in ('password', 'pass_word', 'passwd', 'pwd', 'passWord'.lower())]
        if not password_keys:
            password_keys = [k for k in body.keys() if 'pass' in k.lower() or 'pwd' in k.lower()]

        for pk in password_keys:
            val = body[pk]
            if isinstance(val, str) and val and not re.match(r'^[a-f0-9]{32}$', val):
                md5_val = hashlib.md5(val.encode('utf-8')).hexdigest()
                logger.info(f"DSL 后处理: 对字段 {pk} 应用 MD5 加密 ({val} → {md5_val})")
                body[pk] = md5_val

    dsl["request"]["body"] = body

    # 步骤 4: 修复断言中的占位符期望值
    # 文档示例中的值如 "用户ID"、"学生姓名" 是中文占位符，不应作为精确匹配的期望值
    # 将这类断言改为 json_not_null（字段存在且非空）
    assertions = dsl.get("assertions", [])
    for a in assertions:
        if a.get("type") == "json_path":
            exp = a.get("expected")
            if exp is None:
                # expected 为 None，改为 json_not_null
                a["type"] = "json_not_null"
            elif isinstance(exp, str) and re.search(r'[\u4e00-\u9fff]', str(exp)):
                # expected 包含中文（占位符），改为 json_not_null
                a["type"] = "json_not_null"
                a["expected"] = "非空"
    dsl["assertions"] = assertions

    return dsl


def _build_fallback_dsl(ep: Dict, case: ExecutionCase) -> Dict[str, Any]:
    """当 LLM 解析失败时，基于接口文档信息构造基础 DSL"""
    method = ep.get("method", "GET").upper()
    path = ep.get("path", "/")

    query_params = {}
    body = None

    # 尝试从 params 中提取参数名
    params_text = ep.get("params", "") or ""
    if params_text:
        # 匹配 `参数名` 或 `- 参数名: 说明` 格式
        param_names = re.findall(r'`(\w+)`|[-*]\s*(\w+)\s*[:：]', params_text)
        for match in param_names:
            name = match[0] or match[1]
            if name and name not in ('必填', '选填', 'required', 'optional'):
                if method == "GET":
                    query_params[name] = "test"
                else:
                    if body is None:
                        body = {}
                    body[name] = "test"

    # 如果用例有 test_data，用它覆盖
    if case.test_data:
        test_data = case.test_data if isinstance(case.test_data, dict) else {}
        if method == "GET":
            query_params.update(test_data)
        else:
            body = test_data if test_data else body

    # 基于成功响应示例构造断言
    assertions = [{"type": "status_code", "expected": 200}]
    success_example = ep.get("success_example", "")
    if success_example:
        try:
            example_json = json.loads(success_example) if isinstance(success_example, str) else success_example
            if isinstance(example_json, dict):
                if "code" in example_json:
                    assertions.append({"type": "json_path", "path": "$.code", "expected": example_json["code"]})
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "endpoint": {"method": method, "path": path},
        "request": {"query_params": query_params, "headers": {}, "body": body},
        "assertions": assertions
    }


def _execute_http_request(
    dsl: Dict[str, Any],
    base_url: str,
    extra_headers: Dict = None,
    variables: Dict = None
) -> Dict[str, Any]:
    """
    执行 HTTP 请求并验证断言

    构造逻辑：base_url + endpoint.path + 请求参数（来自接口文档）
    """
    endpoint = dsl.get("endpoint", {})
    method = endpoint.get("method", "GET").upper()
    path = endpoint.get("path", "/")
    req_config = dsl.get("request", {})
    assertions = dsl.get("assertions", [])

    # 拼接完整 URL: base_url + path
    url = f"{base_url.rstrip('/')}{path}"

    # 变量替换（支持 {variable} 格式）
    if variables:
        for k, v in variables.items():
            url = url.replace(f"{{{k}}}", str(v))

    # 构造 Headers
    headers = {}
    if extra_headers:
        headers.update(extra_headers)
    if req_config.get("headers"):
        headers.update(req_config["headers"])
    # 只有在有 body 时才设置 Content-Type
    body = req_config.get("body")
    if body and "Content-Type" not in headers and "content-type" not in headers:
        headers["Content-Type"] = "application/json"

    query_params = req_config.get("query_params", {}) or {}

    # 清理空值参数
    query_params = {k: v for k, v in query_params.items() if v is not None and v != ""}

    request_info = {
        "method": method,
        "url": url,
        "headers": {k: v for k, v in headers.items() if k.lower() not in ('authorization', 'api-key', 'x-api-key')},
        "query_params": query_params,
        "body": body
    }

    try:
        resp = http_requests.request(
            method=method,
            url=url,
            params=query_params if query_params else None,
            json=body if body else None,
            headers=headers if headers else None,
            timeout=30,
            allow_redirects=True
        )

        # 尝试解析 JSON
        resp_json = None
        try:
            resp_json = resp.json()
        except Exception:
            pass

        response_info = {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body_trunc": resp.text[:3000] if resp.text else "",
            "json": resp_json
        }

        # 执行断言
        assertion_results = []
        all_passed = True
        for assertion in assertions:
            a_type = assertion.get("type", "")
            passed = False
            actual = None
            expected = assertion.get("expected")

            if a_type == "status_code":
                actual = resp.status_code
                passed = (actual == expected)
            elif a_type == "json_path" and resp_json is not None:
                json_path = assertion.get("path", "")
                actual = _extract_json_path(resp_json, json_path)
                if actual is None:
                    # 路径在响应中不存在，跳过此断言（不算失败）
                    assertion_results.append({
                        "type": a_type,
                        "expected": expected,
                        "actual": None,
                        "passed": True,
                        "skipped": True,
                        "reason": f"路径 {json_path} 在响应中不存在，已跳过"
                    })
                    continue
                # 宽松比较：数字和字符串互转
                if actual != expected:
                    passed = str(actual) == str(expected)
                else:
                    passed = True
            elif a_type == "contains":
                actual = resp.text[:500]
                passed = str(expected) in resp.text if expected else False
            elif a_type == "not_empty":
                actual = bool(resp.text and resp.text.strip())
                passed = actual
            elif a_type == "json_not_null":
                json_path = assertion.get("path", "")
                actual = _extract_json_path(resp_json, json_path) if resp_json else None
                if json_path and actual is None and resp_json is not None:
                    # 路径不存在，跳过
                    assertion_results.append({
                        "type": a_type,
                        "expected": "非空",
                        "actual": None,
                        "passed": True,
                        "skipped": True,
                        "reason": f"路径 {json_path} 在响应中不存在，已跳过"
                    })
                    continue
                passed = actual is not None

            assertion_results.append({
                "type": a_type,
                "expected": expected,
                "actual": actual,
                "passed": passed
            })
            if not passed:
                all_passed = False

        error_message = ""
        if not all_passed:
            failed_assertions = [a for a in assertion_results if not a["passed"]]
            error_message = "; ".join([
                f"{a['type']}: 期望 {a['expected']}, 实际 {a['actual']}"
                for a in failed_assertions
            ])

        return {
            "passed": all_passed,
            "request_info": request_info,
            "response_info": response_info,
            "assertions": assertion_results,
            "error_message": error_message
        }

    except http_requests.exceptions.Timeout:
        return {
            "passed": False,
            "request_info": request_info,
            "response_info": {},
            "assertions": [],
            "error_message": f"请求超时 (30s): {method} {url}"
        }
    except http_requests.exceptions.ConnectionError as e:
        return {
            "passed": False,
            "request_info": request_info,
            "response_info": {},
            "assertions": [],
            "error_message": f"连接失败: {method} {url} - {str(e)[:200]}"
        }
    except Exception as e:
        return {
            "passed": False,
            "request_info": request_info,
            "response_info": {},
            "assertions": [],
            "error_message": f"请求异常: {method} {url} - {str(e)[:200]}"
        }


def _extract_json_path(data: Any, path: str) -> Any:
    """简易 JSON Path 提取 (支持 $.key.key 格式)"""
    if not path or not data:
        return None
    parts = path.replace('$', '').strip('.').split('.')
    current = data
    for part in parts:
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


async def _generate_api_test_report(
    results: List[Dict],
    test_record_ids: List[int],
    total_duration: int,
    db: Session
) -> Optional[int]:
    """生成接口测试报告"""
    pass_count = sum(1 for r in results if r["status"] == "pass")
    fail_count = len(results) - pass_count

    llm = get_llm_client()

    results_text = json.dumps(results, ensure_ascii=False, indent=2)
    system_prompt = "你是一个专业的接口测试报告生成专家。请根据接口测试结果生成简洁专业的 Markdown 测试报告。"
    user_prompt = f"""接口测试结果:
- 总用例: {len(results)}
- 通过: {pass_count}
- 失败: {fail_count}
- 耗时: {total_duration}s

详情:
{results_text[:4000]}

请生成 Markdown 格式的接口测试报告。"""

    try:
        content = await llm.achat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )
    except Exception:
        content = f"# 接口测试报告\n\n通过: {pass_count}, 失败: {fail_count}, 耗时: {total_duration}s"

    report = TestReport(
        project_id=getattr(results[0].get('case') if results else None, 'project_id', None) if results else None,
        title=f"接口测试报告 - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        summary={
            "total": len(results),
            "pass": pass_count,
            "fail": fail_count,
            "pass_rate": round(pass_count / len(results) * 100, 2) if results else 0,
            "duration": total_duration,
            "execution_mode": "接口测试",
            "test_record_ids": test_record_ids
        },
        details=content,
        format_type="markdown",
        total_steps=sum(1 for r in results)
    )
    db.add(report)
    db.commit()
    return report.id


def _create_bug_reports(
    failed_records: List[Dict],
    db: Session
) -> List[int]:
    """为失败用例创建 Bug 报告"""
    bug_ids = []
    for item in failed_records:
        case = item["case"]
        record = item["record"]
        exec_result = item.get("exec_result", {})
        dsl = item.get("dsl", {})

        endpoint = dsl.get("endpoint", {})
        ep_str = f"{endpoint.get('method', '?')} {endpoint.get('path', '?')}"

        bug = BugReport(
            project_id=getattr(case, 'project_id', None),
            test_record_id=record.id,
            test_case_id=case.id,
            bug_name=f"[接口测试] {case.title} - {ep_str} 失败",
            error_type="接口错误",
            severity_level="二级",
            reproduce_steps=f"1. 调用接口 {ep_str}\n2. 请求参数: {json.dumps(dsl.get('request', {}), ensure_ascii=False)[:500]}",
            expected_result=case.expected or "接口正常返回",
            actual_result=exec_result.get("error_message", "未知错误"),
            result_feedback=json.dumps(exec_result.get("response_info", {}), ensure_ascii=False)[:1000],
            status="待处理",
            description=f"接口测试失败: {ep_str}\n错误: {exec_result.get('error_message', '')}",
            case_type="接口测试",
            execution_mode="接口测试"
        )
        db.add(bug)
        db.flush()
        bug_ids.append(bug.id)

    return bug_ids


def _send_bug_email_notification(
    failed_records: List[Dict],
    bug_report_ids: List[int],
    db: Session
):
    """发送 Bug 邮件通知给 auto_receive_bug=1 的联系人"""
    contacts = db.query(Contact).filter(Contact.auto_receive_bug == 1).all()
    if not contacts:
        return

    email_config = db.query(EmailConfig).filter(EmailConfig.is_active == 1).first()
    if not email_config:
        return

    # 构建邮件内容
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    subject = f"[AI Test Agent][BUG] 接口测试失败汇总 - {now_str}"

    rows = ""
    for item in failed_records:
        case = item["case"]
        dsl = item.get("dsl", {})
        exec_result = item.get("exec_result", {})
        ep = dsl.get("endpoint", {})
        rows += f"""<tr>
            <td style="padding:8px;border:1px solid #e5e7eb;">{case.title}</td>
            <td style="padding:8px;border:1px solid #e5e7eb;">{ep.get('method','')} {ep.get('path','')}</td>
            <td style="padding:8px;border:1px solid #e5e7eb;color:#dc2626;">{exec_result.get('error_message','')[:200]}</td>
        </tr>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;background:#f4f5f7;padding:20px;">
<div style="max-width:680px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
  <div style="background:linear-gradient(135deg,#dc2626,#ef4444);padding:24px 32px;color:#fff;">
    <h2 style="margin:0;">接口测试失败通知</h2>
    <p style="margin:6px 0 0;opacity:0.85;font-size:13px;">{now_str} · 共 {len(failed_records)} 条失败</p>
  </div>
  <div style="padding:24px 32px;">
    <table width="100%" style="border-collapse:collapse;font-size:13px;">
      <tr style="background:#f8fafc;">
        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">用例</th>
        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">接口</th>
        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">错误</th>
      </tr>
      {rows}
    </table>
    <p style="margin-top:16px;font-size:12px;color:#94a3b8;">Bug ID: {', '.join(str(i) for i in bug_report_ids)}</p>
  </div>
  <div style="background:#f8fafc;padding:12px 32px;text-align:center;font-size:12px;color:#94a3b8;">
    此邮件由 AI 测试平台自动发送
  </div>
</div></body></html>"""

    from Email_manage.sender import dispatch_send
    recipients_result = []
    success_count = 0
    failed_count = 0

    for contact in contacts:
        to_email = email_config.test_email if email_config.test_mode == 1 and email_config.test_email else contact.email
        try:
            dispatch_send(email_config, to_email, subject, html)
            success_count += 1
            recipients_result.append({"name": contact.name, "email": contact.email, "status": "success"})
        except Exception as e:
            failed_count += 1
            recipients_result.append({"name": contact.name, "email": contact.email, "status": "failed", "error": str(e)})

    record = EmailRecord(
        subject=subject,
        recipients=recipients_result,
        status='success' if failed_count == 0 else ('partial' if success_count > 0 else 'failed'),
        success_count=success_count,
        failed_count=failed_count,
        total_count=len(contacts),
        email_type='bug_notification',
        content_summary=f"接口测试失败通知: {len(failed_records)} 条失败"
    )
    db.add(record)
    db.commit()
