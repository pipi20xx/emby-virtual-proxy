# --- STAGE 1: Build Frontend (这一阶段不变) ---
# Vite 8 要求 Node >= 20.19,使用 LTS 22
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json .
# 使用 npm 淘宝镜像/阿里云镜像
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY frontend/ .
RUN npm run build
RUN if [ ! -d "dist" ] || [ -z "$(ls -A dist)" ]; then echo "Frontend build failed: dist directory is empty or does not exist." && exit 1; fi


# --- STAGE 2: Build Final Image (这一阶段结构调整) ---
FROM python:3.11-slim

# 将整个应用放在 /app 目录下
WORKDIR /app

# 关键改动：将 Python 源码复制到 /app/src 子目录
COPY src/ /app/src/

# 关键改动：将 PYTHONPATH 设置为源码目录
ENV PYTHONPATH=/app/src

# 将依赖文件复制到工作目录根部并安装
COPY src/requirements.txt .
# 使用 debian 清华源加速 apt-get，并使用阿里云 pip 镜像加速依赖安装
# tzdata:支持 TZ 环境变量指定 IANA 时区(影响日志时间戳与按天日志切分)
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y supervisor tzdata && \
    pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# 关键改动：将编译好的前端文件复制到 /app/static，与 src 目录同级
COPY --from=frontend-builder /app/frontend/dist /app/static

# 暴露端口
EXPOSE 8001
EXPOSE 8999

# 新增：复制 supervisord 配置文件
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# 修改：使用 supervisord 启动服务
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
