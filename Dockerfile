FROM python:3.11-slim

WORKDIR /app

ENV DOCKER_LOG_MAX_SIZE=10m
ENV DOCKER_LOG_MAX_FILE=3
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    tzdata \
    && ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && dpkg-reconfigure -f noninteractive tzdata \
    && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/temp /app/sessions /app/logs /app/config /app/db

COPY . .

EXPOSE 9804 9805

CMD ["python", "main.py"]
