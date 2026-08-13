FROM python:3.12-slim

ENV IN_CONTAINER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git make \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 palmanager \
    && useradd --create-home --gid palmanager --uid 10001 palmanager \
    && install --directory --owner=palmanager --group=palmanager /var/lib/palworld-manager \
    && git config --system --add safe.directory /workspace

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir ".[dev]"

COPY --chown=palmanager:palmanager . .

USER palmanager

EXPOSE 8080

CMD ["python", "-m", "app.web"]
