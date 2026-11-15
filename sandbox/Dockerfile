# The image user code runs in (VOX_SANDBOX=docker). Build: docker build -t voxagent-sandbox:latest sandbox
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip nodejs npm golang-go default-jdk-headless maven gcc g++ make \
      git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 runner
USER runner
WORKDIR /work
