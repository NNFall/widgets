# Dockerfile

# Этап 1: Установка зависимостей
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Этап 2: Сборка финального образа
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

COPY . .

# Указываем Python, что корень нашего проекта /app является
# местом, откуда можно импортировать модули (app, core, wrappers).
ENV PYTHONPATH /app

# Команда для запуска ТОЛЬКО веб-сервера.
# Мы запускаем файл app/server.py
CMD ["python", "app/server.py"]
