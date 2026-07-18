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
RUN addgroup --system --gid 1000 hermes && \
    adduser --system --uid 1000 hermes --ingroup hermes && \
    chown -R hermes:hermes /app
USER hermes

EXPOSE 2800

ENV HERMES_HOME=/data/hermes
ENV PORT=2800

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python3 -c "import urllib.request,os; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT','2800') + '/health')" || exit 1

ENTRYPOINT ["python3", "-c", "import os; from server import app; import uvicorn; uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT','2800')))"]
