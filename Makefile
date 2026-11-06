.DEFAULT_GOAL := help

ifeq ($(OS),Windows_NT)
SHELL := cmd.exe
.SHELLFLAGS := /d /s /c
PYTHON ?= python
VENV_PYTHON := .venv/Scripts/python.exe
else
SHELL := /bin/sh
PYTHON ?= python3
VENV_PYTHON := .venv/bin/python
endif

RUN_PYTHON = "$(VENV_PYTHON)" -X utf8 -B
COMPOSE = docker compose
DATABASE ?= inventory_console
SCENARIO ?= race

.PHONY: help prepare deps up setup start console check test demo status down

help:
	@echo make prepare  - Install dependencies, build/start services, prepare demo database
	@echo make start    - Start services, prepare demo database, open console
	@echo make console  - Open console for an already running stack
	@echo make check    - Run all eight defense scenarios and check database health
	@echo make test     - Run the console unit and integration tests
	@echo make demo SCENARIO=race - Run one defense scenario with SQL and pauses
	@echo make status   - Show service, replication and inventory health
	@echo make up       - Start services without opening the console
	@echo make down     - Stop services and preserve database volumes
	@echo make console DATABASE=inventory_case2 - Open the main database

$(VENV_PYTHON):
	$(PYTHON) -m venv .venv

# Refresh dependencies only when requirements or the interpreter change.
.venv/.requirements-stamp: requirements.txt $(VENV_PYTHON)
	$(RUN_PYTHON) -m pip install -r requirements.txt --disable-pip-version-check
	$(RUN_PYTHON) -c "from pathlib import Path; Path('.venv/.requirements-stamp').touch()"

deps: .venv/.requirements-stamp

prepare: deps
	$(COMPOSE) up -d --build --wait
	$(RUN_PYTHON) inventory_console.py --setup --status
	@echo Ready. Run make console. Choose D for scenarios and S to show SQL.

up:
	$(COMPOSE) up -d --wait

# Dependencies and services must be ready before any setup or scenario runs,
# including when make is invoked with -j.
setup: deps up
	$(RUN_PYTHON) inventory_console.py --setup

start: setup
	$(RUN_PYTHON) inventory_console.py --database "$(DATABASE)"

console: deps
	$(RUN_PYTHON) inventory_console.py --database "$(DATABASE)"

check: setup
	$(RUN_PYTHON) inventory_console.py --demo all --no-pause --status
	@echo All eight defense scenarios passed. The console is ready.

test: setup
	$(RUN_PYTHON) tests/verify_console.py --integration

demo: setup
	$(RUN_PYTHON) inventory_console.py --demo "$(SCENARIO)" --show-sql

status: deps
	$(COMPOSE) ps -a
	$(RUN_PYTHON) inventory_console.py --status

down:
	$(COMPOSE) down
