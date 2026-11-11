FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN pip install --no-deps . \
    && groupadd --gid 10001 wabot \
    && useradd --uid 10001 --gid wabot --no-create-home --shell /usr/sbin/nologin wabot \
    && mkdir -p /data/backups \
    && chown -R wabot:wabot /data

USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "wabot"]
CMD ["run"]
