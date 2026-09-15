# syntax=docker/dockerfile:1
# Сервис распознавания WineScan (FastAPI + модели). Данные кейса и артефакты монтируются томами,
# веса моделей скачиваются при первом запуске в том hf-cache (см. docker-compose.yml).
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf \
    WINESCAN_DATA_DIR=/data \
    WINESCAN_ARTIFACTS_DIR=/artifacts

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip libglib2.0-0 curl make unrar \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# torch отдельным слоем: самый тяжёлый и редко меняется
RUN pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu126

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[ml]"
COPY configs ./configs
COPY scripts ./scripts
COPY Makefile ./

EXPOSE 8080
# старт с загрузкой и прогревом моделей занимает ~40 с на RTX 3090, первая загрузка весов — дольше
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/health || exit 1
CMD ["uvicorn", "winescan.service.app:app", "--host", "0.0.0.0", "--port", "8080"]
