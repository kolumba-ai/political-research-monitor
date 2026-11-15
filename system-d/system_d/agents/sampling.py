"""Select unused simulated respondents, generating only each allocation's shortfall."""

from __future__ import annotations

from ..config import Settings
from ..db import Store
from ..llm import LLM
from ..models import Respondent
from .persona import generate


def select_respondents(
    store: Store,
    llm: LLM,
    sim_run: str,
    aggregated_claim_id: str,
    allocation: dict[str, int],
    settings: Settings,
) -> list[Respondent]:
    strata_by_id = {str(row["id"]): row for row in store.list_strata()}
    selected: list[Respondent] = []
    for stratum_id, count in allocation.items():
        if count <= 0:
            continue
        available = store.available_simulated_respondents(stratum_id, sim_run, aggregated_claim_id)
        reused = available[:count]
        selected.extend(reused)
        shortfall = count - len(reused)
        if shortfall <= 0:
            continue
        stratum = strata_by_id.get(stratum_id)
        if stratum is None:
            raise ValueError(f"unknown population stratum {stratum_id}")
        names = {str(persona.persona.get("name", "")).casefold() for persona in reused}
        personas = generate(llm, stratum, shortfall, names, settings)
        for persona in personas:
            persona_data = persona.model_dump()
            persona_data["sim_run"] = sim_run
            selected.append(store.add_respondent(stratum_id, persona_data))
    return selected
