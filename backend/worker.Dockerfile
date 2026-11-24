FROM python:3.12-slim
ENV OMP_NUM_THREADS=2 HF_HOME=/models HF_HUB_DISABLE_TELEMETRY=1
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
ARG WORKER_PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_DEFAULT_TIMEOUT=120 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --index-url "$WORKER_PIP_INDEX_URL" "pip==25.3"
# Download only the two CPU wheels here. Resolve their dependencies with Docling.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip download --index-url https://download.pytorch.org/whl/cpu \
        --only-binary=:all: --no-deps --dest /tmp/cpu-wheels \
        "torch==2.10.0+cpu" "torchvision==0.25.0+cpu" && \
    python -m pip install --index-url "$WORKER_PIP_INDEX_URL" \
        /tmp/cpu-wheels/*.whl -r requirements.txt "docling==2.80.0" && \
    python -m pip check && \
    python -c 'import torch, torchvision; from docling.document_converter import DocumentConverter; assert torch.version.cuda is None' && \
    rm -rf /tmp/cpu-wheels
RUN apt-get update && apt-get install -y --no-install-recommends antiword poppler-utils tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY *.py ./
CMD ["celery","-A","ingestion.jobs","worker","--pool=solo","--loglevel=WARNING"]
