PY  ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: setup check run demo test reset clean lint

setup:
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -r requirement.txt -r requirements-dev.txt
	$(PIP) install -e .

lint:
	$(PY) -m ruff check src tests

check:
	$(PY) -m ltm check

run:
	$(PY) -m ltm run

demo:
	$(PY) -m ltm demo

test:
	$(PY) -m pytest

reset:
	$(PY) -m ltm reset

clean:
	rm -rf data/evidence data/state.json data/events.jsonl data/demo
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
