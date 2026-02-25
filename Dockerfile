FROM python:3.11-slim

WORKDIR /app

# Keep image minimal but deterministic.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Socket Mode requires websocket-client.
RUN pip install --no-cache-dir websocket-client

# App sources
COPY app_daemon.py arxiv_rss_assistant.py slack_cmd_toolkit.py slack_healthcheck.py paperrss_version.py /app/
COPY VERSION /app/VERSION
COPY config.example.json README.md /app/
COPY config.json /app/config.json

# Runtime directories for generated outputs/state.
RUN mkdir -p /app/storage/data /app/storage/reports /app/logs

EXPOSE 8080

CMD ["python", "app_daemon.py", "--config", "config.json", "--log-level", "INFO", "--log-file", "/app/logs/app.log"]
