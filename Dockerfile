FROM golang:1.24-bookworm AS alpaca-cli-builder

ARG ALPACA_CLI_REVISION=53606273aa230a40c64b783425dcb3f4423ede30

RUN git clone https://github.com/alpacahq/cli.git /src/alpaca-cli \
    && cd /src/alpaca-cli \
    && git checkout ${ALPACA_CLI_REVISION} \
    && go build -o /out/alpaca ./cmd/alpaca

FROM python:3.12-slim

ARG GIT_SHA=unknown
ARG ALPACA_CLI_REVISION=53606273aa230a40c64b783425dcb3f4423ede30

LABEL org.opencontainers.image.revision=${GIT_SHA} \
      io.convictionos.alpaca-cli.revision=${ALPACA_CLI_REVISION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./alembic.ini
COPY --from=alpaca-cli-builder /out/alpaca /usr/local/bin/alpaca

RUN pip install --upgrade pip \
    && pip install .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

CMD ["sh", "-c", "exec python -m convictionos.commands api --host 0.0.0.0 --port ${PORT:-8000}"]
