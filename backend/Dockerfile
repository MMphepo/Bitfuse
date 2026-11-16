FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/Bitfuse

RUN groupadd -r bitfuse && useradd -r -g bitfuse -d /app -s /sbin/nologin bitfuse

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY Bitfuse /app/Bitfuse
COPY entrypoint.sh /app/entrypoint.sh

RUN mkdir -p /app/Bitfuse/staticfiles /app/Bitfuse/media && \
    chmod +x /app/entrypoint.sh && \
    chown -R bitfuse:bitfuse /app

WORKDIR /app/Bitfuse

EXPOSE 8000

USER bitfuse

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
