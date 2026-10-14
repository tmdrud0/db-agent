FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.benchmark.lock.txt /tmp/dependencies/
RUN pip install --no-cache-dir -r /tmp/dependencies/requirements.benchmark.lock.txt

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /benchmark
