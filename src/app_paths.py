# src/app_paths.py —— 文件系统路径唯一定义处
"""所有目录/文件路径在此集中定义。

统一规则:以本文件所在目录(src/)为基准向上定位。
  - 本地源码运行: <仓库根>/config 等
  - Docker 运行:  /app/src/.. -> /app/config 等
两种环境得到相同结果,取代散落各处的 Path("/app/...") 硬编码。

注意:proxy_handlers 下仍存在旧的硬编码路径,属于代理核心,
将在代理侧重构时统一切换到本模块,本轮刻意不改动。
"""

from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent      # .../src (容器内 /app/src)
PROJECT_ROOT = SRC_DIR.parent                  # 仓库根 (容器内 /app)

CONFIG_DIR = PROJECT_ROOT / "config"           # 容器内 /app/config (docker-compose 挂载点)
CONFIG_FILE_PATH = CONFIG_DIR / "config.json"
COVERS_DIR = CONFIG_DIR / "images"             # 生成的虚拟库封面
TEMP_IMAGE_DIR = CONFIG_DIR / "temp_images"    # 前端上传的临时封面素材
LOG_DIR = CONFIG_DIR / "logs"                  # 运行日志(按天滚动,随 config 卷持久化到宿主机)

ASSETS_DIR = SRC_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"  # 本地开发时的前端构建产物
DOCKER_STATIC_DIR = Path("/app/static")                 # Docker 镜像内的前端构建产物
