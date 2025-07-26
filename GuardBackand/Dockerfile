FROM python:3.12-slim-bookworm

WORKDIR /app

# Установка зависимостей для psycopg2
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# копирование данных и установка зависимостей
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запуск
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]