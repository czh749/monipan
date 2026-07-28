# ===========================================================================
# MoniPan 后端 Docker 镜像
# ===========================================================================
# 基于 Python 3.13 Slim 镜像构建（精简版，体积小）
# 构建: docker build -t monipan-backend .
# 运行: docker run -p 8000:8000 monipan-backend

# ---- 基础镜像 ----
# python:3.13-slim: 精简版 Python 3.13，仅包含运行 Python 所需的最小依赖
FROM python:3.13-slim

# ---- 环境变量 ----
# PYTHONDONTWRITEBYTECODE=1: 禁止 Python 生成 .pyc 字节码文件（减少镜像体积）
# PYTHONUNBUFFERED=1: 禁用输出缓冲（日志实时输出到 Docker 控制台）
# PIP_NO_CACHE_DIR=1: pip 不缓存下载的包（减少镜像体积）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ---- 工作目录 ----
WORKDIR /app

# ---- 安装依赖 ----
# 先复制 requirements.txt 并安装依赖（利用 Docker 缓存层）
# 如果 requirements.txt 没变，此层会被缓存，加速构建
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    # 创建非 root 用户运行应用（安全最佳实践）
    && addgroup --system monipan \
    && adduser --system --ingroup monipan monipan

# ---- 复制应用代码 ----
# --chown 将文件所有权赋予 monipan 用户
COPY --chown=monipan:monipan app ./app

# ---- 数据目录 ----
# 创建 SQLite 数据目录并设置权限
RUN mkdir -p /app/data && chown -R monipan:monipan /app

# ---- 切换用户 ----
# 以非 root 用户运行应用（安全最佳实践，遵循最小权限原则）
USER monipan

# ---- 暴露端口 ----
# 8000: FastAPI/Uvicorn 默认端口
EXPOSE 8000

# ---- 启动命令 ----
# 以 Uvicorn ASGI 服务器运行 FastAPI 应用
# --host 0.0.0.0: 监听所有网络接口（容器内必须用 0.0.0.0，不能用 127.0.0.1）
# --port 8000: 监听 8000 端口
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
