FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ferry/ ./ferry/
COPY static/ ./static/
COPY seeds/ ./seeds/

EXPOSE 8080

CMD ["uvicorn", "ferry.main:app", "--host", "0.0.0.0", "--port", "8080"]
