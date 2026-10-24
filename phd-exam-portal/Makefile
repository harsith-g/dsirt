.PHONY: help up down logs build seed reseed migrate revision shell psql redis test inspect candidates fmt clean

COMPOSE := docker compose

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:          ## Build and start the whole stack (migrates + seeds automatically)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  Candidate login : http://localhost:$${HTTP_PORT:-8080}/login"
	@echo "  Admin login     : http://localhost:$${HTTP_PORT:-8080}/admin/login"
	@echo "                    admin@examportal.edu / Admin@123"
	@echo ""
	@echo "  Follow the seed report with:  make logs"

down:        ## Stop the stack (data is kept)
	$(COMPOSE) down

clean:       ## Stop and DELETE all data, including the database
	$(COMPOSE) down -v

build:       ## Rebuild the backend image
	$(COMPOSE) build backend

logs:        ## Tail backend logs
	$(COMPOSE) logs -f backend

migrate:     ## Apply database migrations
	$(COMPOSE) exec backend alembic upgrade head

revision:    ## Autogenerate a migration: make revision m="add x"
	$(COMPOSE) exec backend alembic revision --autogenerate -m "$(m)"

seed:        ## Re-run seeding (idempotent)
	$(COMPOSE) exec backend python seed_db.py

reseed:      ## Re-import question banks only
	$(COMPOSE) exec backend python seed_db.py --questions-only

candidates:  ## Rebuild seed_data/candidates.* from an Application Details workbook
	$(COMPOSE) exec backend python tools/build_candidates.py seed_data/Application_Details.xlsx seed_data

inspect:     ## Dry-run the paper parser and print a per-file quality report
	$(COMPOSE) exec backend python tools/inspect_papers.py seed_data

test:        ## Run the test suite
	$(COMPOSE) exec backend python -m pytest tests -q

shell:       ## Shell inside the backend container
	$(COMPOSE) exec backend bash

psql:        ## Open psql
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-exam} -d $${POSTGRES_DB:-examportal}

redis:       ## Open redis-cli
	$(COMPOSE) exec redis redis-cli
