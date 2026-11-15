"""Generate fictional personas for population strata."""

from __future__ import annotations

from ..config import Settings
from ..llm import LLM
from ..models import Persona, PersonaBatch
from ..prompts import PERSONA_SYSTEM, PERSONA_USER


def generate(
    llm: LLM,
    stratum: dict,
    count: int,
    used_names: set[str] | list[str],
    settings: Settings,
) -> list[Persona]:
    if count <= 0:
        return []
    existing_names = set(used_names)
    existing = {name.casefold() for name in existing_names}
    generated: list[Persona] = []
    for start in range(0, count, settings.personas_per_call):
        chunk_count = min(settings.personas_per_call, count - start)
        result, _ = llm.complete_json(
            system=PERSONA_SYSTEM,
            user=PERSONA_USER.format(
                region=stratum["region"],
                age_band=stratum["age_band"],
                gender=stratum["gender"],
                urban_rural=stratum.get("urban_rural"),
                count=chunk_count,
                used_names=", ".join(sorted(existing_names)),
            ),
            output_model=PersonaBatch,
            max_tokens=4096,
        )
        for persona in result.personas:
            normalized_name = persona.name.casefold()
            if normalized_name in existing or len(generated) >= count:
                continue
            existing.add(normalized_name)
            existing_names.add(persona.name)
            generated.append(persona)
    return generated
