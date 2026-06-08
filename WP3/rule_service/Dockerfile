FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && apt-get purge -y --auto-remove build-essential gcc \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

COPY src /app/src

EXPOSE 8010

CMD ["uvicorn", "src.rule_api:app", "--host", "0.0.0.0", "--port", "8010"]
