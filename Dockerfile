FROM python:3.12-slim
ARG CODEX_CLI_VERSION=0.144.6
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc \
    nodejs \
    npm \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-por \
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global "@openai/codex@${CODEX_CLI_VERSION}" \
    && npm cache clean --force
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts
EXPOSE 8080
CMD ["bash","scripts/start.sh"]
