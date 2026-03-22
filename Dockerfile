FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ git curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

# Pasta de dados persistente
RUN mkdir -p /data/venvs

ENV DATA_DIR=/data
ENV ADMIN_TOKEN=admin-mude-isso
ENV BASE_URL=http://localhost:8000
ENV EXEC_TIMEOUT=30

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
