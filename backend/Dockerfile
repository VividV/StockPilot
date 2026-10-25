FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=false
RUN pip install -r requirements.txt && if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; fi
COPY . .
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data/attachments && chown -R app /app && chmod +x /app/entrypoint.sh
ENV ATTACHMENTS_DIR=/app/data/attachments
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready').status==200 else 1)"
ENTRYPOINT ["/app/entrypoint.sh"]
