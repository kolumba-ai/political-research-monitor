"""Run the respondent script and score the resulting transcript."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from ..channels.base import Channel
from ..channels.simulator import SimulatorChannel
from ..config import Settings
from ..db import Store, get_pool
from ..llm import LLM, AnthropicLLM, Usage, UsageMeter
from ..models import PollResult, PollScore, QuestionScript, Respondent, TranscriptMessage
from ..prompts import POLL_SCORER_SYSTEM, POLL_SCORER_USER

REFUSAL_PATTERNS = (
    "no quiero",
    "no me interesa",
    "no tengo tiempo",
    "no gracias",
    "no deseo",
    "dejen de",
    "basta",
    "no participo",
)
SENTIMENT_SCALE = {1: -1.0, 2: -0.5, 3: 0.0, 4: 0.5, 5: 1.0}


def _normalized(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _is_refusal(text: str) -> bool:
    normalized = _normalized(text)
    return any(pattern in normalized for pattern in REFUSAL_PATTERNS)


def run_poll(
    channel: Channel,
    respondent: Respondent,
    script: QuestionScript,
    politician: str,
    claim: str,
    llm: LLM,
) -> tuple[PollResult, Usage]:
    transcript: list[TranscriptMessage] = []
    conversation_id = channel.start(respondent, script.opening)
    transcript.append(TranscriptMessage(role="agent", text=script.opening))
    refused = False
    try:
        prompts = [script.belief_q, script.sentiment_q, f"{script.open_q}\n\n{script.closing}"]
        for message in prompts:
            transcript.append(TranscriptMessage(role="agent", text=message))
            reply = channel.send(conversation_id, message)
            if reply is None:
                break
            transcript.append(TranscriptMessage(role="respondent", text=reply))
            if _is_refusal(reply):
                refused = True
                break
    finally:
        channel.close(conversation_id)

    score, score_usage = llm.complete_json(
        system=POLL_SCORER_SYSTEM,
        user=POLL_SCORER_USER.format(
            politician=politician,
            claim=claim,
            transcript=json.dumps([item.model_dump() for item in transcript], ensure_ascii=False),
        ),
        output_model=PollScore,
        max_tokens=1024,
    )
    believes_claim = {"yes": True, "no": False, "dont_know": None}.get(score.believes_claim)
    sentiment = SENTIMENT_SCALE.get(score.sentiment_choice)
    complete = (
        score.completed
        and not score.refused
        and not refused
        and score.believes_claim is not None
        and score.sentiment_choice is not None
    )
    usage_meter = UsageMeter()
    usage_for = getattr(channel, "usage_for", None)
    if usage_for is not None:
        usage_meter.add(usage_for(conversation_id))
    usage_meter.add(score_usage)
    return (
        PollResult(
            believes_claim=believes_claim,
            sentiment=sentiment,
            completed=complete,
            transcript=transcript,
        ),
        usage_meter.usage,
    )


def poll_task(
    task: dict[str, Any],
    settings: Settings,
    store: Store | None = None,
    llm: LLM | None = None,
) -> dict[str, Any]:
    pool = None
    if store is None:
        pool = get_pool(settings)
        connection_context = pool.connection()
    else:
        connection_context = None
    client = llm or AnthropicLLM(settings)
    try:
        if connection_context is None:
            return _poll_task_with_store(task, settings, store, client)
        with connection_context as conn:
            return _poll_task_with_store(task, settings, Store(conn), client)
    finally:
        if pool is not None:
            pool.close()


def _poll_task_with_store(task: dict[str, Any], settings: Settings, store: Store, llm: LLM) -> dict[str, Any]:
    claim = store.get_claim_info(task["aggregated_claim_id"])
    if claim is None:
        raise ValueError(f"unknown aggregated claim {task['aggregated_claim_id']}")
    question_set = store.get_question_set_by_id(task["question_set_id"])
    respondent = store.get_respondent(task["respondent_id"])
    if question_set is None or respondent is None:
        raise ValueError("poll task references an unknown question set or respondent")
    script = QuestionScript.model_validate(question_set["script"])
    respondent.persona["aggregated_claim_id"] = task["aggregated_claim_id"]
    channel = SimulatorChannel(
        llm,
        settings,
        rng_seed=task["sim_run"],
    )
    result, usage = run_poll(
        channel,
        respondent,
        script,
        claim["politician_name"],
        claim["canonical_text"],
        llm,
    )
    store.insert_poll_response(
        task["aggregated_claim_id"],
        task["respondent_id"],
        task["question_set_id"],
        task["sim_run"],
        task["batch_no"],
        result,
    )
    return {
        "ok": True,
        "usage_usd": usage.cost_usd(settings),
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "completed": result.completed,
        "error": None,
    }
