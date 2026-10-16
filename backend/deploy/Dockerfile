# Agent Platform 后端镜像（对齐 agent.md §11.1 部署拓扑）
# 构建：docker build -f deploy/Dockerfile -t agent-platform:latest .
# 运行：docker run -p 8000:8000 --env-file .env agent-platform:latest
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 系统依赖：构建 wheel、Postgres 客户端库、curl 健康检查
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# 依赖层（利用 Docker 层缓存）
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# 应用代码
COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/monitor/stats || exit 1

CMD ["python", "-m", "uvicorn", "platform_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
