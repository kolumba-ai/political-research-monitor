# Instructions for Claude Code

Before researching, extending or restructuring any country in the knowledge graph, read and follow
[AGENTS.md](AGENTS.md). In short:

1. Read the country's constitution (official consolidated text) first and derive its structure.
2. Write a Structure Spec with an article citation for every tier, body and office.
3. Get human approval of the structure **before** building or restructuring the graph.
4. Only then fetch officeholders.

Local government is nested under the division that constitutes it (e.g. Mexico's municipalities belong to
their state); never create a country-wide municipal tier for a federal or devolved country.
