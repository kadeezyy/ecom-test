.DEFAULT_GOAL := help
SHELL := /bin/bash
PY ?= .venv/bin/python
COMPOSE ?= docker compose

export PYTHONPATH := src

.PHONY: help
help: ## Список целей
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- окружение ------------------------------------------------------------

.PHONY: venv
venv: ## Создать venv и поставить зависимости
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt

# --- docker ---------------------------------------------------------------

.PHONY: up
up: ## Поднять всё: postgres, миграции, сид, api, воркер, поставщиков A и B
	$(COMPOSE) up -d --build
	@echo "API:        http://localhost:8000/docs"
	@echo "Поставщик A: http://localhost:9001/admin/state"
	@echo "Поставщик B: http://localhost:9002/admin/state"

.PHONY: down
down: ## Остановить и удалить контейнеры и данные
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Логи api и воркера
	$(COMPOSE) logs -f api worker

# --- база -----------------------------------------------------------------

.PHONY: migrate
migrate: ## Накатить миграции локально
	$(PY) -m alembic upgrade head

.PHONY: seed
seed: ## Загрузить каталог из задания (12 SKU)
	$(PY) scripts/seed_catalog.py

.PHONY: seed-bulk
seed-bulk: ## Загрузить 50 000 синтетических SKU (этап 5)
	$(PY) scripts/seed_bulk.py --count 50000

.PHONY: explain
explain: ## Показать план запроса витрины
	$(PY) scripts/explain_storefront.py

# --- проверки -------------------------------------------------------------

.PHONY: lint
lint: ## ruff + mypy
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy

.PHONY: fmt
fmt: ## Отформатировать код
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

.PHONY: test
test: ## Все тесты (требуют TEST_DATABASE_URL для db-тестов)
	$(PY) -m pytest -v

.PHONY: test-nodb
test-nodb: ## Только тесты, не требующие PostgreSQL
	$(PY) -m pytest -v -m "not db"

.PHONY: test-acceptance
test-acceptance: ## Шесть критериев приёмки из ТЗ
	$(PY) -m pytest -v tests/acceptance

# --- сценарии из ТЗ -------------------------------------------------------

.PHONY: race
race: ## 50 параллельных вебхуков по одному заказу (критерий 1)
	$(PY) scripts/flood_webhooks.py --sku STEAM-TOPUP-500 --count 50

.PHONY: race-same-event
race-same-event: ## 50 доставок одного event_id (критерий 2)
	$(PY) scripts/flood_webhooks.py --sku STEAM-TOPUP-500 --count 50 --same-event

.PHONY: supplier-a-down
supplier-a-down: ## Поставщик A всегда отказывает -> фолбэк на B (критерий 5)
	curl -sS -X POST localhost:9001/admin/config -H 'content-type: application/json' \
		-d '{"mode":"error"}' | python3 -m json.tool

.PHONY: supplier-a-timeout
supplier-a-timeout: ## Поставщик A выдаёт код и зависает (критерий 4)
	curl -sS -X POST localhost:9001/admin/config -H 'content-type: application/json' \
		-d '{"mode":"timeout","hang_seconds":30}' | python3 -m json.tool

.PHONY: supplier-reset
supplier-reset: ## Вернуть заглушкам случайное поведение и полный пул ключей
	curl -sS -X POST localhost:9001/admin/config -H 'content-type: application/json' -d '{"mode":"random"}' >/dev/null
	curl -sS -X POST localhost:9002/admin/config -H 'content-type: application/json' -d '{"mode":"random"}' >/dev/null
	curl -sS -X POST localhost:9001/admin/reset >/dev/null
	curl -sS -X POST localhost:9002/admin/reset >/dev/null
	@echo "заглушки сброшены"

.PHONY: reconcile
reconcile: ## Отчёт сверки
	curl -sS localhost:8000/admin/reconcile \
		-H "X-Admin-Token: $${ADMIN_TOKEN:-dev-admin-token}" | python3 -m json.tool
