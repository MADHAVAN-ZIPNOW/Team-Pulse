# One container: React UI (built) + FastAPI backend + Claude Code CLI.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM node:22-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code \
 && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
# Azure CLI (Claude runs `az` as the signed-in person) + its Azure DevOps extension, installed once for everyone.
ENV AZURE_EXTENSION_DIR=/opt/azext AZURE_CORE_COLLECT_TELEMETRY=false
RUN pip install azure-cli  && az extension add --name azure-devops --yes
# Free speech-to-text (Whisper): the model is NOT in the image (it is 1.5 GB). The app downloads it once, in the background, right after
# it starts, into the data volume, so it survives restarts and the image builds in minutes.
ENV HF_HOME=/home/app/data/hf
COPY backend/app ./app
COPY --from=web /web/dist /app/static
ENV STATIC_DIR=/app/static HOME=/home/app
# The app runs as the non-root user "app" (Claude Code keeps its config under $HOME). The container STARTS as root only to hand the data
# folder to that user (a Render disk is mounted owned by root), then drops to "app" for good.
RUN useradd -m app && mkdir -p /home/app/data && chown -R app:app /app /home/app
EXPOSE 8000
CMD ["sh", "-c", "chown -R app:app /home/app/data 2>/dev/null; exec su -m app -s /bin/sh -c \"uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'\""]
