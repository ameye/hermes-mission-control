FROM python:3.12-slim AS builder

WORKDIR /app
COPY server.py .
COPY static/ ./static/

# Install only the dependencies we need for runtime
RUN pip install --no-cache-dir fastapi==0.133.1 uvicorn==0.41.0 sse-starlette==3.4.5

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY server.py .
COPY static/ ./static/

# Create a non-root user
RUN addgroup --system --gid 1001 hermes && \
    adduser --system --uid 1001 hermes --ingroup hermes && \
    chown -R hermes:hermes /app
USER hermes

EXPOSE 2800

ENV HERMES_HOME=/data/hermes

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:2800/api/fleet')" || exit 1

ENTRYPOINT ["python3", "server.py"]
