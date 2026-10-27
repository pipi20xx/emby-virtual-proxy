# src/main.py
import os
import uvicorn
import sys

# 生产环境默认关闭 reload:watchfiles 常驻轮询会持续占 CPU,且任何源码变动都会
# 静默重启进程 —— 代理一重启,内存里的全库数据源/结果层缓存就全丢。本地开发用
# UVICORN_RELOAD=1 临时打开。
_RELOAD = os.getenv("UVICORN_RELOAD", "").strip().lower() in ("1", "true", "yes")


def start_admin():
    """启动管理服务器"""
    print("--- Starting Admin Server ---")
    print("Access the Web UI at http://127.0.0.1:8011 (or your host IP)")
    print("API documentation at http://127.0.0.1:8011/docs")
    uvicorn.run("admin_server:admin_app", host="0.0.0.0", port=8001, reload=_RELOAD)


def start_proxy():
    """启动代理服务器"""
    print("--- Starting Proxy Server ---")
    print("Proxy is listening on http://0.0.0.0:8999")
    uvicorn.run("proxy_server:proxy_app", host="0.0.0.0", port=8999, reload=_RELOAD)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "admin":
            start_admin()
        elif sys.argv[1] == "proxy":
            start_proxy()
        else:
            print(f"Unknown command: {sys.argv[1]}")
            print("Available commands: admin, proxy")
    else:
        print("Please specify a service to start.")
        print("Available commands: admin, proxy")