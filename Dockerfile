FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000 8501 8502
CMD ["uvicorn", "core_service:app", "--host", "0.0.0.0", "--port", "8000"]
