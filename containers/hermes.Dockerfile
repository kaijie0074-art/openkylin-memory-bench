FROM python:3.14.2-slim-bookworm
ARG HERMES_COMMIT=2ffa4977baf9874c498453a09447dc399b0fd215
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/hermes \
    && git -C /opt/hermes remote add origin https://github.com/NousResearch/hermes-agent.git \
    && git -C /opt/hermes fetch --depth 1 origin "${HERMES_COMMIT}" \
    && git -C /opt/hermes checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/hermes rev-parse HEAD)" = "${HERMES_COMMIT}"
# Upstream forbids ordinary wheel/sdist builds; editable installation is supported.
RUN pip install --no-cache-dir -e /opt/hermes
RUN useradd --uid 10001 --create-home kmb
COPY scripts/agent-worker.py /opt/kmb/agent-worker.py
LABEL org.opencontainers.image.source="https://github.com/NousResearch/hermes-agent"
LABEL org.kmb.agent="hermes"
LABEL org.opencontainers.image.revision="2ffa4977baf9874c498453a09447dc399b0fd215"
USER 10001:10001
WORKDIR /trial/workspace
ENTRYPOINT []
CMD ["python3", "/opt/kmb/agent-worker.py", "hermes"]
