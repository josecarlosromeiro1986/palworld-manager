COMPOSE ?= docker compose

ifeq ($(IN_CONTAINER),1)
RUN :=
else
RUN := $(COMPOSE) run --build --rm app
endif

.PHONY: dev down test lint format format-check typecheck precommit check e2e

dev:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down --remove-orphans

test:
	$(RUN) env APP_ENVIRONMENT=test pytest

lint:
	$(RUN) ruff check .

format:
	$(RUN) ruff format .

format-check:
	$(RUN) ruff format --check .

typecheck:
	$(RUN) mypy

precommit:
	$(RUN) pre-commit run --all-files

check: lint format-check typecheck test

e2e:
	@echo "Os testes E2E serão adicionados na Etapa 28."
