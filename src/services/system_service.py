# src/services/system_service.py —— 系统级管理操作
"""与业务数据无关的系统操作:通过 Docker API 管理代理核心容器。"""

import logging
import os

import docker
from fastapi import HTTPException, Response

logger = logging.getLogger(__name__)


async def restart_proxy_container() -> Response:
    """通过 Docker API 重启代理核心容器,顺带清空其内存缓存。"""
    proxy_container_name = os.getenv("PROXY_CONTAINER_NAME")
    if not proxy_container_name:
        logger.error("环境变量 PROXY_CONTAINER_NAME 未设置。")
        raise HTTPException(
            status_code=500,
            detail="Admin服务未配置目标容器名称，无法执行重启。"
        )

    try:
        # 通过挂载的 socket 连接到 Docker 守护进程
        client = docker.from_env()
        logger.info(f"正在查找要重启的容器: '{proxy_container_name}'")
        proxy_container = client.containers.get(proxy_container_name)
        logger.info("找到容器，正在执行重启...")
        proxy_container.restart()
        logger.info(f"重启命令已发送至容器 '{proxy_container_name}'。")
        return Response(status_code=204)

    except docker.errors.NotFound:
        logger.error(f"找不到名为 '{proxy_container_name}' 的容器。")
        raise HTTPException(
            status_code=404,
            detail=f"找不到名为 '{proxy_container_name}' 的容器。请检查 docker-compose.yml 配置。"
        )
    except docker.errors.APIError as e:
        logger.error(f"与 Docker API 通信时出错: {e}")
        raise HTTPException(
            status_code=502,  # Bad Gateway,表示代理角色执行上游操作失败
            detail=f"与 Docker API 通信时出错: {e}"
        )
    except Exception as e:
        logger.error(f"重启容器时发生未知错误: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"重启容器时发生未知内部错误: {e}"
        )
