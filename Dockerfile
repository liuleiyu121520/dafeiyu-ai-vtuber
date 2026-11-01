# 大肥鱼 AI 虚拟主播 · macOS (Apple Silicon / linux-arm64) 镜像
#
# 与仓库根目录原 Dockerfile 的区别：
#   1. 基础镜像锁 python:3.11-slim-bookworm（3.10 在 arm64 上部分轮子覆盖不全）
#   2. 依赖用 requirements/docker-arm64.txt（剔除 Windows 专有 / 无 arm64 轮子的包）
#   3. 非 root 运行（appuser）
#   4. 不 COPY 密钥、日志、缓存与运行态目录（见 .dockerignore）
#   5. 编译型依赖（PyAudio）单独一层装构建工具，装完即清，不留在最终镜像里
#
# 平台由构建命令决定（compose 的 platforms: [linux/arm64] 或 docker build --platform），
# 这里不写死，避免 buildx 的 FromPlatformFlagConstDisallowed 警告。
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai \
    DEBIAN_FRONTEND=noninteractive

# ---- 运行期系统库 ----
# ffmpeg:          pydub / edge-tts / 点歌转码
# libportaudio2:   PyAudio 运行期 .so（容器内不录麦克风，但要能 import）
# libsndfile1:     pygame / soundfile
# libgl1 libglib:  opencv-headless 兜底
# fonts-noto-cjk:  无头渲染中文不乱码
# tini:            PID 1 信号转发，保证 docker stop 能优雅退出
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libportaudio2 \
        libsndfile1 \
        libgl1 \
        libglib2.0-0 \
        fonts-noto-cjk \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- 依赖层（单独一层，代码改动不会让它失效）----
# PyAudio 在 PyPI 上只有 Windows 轮子和源码包，Linux 必须现场编译，
# 所以这里临时装 portaudio19-dev + gcc，装完立刻 purge，最终镜像不带编译器。
COPY requirements/docker-arm64.txt /app/requirements/docker-arm64.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends portaudio19-dev gcc g++ python3-dev \
    && python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements/docker-arm64.txt \
    && apt-get purge -y --auto-remove portaudio19-dev gcc g++ python3-dev \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# ---- 代码层 ----
COPY . /app

# 运行期目录：容器内以 appuser 写入，宿主机用 bind mount 覆盖
RUN mkdir -p /app/log /app/out /app/out/songroom /app/data /app/song /app/bgm_cache \
    && useradd --create-home --uid 1000 --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/healthcheck.py /usr/local/bin/healthcheck.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.py

USER appuser

# 8081 WebUI / 8082 主程序 API / 12345 Live2D / 5500 字幕+音频桥 / 8888 弹幕 Hub
EXPOSE 8081 8082 12345 5500 8888

HEALTHCHECK --interval=20s --timeout=8s --start-period=90s --retries=5 \
    CMD python /usr/local/bin/healthcheck.py --quiet

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["webui"]
