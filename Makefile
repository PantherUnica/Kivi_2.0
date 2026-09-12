# Kivi - convenience wrappers. Every target is the exact command from RUN.md.
.PHONY: help build up down logs migrate seed reset eval test shell psql

help:
	@echo "build    build both images"
	@echo "up       start db + api + web          -> http://localhost:5173"
	@echo "seed     import the 520-record corpus"
	@echo "eval     run the full evaluation"
	@echo "test     run the unit tests"
	@echo "reset    clear all data, keep the schema"
	@echo "down     stop everything (add VOLUMES=1 to drop the database)"

build:
	docker compose build

up:
	docker compose up -d
	@echo "web  -> http://localhost:5173"
	@echo "api  -> http://localhost:8000/docs"

down:
	docker compose down $(if $(VOLUMES),-v,)

logs:
	docker compose logs -f api

migrate:
	docker compose run --rm api alembic upgrade head

seed:
	docker compose exec api python -m app.cli import --file corpus/kivi_corpus.jsonl

reset:
	docker compose exec api python -m app.cli reset --yes

eval:
	docker compose exec api python -m evaluation.run --corpus corpus/kivi_corpus.jsonl

test:
	docker compose exec api python -m pytest tests -q

shell:
	docker compose exec api bash

psql:
	docker compose exec db psql -U kivi -d kivi
