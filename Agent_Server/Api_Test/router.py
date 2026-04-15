"""
接口测试 API 路由

作者: 程序员Eighteen
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional, Dict

from database.connection import get_db

router = APIRouter(
    prefix="/api/api-test",
    tags=["接口测试"]
)


class MatchSpecRequest(BaseModel):
    test_case_ids: List[int]
    top_k: int = 5
    service_name: Optional[str] = None


class MatchEndpointsRequest(BaseModel):
    test_case_ids: List[int]
    spec_version_id: int


class ExecuteRequest(BaseModel):
    test_case_ids: List[int]
    spec_version_id: int
    environment: Optional[Dict] = None
    mode: str = "llm_enhanced"
    case_endpoint_map: Optional[Dict] = None



@router.post("/match-spec")
def match_spec(
    request: MatchSpecRequest,
    db: Session = Depends(get_db)
):
    """智能匹配接口文件"""
    from Api_Test.service import ApiTestService

    result = ApiTestService.match_spec(
        test_case_ids=request.test_case_ids,
        db=db,
        top_k=request.top_k,
        service_name=request.service_name
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))

    return result


@router.post("/match-endpoints")
def match_endpoints(
    request: MatchEndpointsRequest,
    db: Session = Depends(get_db)
):
    """为每条用例匹配具体的 endpoint"""
    from Api_Test.service import ApiTestService

    result = ApiTestService.match_case_endpoints(
        test_case_ids=request.test_case_ids,
        spec_version_id=request.spec_version_id,
        db=db
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message"))

    return result


@router.post("/execute")
async def execute_api_test(
    request: ExecuteRequest,
    db: Session = Depends(get_db)
):
    """确认后执行接口测试"""
    from Api_Test.service import ApiTestService

    result = await ApiTestService.execute_api_test(
        test_case_ids=request.test_case_ids,
        spec_version_id=request.spec_version_id,
        db=db,
        environment=request.environment,
        mode=request.mode,
        case_endpoint_map=request.case_endpoint_map
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result
