"""Create and version neutral survey scripts."""

from __future__ import annotations

import json
from typing import Any

from ..db import Store
from ..llm import LLM
from ..models import QuestionScript
from ..prompts import QUESTION_DESIGNER_SYSTEM, QUESTION_DESIGNER_USER, QUESTION_REWRITE_SUFFIX


def design(
    llm: LLM,
    politician: dict[str, Any] | str,
    claim: str,
    previous: QuestionScript | None = None,
) -> QuestionScript:
    if isinstance(politician, str):
        name, party, region, context = politician, "", "", {}
    else:
        name = politician["politician_name"]
        party = politician.get("party") or ""
        region = politician.get("region") or ""
        context = politician.get("context") or {}
    system = QUESTION_DESIGNER_SYSTEM
    if previous is not None:
        system += QUESTION_REWRITE_SUFFIX.format(previous_script=json.dumps(previous.model_dump(), ensure_ascii=False))
    script, _ = llm.complete_json(
        system=system,
        user=QUESTION_DESIGNER_USER.format(
            politician=name,
            party=party,
            region=region,
            context=json.dumps(context, ensure_ascii=False),
            claim=claim,
        ),
        output_model=QuestionScript,
        max_tokens=2048,
    )
    return script


def ensure_question_set(store: Store, llm: LLM, aggregated_claim_id: str) -> dict[str, Any]:
    existing = store.latest_question_set(aggregated_claim_id)
    if existing is not None:
        return existing
    claim = store.get_claim_info(aggregated_claim_id)
    if claim is None:
        raise ValueError(f"unknown aggregated claim {aggregated_claim_id}")
    script = design(llm, claim, claim["canonical_text"])
    return store.add_question_set(aggregated_claim_id, 1, script.model_dump())


def queue_rewrite(store: Store, llm: LLM, aggregated_claim_id: str) -> dict[str, Any]:
    latest = store.latest_question_set(aggregated_claim_id)
    if latest is None:
        return ensure_question_set(store, llm, aggregated_claim_id)
    claim = store.get_claim_info(aggregated_claim_id)
    if claim is None:
        raise ValueError(f"unknown aggregated claim {aggregated_claim_id}")
    previous = QuestionScript.model_validate(latest["script"])
    script = design(llm, claim, claim["canonical_text"], previous)
    return store.add_question_set(aggregated_claim_id, int(latest["version"]) + 1, script.model_dump())
