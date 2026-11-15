# System C — Evidence Knowledge Graph (design only)

Design: [`System_C_Design_Doc_for_Devin.md`](System_C_Design_Doc_for_Devin.md). Read its implementation brief first.

System C finds documents online (news, official statistics, government, legislative and court records, think tanks,
universities, fact-checkers, polls) through Exa and registered feeds, builds a timestamped evidence knowledge graph
in Postgres with a projection in past.dev, and maps System B's propositions against it.

Status: not built. Code will live in `backend/evidencegraph/` with `ev_`-prefixed tables in System B's database.
