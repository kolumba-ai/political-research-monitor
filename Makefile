PY := $(CURDIR)/.venv/bin/python
NEO4J_HOME := $(CURDIR)/.local/neo4j-community-5.26.0
NODE_BIN ?= $(CURDIR)/.local/node-v24.22.2-linux-x64/bin
RUN := cd backend && "$(PY)" -m polgraph
EXPLORER = cd frontend-3d && PATH="$(NODE_BIN):$$PATH" npm

.PHONY: sc-migrate sc-list sc-sync sc-dry-run sc-test export export-bundles import-kb classify install neo4j-start neo4j-stop seed mexico pakistan mexico-production refresh-mexico report path serve test explorer-build explorer-dev explorer-check

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

neo4j-start:
	"$(NEO4J_HOME)/bin/neo4j" start

neo4j-stop:
	"$(NEO4J_HOME)/bin/neo4j" stop

seed:
	$(RUN) seed

# Build / refresh the Mexico graph. Research-only: Mexico has not passed the eligibility gates
# (they require human review), so production mode would stop after the checks.
mexico:
	$(RUN) build --country MX

mexico-production:
	$(RUN) build --country MX --production

refresh-mexico:
	$(RUN) build --country MX --no-cache

report:
	$(RUN) report --country MX

path:
	$(RUN) path "Sheinbaum"

serve:
	$(RUN) serve

explorer-build:
	$(EXPLORER) run build

explorer-dev:
	$(EXPLORER) run dev -- --host 127.0.0.1

explorer-check:
	$(EXPLORER) run lint
	$(EXPLORER) run build

test:
	cd backend && "$(PY)" -m pytest -q

pakistan:
	$(RUN) build --country PK

classify:
	$(RUN) classify-world

export:
	$(RUN) export --out ../knowledge_base

export-bundles:
	$(RUN) export-bundles --country MX PK --kb ../knowledge_base --out ../knowledge_base/bundles

import-kb:
	$(RUN) import --from ../knowledge_base

# --- System B (social claims, Phase 0/1) ---------------------------------------------------------------
SC := cd backend && "$(PY)" -m socialclaims

sc-migrate:
	$(SC) migrate

sc-list:
	$(SC) list --country MX

sc-sync:
	$(SC) sync-refs --country MX

# Builds the Oriane requests for the three pilots without calling Oriane.
sc-dry-run:
	$(SC) capture --dry-run "Claudia Sheinbaum" "Clara Brugada" "Pablo Lemus"

sc-test:
	cd backend && "$(PY)" -m pytest -q tests/socialclaims
