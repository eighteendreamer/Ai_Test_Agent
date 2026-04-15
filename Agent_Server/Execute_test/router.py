"""
测试执行 API 路由

作者: 程序员Eighteen
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from database.connection import get_db, get_default_project

router = APIRouter(
    prefix="/api/test-code",
    tags=["测试执行"]
)


class ExecuteRequest(BaseModel):
    test_case_id: int
    headless: Optional[bool] = None
    max_steps: Optional[int] = None
    use_vision: Optional[bool] = None


class BatchExecuteRequest(BaseModel):
    test_case_ids: List[int]
    headless: Optional[bool] = None
    max_steps: Optional[int] = None
    use_vision: Optional[bool] = None


class BrowserUseRequest(BaseModel):
    """Browser-use 执行请求"""
    test_case_id: int
    headless: Optional[bool] = True
    max_steps: Optional[int] = 20
    use_vision: Optional[bool] = False


@router.get("/test-browser")
async def test_browser_connection():
    """
    测试浏览器连接是否正常
    
    用于诊断 browser-use 浏览器启动问题
    """
    try:
        from Execute_test.service import BrowserUseService
        
        result = await BrowserUseService.test_browser_connection()
        return result
    except Exception as e:
        import traceback
        return {
            "success": False,
            "message": f"测试失败: {str(e)}",
            "error_details": traceback.format_exc()
        }


@router.post("/execute-browser-use")
async def execute_browser_use(
    request: BrowserUseRequest,
    db: Session = Depends(get_db)
):
    """使用 browser-use 执行测试"""
    try:
        from Execute_test.service import BrowserUseService
        from database.connection import ExecutionCase, get_active_project_by_id
        from Test_Tools.task_manager import get_task_manager

        # 验证用例所属项目是否启用
        case = db.query(ExecutionCase).filter(ExecutionCase.id == request.test_case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="测试用例不存在")

        if case.project_id:
            project = get_active_project_by_id(db, case.project_id)
            if not project:
                raise HTTPException(status_code=400, detail="用例所属项目未启用")

        # 注册任务到 TaskManager，使任务控制链生效
        task_manager = get_task_manager()
        task_id = task_manager.create_task(
            task_type="browser_use",
            description=f"执行用例 {request.test_case_id}: {case.title if hasattr(case, 'title') else ''}"
        )
        from Test_Tools.task_manager import TaskStatus
        task_manager.update_task_status(task_id, status=TaskStatus.RUNNING)

        try:
            result = await BrowserUseService.execute_test_with_browser_use(
                test_case_id=request.test_case_id,
                db=db,
                headless=request.headless,
                max_steps=request.max_steps,
                use_vision=request.use_vision,
                task_id=task_id
            )
        finally:
            # 执行结束后确保任务状态同步
            current = task_manager.get_task(task_id)
            if current and current["status"] == TaskStatus.RUNNING:
                task_manager.update_task_status(task_id, status=TaskStatus.COMPLETED)

        if isinstance(result, dict) and result.get("data") is not None:
            result["data"]["task_id"] = task_id
        elif isinstance(result, dict):
            result["task_id"] = task_id

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行测试失败: {str(e)}")


@router.post("/execute")
async def execute_single_test(
    request: ExecuteRequest,
    db: Session = Depends(get_db)
):
    """执行单条测试用例"""
    try:
        from Execute_test.service import BrowserUseService
        from database.connection import ExecutionCase, get_active_project_by_id
        
        # 验证用例所属项目是否启用
        case = db.query(ExecutionCase).filter(ExecutionCase.id == request.test_case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail="测试用例不存在")
        
        if case.project_id:
            project = get_active_project_by_id(db, case.project_id)
            if not project:
                raise HTTPException(status_code=400, detail="用例所属项目未启用")
        
        result = await BrowserUseService.execute_test_with_browser_use(
            test_case_id=request.test_case_id,
            db=db,
            headless=request.headless,
            max_steps=request.max_steps,
            use_vision=request.use_vision
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行测试失败: {str(e)}")


@router.post("/execute-batch-browser-use")
async def execute_batch_browser_use(
    request: BatchExecuteRequest,
    db: Session = Depends(get_db)
):
    """批量执行测试用例（browser-use 模式）"""
    try:
        from Execute_test.service import BrowserUseService
        from database.connection import ExecutionCase, get_active_project_by_id
        
        # 验证所有用例所属项目是否启用
        for case_id in request.test_case_ids:
            case = db.query(ExecutionCase).filter(ExecutionCase.id == case_id).first()
            if not case:
                raise HTTPException(status_code=404, detail=f"测试用例 {case_id} 不存在")
            
            if case.project_id:
                project = get_active_project_by_id(db, case.project_id)
                if not project:
                    raise HTTPException(status_code=400, detail=f"用例 {case_id} 所属项目未启用")
        
        result = await BrowserUseService.execute_batch_test_cases(
            test_case_ids=request.test_case_ids,
            db=db,
            headless=request.headless,
            max_steps=request.max_steps,
            use_vision=request.use_vision
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量执行测试失败: {str(e)}")


@router.post("/batch")
async def execute_batch_tests(
    request: BatchExecuteRequest,
    db: Session = Depends(get_db)
):
    """批量执行测试用例"""
    try:
        from Execute_test.service import BrowserUseService
        from database.connection import ExecutionCase, get_active_project_by_id
        
        # 验证所有用例所属项目是否启用
        for case_id in request.test_case_ids:
            case = db.query(ExecutionCase).filter(ExecutionCase.id == case_id).first()
            if not case:
                raise HTTPException(status_code=404, detail=f"测试用例 {case_id} 不存在")
            
            if case.project_id:
                project = get_active_project_by_id(db, case.project_id)
                if not project:
                    raise HTTPException(status_code=400, detail=f"用例 {case_id} 所属项目未启用")
        
        result = await BrowserUseService.execute_batch_test_cases(
            test_case_ids=request.test_case_ids,
            db=db,
            headless=request.headless,
            max_steps=request.max_steps,
            use_vision=request.use_vision
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量执行测试失败: {str(e)}")

@router.post("/stop-task/{task_id}")
@router.post("/stop/{task_id}")
def stop_test(task_id: int):
    """停止测试执行"""
    try:
        from Test_Tools.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        task_manager.stop_task(task_id)
        
        return {
            "success": True,
            "message": f"已发送停止信号到任务 {task_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"停止测试失败: {str(e)}")


@router.post("/pause-task/{task_id}")
@router.post("/pause/{task_id}")
def pause_test(task_id: int):
    """暂停测试执行"""
    try:
        from Test_Tools.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        task_manager.pause_task(task_id)
        
        return {
            "success": True,
            "message": f"已暂停任务 {task_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"暂停测试失败: {str(e)}")


@router.post("/resume-task/{task_id}")
@router.post("/resume/{task_id}")
def resume_test(task_id: int):
    """恢复测试执行"""
    try:
        from Test_Tools.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        task_manager.resume_task(task_id)
        
        return {
            "success": True,
            "message": f"已恢复任务 {task_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"恢复测试失败: {str(e)}")


@router.get("/task-status/{task_id}")
@router.get("/status/{task_id}")
def get_task_status(task_id: int):
    """获取任务状态"""
    try:
        from Test_Tools.task_manager import get_task_manager
        
        task_manager = get_task_manager()
        status = task_manager.get_task_status(task_id)
        
        return {
            "success": True,
            "data": status
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")
