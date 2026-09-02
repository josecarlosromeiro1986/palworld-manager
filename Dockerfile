FROM node:24-slim AS node-runtime

FROM python:3.12-slim AS runtime-build

ENV IN_CONTAINER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git make \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && groupadd --gid 10001 palmanager \
    && useradd --create-home --gid palmanager --uid 10001 palmanager \
    && install --directory --owner=palmanager --group=palmanager /var/lib/palworld-manager \
    && git config --system --add safe.directory /workspace

COPY pyproject.toml README.md package.json package-lock.json ./
COPY app ./app
COPY scripts ./scripts
RUN pip install --no-cache-dir ".[dev]"
RUN npm ci && npm run build

FROM runtime-build AS e2e

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN npx playwright install --with-deps chromium \
    && chown --recursive palmanager:palmanager /ms-playwright

COPY --chown=palmanager:palmanager . .

USER palmanager
CMD ["npm", "run", "e2e"]

FROM runtime-build AS app

COPY --chown=palmanager:palmanager . .

USER palmanager

EXPOSE 8080

CMD ["python", "-m", "app.web"]
