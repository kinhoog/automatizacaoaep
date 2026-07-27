FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AEP_HOST=0.0.0.0 \
    AEP_RUNTIME_DIR=/tmp/aep-jobs \
    AEP_JOB_TTL_SECONDS=900 \
    AEP_TEMPLATE_PATH=/tmp/aep-runtime/template/aep_template.docx \
    AEP_TEMPLATE_MANIFEST_PATH=/tmp/aep-runtime/template/aep_template.manifest.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-crosextra-caladea \
        fonts-crosextra-carlito \
        fonts-dejavu-core \
        fonts-liberation \
        libreoffice-writer \
        poppler-utils \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deliberately copy only public application sources. Private templates, uploads,
# samples and generated documents never become part of the image build context.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN groupadd --gid 1000 render-secrets \
    && groupadd --system --gid 10001 aep \
    && useradd \
        --system \
        --uid 10001 \
        --gid aep \
        --groups render-secrets \
        --create-home \
        --home-dir /home/aep \
        --shell /usr/sbin/nologin \
        aep \
    && mkdir -p \
        /tmp/aep-jobs \
        /tmp/aep-runtime/template \
    && chown -R aep:aep \
        /home/aep \
        /tmp/aep-jobs \
        /tmp/aep-runtime

USER aep

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import json, os, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '10000') + '/api/health', timeout=4); payload = json.load(response); raise SystemExit(0 if response.status == 200 and payload.get('pipeline_ready') is True else 1)"

CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-10000}\""]
