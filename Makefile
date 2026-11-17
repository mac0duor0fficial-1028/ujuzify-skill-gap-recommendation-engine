.PHONY: install ui test run pipeline docker lint
install:
	pip install -r requirements-dev.txt
ui:
	cd app && npm ci && npm run build
test:
	python -m pytest -q --cov=src --cov=api
run: ui
	uvicorn api.main:app --reload --port 8000
pipeline:
	python -m src.pipeline --load-db
docker:
	docker compose up --build
