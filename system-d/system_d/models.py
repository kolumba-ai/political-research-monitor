"""Validated data models used at System D boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ExtractedClaim(BaseModel):
    claim_text: str = Field(min_length=1)
    about_politician: str = Field(min_length=1)
    is_factual_claim: bool
    source_url: str | None


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim]


class CommentScore(BaseModel):
    index: int = Field(ge=0)
    sentiment: float = Field(ge=-1, le=1)
    stance: Literal["believes", "disputes", "neutral"]


class CommentScores(BaseModel):
    scores: list[CommentScore]


class CanonicalClaim(BaseModel):
    canonical_text: str = Field(min_length=1)
    testable: bool


class QuestionScript(BaseModel):
    opening: str = Field(min_length=1)
    belief_q: str = Field(min_length=1)
    sentiment_q: str = Field(min_length=1)
    open_q: str = Field(min_length=1)
    closing: str = Field(min_length=1)

    @model_validator(mode="after")
    def total_word_limit(self) -> QuestionScript:
        if sum(len(value.split()) for value in self.model_dump().values()) > 225:
            raise ValueError("question script must contain at most 225 words")
        return self


class Persona(BaseModel):
    name: str = Field(min_length=1)
    age: int = Field(ge=18, le=100)
    state: str = Field(min_length=1)
    municipality_type: Literal["urbano", "semiurbano", "rural"]
    occupation: str = Field(min_length=1)
    education: str = Field(min_length=1)
    media_diet: str = Field(min_length=1)
    political_leaning: str = Field(min_length=1)
    trust_in_institutions: Literal["baja", "media", "alta"]
    speaking_style: str = Field(min_length=1)


class PersonaBatch(BaseModel):
    personas: list[Persona]


class PollScore(BaseModel):
    believes_claim: Literal["yes", "no", "dont_know"] | None
    sentiment_choice: int | None = Field(default=None, ge=1, le=5)
    refused: bool
    completed: bool


class TranscriptMessage(BaseModel):
    role: Literal["agent", "respondent"]
    text: str


class PollResult(BaseModel):
    believes_claim: bool | None
    sentiment: float | None = Field(default=None, ge=-1, le=1)
    completed: bool
    transcript: list[TranscriptMessage]


@dataclass(frozen=True)
class Respondent:
    id: str
    stratum_id: str
    persona: dict
    channel: str
