FROM python:3.12-slim

# Логи идут в stdout без буферизации (важно для Railway logs и слоя ошибок),
# .pyc не пишем — образ чище.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Сначала зависимости — слой кешируется, пока requirements.txt не меняется.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем код бота.
COPY . .

# Каталог для SQLite-базы; монтируется как Persistent Volume в Railway.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["python", "main.py"]
