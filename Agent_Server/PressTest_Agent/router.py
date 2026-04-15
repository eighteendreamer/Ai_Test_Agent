"""
PressTest Agent API 路由

提供压测任务的 REST API 接口
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
import logging
from datetime import datetime

from database.connection import get_db, PressTestTask
from .models import (
    CreateTaskRequest, TaskResponse, TaskProgressResponse,
    TaskResultResponse, EngineSelectionResponse, TaskStatus
)
from .agents.orchestrator import PressTestOrchestrator

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/presstest", tags=["PressTest"])


# ============================================================
# 任务管理 API
# ============================================================

@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    request: CreateTaskRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    创建压测任务
    
    支持三种方式：
    1. 直接提供 DSL
    2. 提供测试用例描述（由 AI 解析）
    3. 提供原始配置
    """
    try:
        # 创建 Orchestrator
        from llm import get_active_llm_provider
        llm_provider = get_active_llm_provider(db)
        orchestrator = PressTestOrchestrator(llm_provider)
        
        # 解析 DSL
        if request.dsl:
            dsl = request.dsl
        elif request.test_case:
            # 使用 AI 解析测试用例
            dsl = orchestrator.parse_test_case(request.test_case)
        elif request.raw_config:
            # 从原始配置转换
            from .models import PressTestDSL
            dsl = PressTestDSL(**request.raw_config)
        else:
            raise HTTPException(status_code=400, detail="必须提供 dsl、test_case 或 raw_config 之一")
        
        # 选择引擎
        selection = orchestrator.select_engine(dsl)
        
        # 创建数据库记录
        task = PressTestTask(
            project_id=request.project_id,
            name=request.name or dsl.name,
            description=request.description or dsl.description,
            engine=selection.recommended_engine.value,
            dsl_config=dsl.dict(),
            status=TaskStatus.CREATED.value,
            created_at=datetime.now(),
        )
        
        db.add(task)
        db.commit()
        db.refresh(task)
        
        logger.info(f"创建压测任务: ID={task.id}, name={task.name}, engine={task.engine}")
        
        # 后台执行任务
        background_tasks.add_task(execute_task_background, task.id, dsl, db)
        
        return TaskResponse(
            id=task.id,
            name=task.name,
            status=TaskStatus(task.status),
            engine=selection.recommended_engine,
            created_at=task.created_at.isoformat(),
            started_at=None,
            finished_at=None,
            duration=None,
            error_message=None,
        )
        
    except Exception as e:
        logger.error(f"创建任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks", response_model=List[TaskResponse])
