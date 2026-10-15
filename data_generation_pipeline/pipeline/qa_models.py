"""Structured output for family-conditioned question generation."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class StructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GoldAnswer(StructuredOutput):
    answer: Text


class SolutionStep(StructuredOutput):
    step: Text
    record_ids: list[Text]


class Candidate(StructuredOutput):
    id: Text
    q: Text
    answer_status: Literal["answerable"]
    gold: GoldAnswer
    solution: list[SolutionStep] = Field(min_length=2, max_length=5)
    evidence_paths: list[Annotated[list[Text], Field(min_length=1)]] = Field(min_length=1)
    evidence: list[Text] = Field(min_length=1)


class Omission(StructuredOutput):
    id: Text
    reason: Text


class CandidateBatch(StructuredOutput):
    schema_version: Literal["qa_v1"]
    items: list[Candidate]
    omitted: list[Omission]


class Perturbation(StructuredOutput):
    source_id: Text
    original: Text
    replacement: Text


class UnanswerableCandidate(StructuredOutput):
    id: Text
    q: Text
    answer_status: Literal["unanswerable"]
    gold: GoldAnswer
    solution: list[SolutionStep] = Field(min_length=2, max_length=5)
    evidence_paths: list[list[Text]] = Field(max_length=0)
    evidence: list[Text] = Field(max_length=0)
    perturbation: Perturbation


class UnanswerableBatch(StructuredOutput):
    schema_version: Literal["qa_v1"]
    items: list[UnanswerableCandidate]
    omitted: list[Omission]
