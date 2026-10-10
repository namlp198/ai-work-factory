PY ?= .venv/bin/python
AWF ?= .venv/bin/awf

.PHONY: install test unit integration e2e db api orchestrator worker demo demo-headless up down

install:
	python -m venv .venv && $(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest -q -p no:logging

unit:
	$(PY) -m pytest tests/unit -q -p no:logging
integration:
	$(PY) -m pytest tests/integration -q -p no:logging
e2e:
	$(PY) -m pytest tests/e2e -q -p no:logging

db:
	$(AWF) db upgrade
api:
	$(AWF) api --embedded-orchestrator
orchestrator:
	$(AWF) orchestrator
worker:
	$(AWF) worker --name local-worker

demo:
	$(AWF) demo
demo-headless:
	$(AWF) demo --auto-approve

up:
	docker compose up -d --build
down:
	docker compose down