async def list_tasks(
    project_id: Optional[int] = None,
    status: Optional[TaskStatus] = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """获取任务列表"""
    query = db.query(PressTestTask)
    
    if project_id:
        query = query.filter(PressTestTask.project_id == project_id)
    
    if status:
        query = query.filter(PressTestTask.status == status.value)
    
    tasks = query.order_by(PressTestTask.created_at.desc()).offset(offset).limit(limit).all()
    
    return [
        TaskResponse(
            id=task.id,
            name=task.name,
            status=TaskStatus(task.status),
            engine=task.engine,
            created_at=task.created_at.isoformat(),
            started_at=task.started_at.isoformat() if task.started_at else None,
            finished_at=task.finished_at.isoformat() if task.finished_at else None,
            duration=task.duration,
            error_message=task.error_message,
        )
        for task in tasks
    ]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: int, db: Session = Depends(get_db)):
    """获取任务详情"""
    task = db.query(PressTestTask).get(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return TaskResponse(
        id=task.id,
        name=task.name,
        status=TaskStatus(task.status),
        engine=task.engine,
        created_at=task.created_at.isoformat(),
        started_at=task.started_at.isoformat() if task.started_at else None,
        finished_at=task.finished_at.isoformat() if task.finished_at else None,
        duration=task.duration,
        error_message=task.error_message,
    )


@router.get("/tasks/{task_id}/progress", response_model=TaskProgressResponse)
async def get_task_progress(task_id: int, db: Session = Depends(get_db)):
    """获取任务进度"""
    task = db.query(PressTestTask).get(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 计算进度
    progress = 0
    if task.status == TaskStatus.FINISHED.value:
        progress = 100
    elif task.status == TaskStatus.RUNNING.value:
        # TODO: 从实时指标计算进度
        progress = 50
    
    return TaskProgressResponse(
        task_id=task.id,
        status=TaskStatus(task.status),
        progress=progress,
        current_vus=None,
        current_rps=None,
        elapsed_time=None,
    )


@router.get("/tasks/{task_id}/result", response_model=TaskResultResponse)
async def get_task_result(task_id: int, db: Session = Depends(get_db)):
    """获取任务结果"""
    task = db.query(PressTestTask).get(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status != TaskStatus.FINISHED.value:
        raise HTTPException(status_code=400, detail="任务尚未完成")
    
    # 从数据库读取结果
    result = task.result or {}
    
    return TaskResultResponse(
        task_id=task.id,
        status=TaskStatus(task.status),
        total_requests=result.get('total_requests', 0),
        total_errors=result.get('total_errors', 0),
        duration=result.get('duration', 0),
        avg_rps=result.get('avg_rps', 0),
        max_rps=result.get('max_rps', 0),
        avg_response_time=result.get('avg_response_time', 0),
        p95_response_time=result.get('p95_response_time', 0),
        p99_response_time=result.get('p99_response_time', 0),
        error_rate=result.get('error_rate', 0),
        metrics=result.get('metrics', []),
        ai_analysis=result.get('ai_analysis'),
        recommendations=result.get('recommendations', []),
    )


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, db: Session = Depends(get_db)):
    """删除任务"""
    task = db.query(PressTestTask).get(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 只能删除已完成或失败的任务
    if task.status in [TaskStatus.RUNNING.value, TaskStatus.WAITING.value]:
        raise HTTPException(status_code=400, detail="无法删除运行中的任务")
    
    db.delete(task)
    db.commit()
    
    return {"message": "任务已删除"}


@router.post("/tasks/{task_id}/send-report")
async def send_report_email(task_id: int, db: Session = Depends(get_db)):
    """
    手动发送压测报告邮件
    
    用于重新发送或补发报告
    """
    task = db.query(PressTestTask).get(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status != TaskStatus.FINISHED.value:
        raise HTTPException(status_code=400, detail="只能发送已完成任务的报告")
    
    try:
        from llm import get_active_llm_provider
        from .email_service import PressTestEmailService
        from .models import PressTestDSL
        
        # 重建 DSL
        dsl = PressTestDSL(**task.dsl_config)
        
        # 重建结果
        result_dict = task.result or {}
        result = TaskResultResponse(
            task_id=task.id,
            status=TaskStatus(task.status),
            total_requests=result_dict.get('total_requests', 0),
            total_errors=result_dict.get('total_errors', 0),
            duration=result_dict.get('duration', 0),
            avg_rps=result_dict.get('avg_rps', 0),
            max_rps=result_dict.get('max_rps', 0),
            avg_response_time=result_dict.get('avg_response_time', 0),
            p95_response_time=result_dict.get('p95_response_time', 0),
            p99_response_time=result_dict.get('p99_response_time', 0),
            error_rate=result_dict.get('error_rate', 0),
            metrics=result_dict.get('metrics', []),
            ai_analysis=result_dict.get('ai_analysis'),
            recommendations=result_dict.get('recommendations', []),
        )
        
        # 发送邮件
        llm_provider = get_active_llm_provider(db)
        email_service = PressTestEmailService(db, llm_provider)
        email_result = email_service.send_report(
            task_id=task.id,
            task_name=task.name,
            dsl=dsl,
            result=result,
            created_at=task.created_at,
            finished_at=task.finished_at
        )
        
        return {
            "success": email_result['success'],
            "message": email_result['message'],
            "sent_count": email_result['sent_count'],
            "failed_count": email_result['failed_count']
        }
        
    except Exception as e:
        logger.error(f"发送报告失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 辅助功能 API
# ============================================================

@router.post("/parse", response_model=dict)
async def parse_test_case(test_case: str, db: Session = Depends(get_db)):
    """
    解析测试用例（AI 驱动）
    
    输入自然语言描述，输出标准 DSL
    """
    try:
        from llm import get_active_llm_provider
        llm_provider = get_active_llm_provider(db)
        orchestrator = PressTestOrchestrator(llm_provider)
        
        dsl = orchestrator.parse_test_case(test_case)
        
        return dsl.dict()
        
    except Exception as e:
        logger.error(f"解析测试用例失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/select-engine", response_model=EngineSelectionResponse)
async def select_engine(dsl: dict, db: Session = Depends(get_db)):
    """
    选择最优引擎（AI 驱动）
    """
    try:
        from llm import get_active_llm_provider
        from .models import PressTestDSL
        
        llm_provider = get_active_llm_provider(db)
        orchestrator = PressTestOrchestrator(llm_provider)
        
        dsl_obj = PressTestDSL(**dsl)
        selection = orchestrator.select_engine_with_llm(dsl_obj)
        
        return selection
        
    except Exception as e:
        logger.error(f"选择引擎失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 后台任务执行
# ============================================================

def execute_task_background(task_id: int, dsl, db: Session):
    """后台执行压测任务"""
    from database.connection import SessionLocal
    
    # 创建新的数据库会话
    db = SessionLocal()
    
    try:
        task = db.query(PressTestTask).get(task_id)
        if not task:
            logger.error(f"任务不存在: {task_id}")
            return
        
        # 更新状态为运行中
        task.status = TaskStatus.RUNNING.value
        task.started_at = datetime.now()
        db.commit()
        
        logger.info(f"开始执行任务: {task_id}")
        
        # 创建 Orchestrator
        from llm import get_active_llm_provider
        llm_provider = get_active_llm_provider(db)
        orchestrator = PressTestOrchestrator(llm_provider)
        
        # 执行压测
        result = orchestrator.dispatch(task_id, dsl)
        
        # AI 分析结果
        analysis = orchestrator.analyze_result(result)
        
        # 保存结果
        task.status = TaskStatus.FINISHED.value
        task.finished_at = datetime.now()
        task.duration = result.duration
        task.result = result.dict()
        db.commit()
        
        logger.info(f"任务执行完成: {task_id}")
        
        # 发送邮件报告
        try:
            from .email_service import PressTestEmailService
            
            email_service = PressTestEmailService(db, llm_provider)
            email_result = email_service.send_report(
                task_id=task.id,
                task_name=task.name,
                dsl=dsl,
                result=result,
                created_at=task.created_at,
                finished_at=task.finished_at
            )
            
            logger.info(f"邮件发送结果: {email_result['message']}")
            
        except Exception as e:
            logger.error(f"发送邮件报告失败: {e}")
            # 邮件发送失败不影响任务状态
        
    except Exception as e:
        logger.error(f"任务执行失败: {task_id}, error: {e}")
        
        task = db.query(PressTestTask).get(task_id)
        if task:
            task.status = TaskStatus.FAILED.value
            task.finished_at = datetime.now()
            task.error_message = str(e)
            db.commit()
    
    finally:
        db.close()
