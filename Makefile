PY := $(CURDIR)/.venv/bin/python
NEO4J_HOME := $(CURDIR)/.local/neo4j-community-5.26.0
RUN := cd backend && $(PY) -m polgraph

.PHONY: classify install neo4j-start neo4j-stop seed mexico mexico-production refresh-mexico report path serve test

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

neo4j-start:
	$(NEO4J_HOME)/bin/neo4j start

neo4j-stop:
	$(NEO4J_HOME)/bin/neo4j stop

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

test:
	cd backend && $(PY) -m pytest -q

pakistan:
	$(RUN) build --country PK

classify:
	$(RUN) classify-world
