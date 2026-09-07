FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PV_WIKI_WORKER_HOST=0.0.0.0 \
    PV_WIKI_WORKER_PORT=8080 \
    PV_WIKI_DATASHEET_CACHE_DIR=/var/lib/pv-wiki/datasheets

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY skills/wikijs-sync-products/scripts ./skills/wikijs-sync-products/scripts

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir . \
    && addgroup --system --gid 10001 pvwiki \
    && adduser --system --uid 10001 --ingroup pvwiki --home /nonexistent pvwiki \
    && mkdir -p /var/lib/pv-wiki/datasheets \
    && chown -R pvwiki:pvwiki /var/lib/pv-wiki

USER pvwiki

EXPOSE 8080

CMD ["pv-wiki", "serve"]
