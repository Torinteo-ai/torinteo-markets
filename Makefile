.PHONY: setup build run test web-test

PYTHON ?= python3

setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements-web.txt
	.venv/bin/python -m pip install --no-deps -e .
	npm --prefix web ci
	npm --prefix web run build

build:
	npm --prefix web run build

run:
	.venv/bin/python -m torinteo_paper.web --data-dir runtime/web --static-dir web/dist

test:
	.venv/bin/python -m unittest discover -s tests -v
	npm --prefix web run build

web-test:
	npm --prefix web run test:e2e
