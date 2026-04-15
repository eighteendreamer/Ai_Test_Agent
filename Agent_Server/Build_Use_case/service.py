"""
测试用例生成服务

作者: 程序员Eighteen
"""
import os
import csv
import json
import io
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from dotenv import load_dotenv

# 加载环境变量 - .env 文件在 Agent_Server 目录下
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)

SAVE_FOLDER_DIR = os.getenv('SAVE_FOLDER_DIR', '../save_floder')

# CSV 模板字段
TEMPLATE_FIELDS = [
    "module", "title", "precondition", "steps", "expected",
    "keywords", "priority", "case_type", "stage"
]


class TestCaseService:
    """测试用例生成服务"""
    
    @staticmethod
    async def generate_test_cases(
        requirement: str,
        db: Session,
        project_id: int = None
    ) -> Dict[str, Any]:
        """
        根据需求生成测试用例
        
        Args:
            requirement: 用户需求或测试点
            db: 数据库会话
        
        Returns:
            生成的测试用例数据
        """
        try:
            if project_id is None:
                return {
                    "success": False,
                    "message": "project_id is required when generating test cases",
                    "test_cases": []
                }
            # 使用新的 LLM 模块
            from llm import get_llm_client
            
            llm_client = get_llm_client()
            
            # 调用 LLM 生成测试用例（传入 db 以启用模板自适应）
            result = llm_client.generate_test_cases(
                requirement=requirement,
                priority="3",
                db=db,
            )
            
            if not result.get('success'):
                error_msg = result.get('message', '生成测试用例失败')
                print(f"[ERROR] LLM 生成失败: {error_msg}")
                return {
                    "success": False,
                    "message": error_msg,
                    "test_cases": []
                }
            
            test_cases_data = result.get('test_cases', [])
            
            if not test_cases_data:
                return {
                    "success": False,
                    "message": "未生成任何测试用例",
                    "test_cases": []
                }
            
            # 保存到数据库和 CSV
            from database.connection import ExecutionCase
            
            saved_cases = []
            csv_file_path = TestCaseService._save_to_csv(test_cases_data)
            
            for case_data in test_cases_data:
                test_case = ExecutionCase(
                    project_id=project_id,
                    module=case_data.get('module', ''),
                    title=case_data.get('title', ''),
                    precondition=case_data.get('precondition', ''),
                    steps=json.dumps(case_data.get('steps', []), ensure_ascii=False),
                    expected=case_data.get('expected', ''),
                    keywords=case_data.get('keywords', ''),
                    priority=case_data.get('priority', '3'),
                    case_type=case_data.get('case_type', ''),
                    stage=case_data.get('stage', ''),
                    test_data=case_data.get('test_data', {}),
                    csv_file_path=csv_file_path
                )
                
                db.add(test_case)
                db.commit()
                db.refresh(test_case)
                
                saved_cases.append({
                    "id": test_case.id,
                    "module": test_case.module,
                    "title": test_case.title,
                    "precondition": test_case.precondition,
                    "steps": json.loads(test_case.steps),
                    "expected": test_case.expected,
                    "keywords": test_case.keywords,
                    "priority": test_case.priority,
                    "case_type": test_case.case_type,
                    "stage": test_case.stage,
                    "test_data": test_case.test_data,
                    "created_at": test_case.created_at.isoformat() if test_case.created_at else None
                })
            
            return {
                "success": True,
                "message": f"成功生成 {len(saved_cases)} 个测试用例",
                "test_cases": saved_cases,
                "csv_file_path": csv_file_path
            }
        
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            print(f"[ERROR] 生成测试用例异常: {str(e)}")
            print(error_traceback)
            return {
                "success": False,
                "message": f"异常: {str(e)}",
                "test_cases": [],
                "error_details": error_traceback
            }
    
    @staticmethod
    def _save_to_csv(test_cases: List[Dict[str, Any]]) -> str:
        """保存测试用例到 CSV 文件"""
        os.makedirs(SAVE_FOLDER_DIR, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"test_case_{timestamp}.csv"
        file_path = os.path.join(SAVE_FOLDER_DIR, filename)
        
        with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            
            headers = [
                "模块", "用例名称", "前置条件", "测试步骤", "预期结果",
                "关键词", "优先级", "用例类型", "适用阶段"
            ]
            writer.writerow(headers)
            
            for case in test_cases:
                steps = case.get('steps', [])
                steps_str = '\n'.join([f"{i+1}. {step}" for i, step in enumerate(steps)])
                
                row = [
                    case.get('module', ''),
                    case.get('title', ''),
                    case.get('precondition', ''),
                    steps_str,
                    case.get('expected', ''),
                    case.get('keywords', ''),
                    case.get('priority', '3'),
                    case.get('case_type', ''),
                    case.get('stage', '')
                ]
                writer.writerow(row)
        
        return file_path
    
    @staticmethod
    def get_test_cases(
        db: Session,
        limit: int = 20,
        offset: int = 0,
        module: str = None,
        search: str = None,
        priority: str = None,
        case_type: str = None,
        project_id: int = None
    ) -> Dict[str, Any]:
        """获取测试用例列表"""
        from database.connection import ExecutionCase
        
        query = db.query(ExecutionCase)
        
        # 项目过滤
        if project_id is not None:
            query = query.filter(ExecutionCase.project_id == project_id)
        
        if module:
            query = query.filter(ExecutionCase.module.like(f"%{module}%"))
        if search:
            query = query.filter(ExecutionCase.title.like(f"%{search}%"))
        if priority:
            query = query.filter(ExecutionCase.priority == priority)
        if case_type:
            query = query.filter(ExecutionCase.case_type == case_type)
        
        total = query.count()
        cases = query.order_by(ExecutionCase.id.desc()).limit(limit).offset(offset).all()
        
        data = [
            {
                "id": case.id,
                "module": case.module,
                "title": case.title,
                "precondition": case.precondition,
                "steps": json.loads(case.steps) if case.steps else [],
                "expected": case.expected,
                "keywords": case.keywords,
                "priority": case.priority,
                "case_type": case.case_type,
                "stage": case.stage,
                "test_data": case.test_data,
                "created_at": case.created_at.isoformat() if case.created_at else None
            }
            for case in cases
        ]
        
        return {"data": data, "total": total}
    
    @staticmethod
    def get_test_case_by_id(db: Session, case_id: int) -> Dict[str, Any]:
        """获取单个测试用例"""
        from database.connection import ExecutionCase
        
        case = db.query(ExecutionCase).filter(ExecutionCase.id == case_id).first()
        
        if not case:
            return None
        
        return {
            "id": case.id,
            "module": case.module,
            "title": case.title,
            "precondition": case.precondition,
            "steps": json.loads(case.steps) if case.steps else [],
            "expected": case.expected,
            "keywords": case.keywords,
            "priority": case.priority,
            "case_type": case.case_type,
            "stage": case.stage,
            "test_data": case.test_data,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "csv_file_path": case.csv_file_path
        }
    
    @staticmethod
    def update_test_case(
        db: Session,
        case_id: int,
        module: str,
        title: str,
        precondition: str,
        steps: List[str],
        expected: str,
        keywords: str,
        priority: str,
        case_type: str,
        stage: str
    ) -> Dict[str, Any]:
        """更新测试用例"""
        from database.connection import ExecutionCase
        
        try:
            case = db.query(ExecutionCase).filter(ExecutionCase.id == case_id).first()
            
            if not case:
                return {
                    "success": False,
                    "message": f"测试用例 ID {case_id} 不存在"
                }
            
            case.module = module
            case.title = title
            case.precondition = precondition
            case.steps = json.dumps(steps, ensure_ascii=False)
            case.expected = expected
            case.keywords = keywords
            case.priority = priority
            case.case_type = case_type
            case.stage = stage
            
            db.commit()
            db.refresh(case)
            
            return {
                "success": True,
                "message": "测试用例更新成功",
                "data": {
                    "id": case.id,
                    "module": case.module,
                    "title": case.title,
                    "updated_at": case.updated_at.isoformat() if case.updated_at else None
                }
            }
        
        except Exception as e:
            db.rollback()
            return {
                "success": False,
                "message": f"更新测试用例失败: {str(e)}"
            }
    
    @staticmethod
    async def process_uploaded_file(
        filename: str,
        content: bytes,
        db: Session,
        project_id: int = None
    ) -> Dict[str, Any]:
        """处理上传的文件并生成测试用例"""
        try:
            file_ext = os.path.splitext(filename)[1].lower()
            
            if file_ext == '.txt' or file_ext == '.md':
                text_content = content.decode('utf-8')
            elif file_ext == '.pdf':
                text_content = TestCaseService._extract_text_from_pdf(content)
            elif file_ext == '.docx':
                text_content = TestCaseService._extract_text_from_docx(content)
            else:
                return {
                    "success": False,
                    "message": f"不支持的文件格式: {file_ext}"
                }
            
            if not text_content or not text_content.strip():
                return {
                    "success": False,
                    "message": "文件内容为空或无法解析"
                }
            
            # 保存上传的文件
            upload_dir = os.path.join(SAVE_FOLDER_DIR, 'uploads')
            os.makedirs(upload_dir, exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            saved_filename = f"{timestamp}_{filename}"
            saved_path = os.path.join(upload_dir, saved_filename)
            
            with open(saved_path, 'wb') as f:
                f.write(content)
            
            # 使用解析的文本内容生成测试用例
            result = await TestCaseService.generate_test_cases(
                requirement=text_content,
                db=db,
                project_id=project_id
            )
            
            if result.get('success'):
                result['uploaded_file'] = saved_path
                result['message'] = f"成功从文件 '{filename}' 生成 {len(result.get('test_cases', []))} 个测试用例"
            
            return result
        
        except Exception as e:
            import traceback
            return {
                "success": False,
                "message": f"处理文件失败: {str(e)}",
                "error_details": traceback.format_exc()
            }
    
    @staticmethod
    def _extract_text_from_pdf(content: bytes) -> str:
        """从 PDF 提取文本"""
        try:
            import PyPDF2
            pdf_file = io.BytesIO(content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            
            return text.strip()
        except ImportError:
            return "错误：需要安装 PyPDF2 库"
        except Exception as e:
            return f"PDF 解析错误: {str(e)}"
    
    @staticmethod
    def _extract_text_from_docx(content: bytes) -> str:
        """从 DOCX 提取文本"""
        try:
            from docx import Document
            docx_file = io.BytesIO(content)
            doc = Document(docx_file)
            
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
            
            return text.strip()
        except ImportError:
            return "错误：需要安装 python-docx 库"
        except Exception as e:
            return f"DOCX 解析错误: {str(e)}"
