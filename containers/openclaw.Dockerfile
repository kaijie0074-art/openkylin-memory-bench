FROM node:24.16.0-bookworm-slim
ARG OPENCLAW_VERSION=2026.9.6
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "openclaw@${OPENCLAW_VERSION}" \
    && npm cache clean --force \
    && useradd --uid 10001 --create-home kmb
COPY scripts/agent-worker.py /opt/kmb/agent-worker.py
LABEL org.opencontainers.image.source="https://github.com/openclaw/openclaw"
LABEL org.kmb.agent="openclaw"
USER 10001:10001
WORKDIR /trial/workspace
ENTRYPOINT []
CMD ["python3", "/opt/kmb/agent-worker.py", "openclaw"]
