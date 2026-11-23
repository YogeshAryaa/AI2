# One image: builds the UI, then serves UI + API from the backend on port 8000.
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models
WORKDIR /app/backend
# Keep the CPU Torch and torchvision wheels on the same release to avoid missing operators.
RUN pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY --from=ui /ui/dist /app/frontend/dist
# Download the embedding model at build time so the first start is fast
ARG EMBEDDING_MODEL=intfloat/multilingual-e5-small
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('${EMBEDDING_MODEL}')"
# OCR models for Studio (Scan & read) are downloaded at build time too
ENV OCR_MODEL_DIR=/models/easyocr
RUN python -c "import easyocr; easyocr.Reader(['en','hi'], gpu=False, model_storage_directory='/models/easyocr')"
RUN useradd -m app && chown -R app /app /models
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
# 2 workers share the work safely (one of them runs sync in the background). Each loads the model (~0.5 GB RAM).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-2} --proxy-headers --forwarded-allow-ips='*'"]
