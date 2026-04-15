"""
MinIO 客户端封装

作者: 程序员Eighteen
"""
import os
from minio import Minio
from dotenv import load_dotenv

# 加载环境变量 - .env 文件在 Agent_Server 目录下
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)


def get_minio_client() -> Minio:
    """获取 MinIO 客户端实例"""
    endpoint = os.getenv('MINIO_ENDPOINT')
    access_key = os.getenv('MINIO_ACCESS_KEY')
    secret_key = os.getenv('MINIO_SECRET_KEY')
    secure = os.getenv('MINIO_SECURE', 'false').lower() == 'true'
    
    # 验证必需的环境变量
    if not all([endpoint, access_key, secret_key]):
        raise ValueError(
            "MinIO 配置缺失！请在 .env 文件中配置以下环境变量：\n"
            "MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY"
        )

    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure
    )


def get_bucket_name() -> str:
    bucket = os.getenv('MINIO_BUCKET')
    if not bucket:
        raise ValueError("MinIO bucket 配置缺失！请在 .env 文件中配置 MINIO_BUCKET")
    return bucket


def ensure_bucket():
    """确保 bucket 存在"""
    client = get_minio_client()
    bucket = get_bucket_name()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    return client, bucket
