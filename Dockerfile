FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app

# /app/data holds the SQLite DB and uploaded PDFs; in Kubernetes it is a persistent volume
RUN mkdir -p /app/data/uploads

ENV PYTHONUNBUFFERED=1 \
    PORT=8121 \
    DATA_DIR=/app/data

EXPOSE 8121

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
