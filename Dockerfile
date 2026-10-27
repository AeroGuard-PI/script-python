FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV HOST_ROOT=/host
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY script-coleta-python.py configurar.py ./
CMD ["python", "script-coleta-python.py", "--coletar"]
