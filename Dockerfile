ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates tini && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -r -u 10001 -m app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY hc_bulk ./hc_bulk

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HC_API_URL="https://healthchecks.io/api/"

USER app

ENTRYPOINT ["/usr/bin/tini","--","hc-bulk"]
CMD ["--help"]
