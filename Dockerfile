# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    BOT_CONFIG=/app/config/roles.yaml BOT_DB_PATH=/data/bot.db TZ=Asia/Shanghai
COPY --from=build /install /usr/local
RUN useradd --system --uid 10001 --home /app bot && mkdir -p /app/config /data && chown bot /data
WORKDIR /app
USER 10001
VOLUME ["/data"]
# No inbound ports: events arrive over an outbound WebSocket long connection.
ENTRYPOINT ["python", "-m", "bitable_bot"]
