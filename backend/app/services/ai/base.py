from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class AIProviderError(RuntimeError):
    pass


class CandidateDraft(BaseModel):
    word: str = Field(min_length=1, max_length=160)
    phonetic: str = ""
    part_of_speech: str = ""
    source_meanings: list[str] = Field(default_factory=list)
    source_raw: str
    anchor: str = Field(min_length=1, max_length=300)
    semantic_note: str = ""
    possible_issue: bool = False
    issue_note: str = ""

    @field_validator("word")
    @classmethod
    def normalize_word(cls, value: str) -> str:
        return value.strip()


class StructuredCandidates(BaseModel):
    candidates: list[CandidateDraft]


class ArticleGeneration(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    article: str = Field(min_length=100)
    actual_used_words: list[str]


class JudgementSuggestion(BaseModel):
    suggestion: str = Field(pattern="^(know|fuzzy|fail)$")
    explanation: str = Field(min_length=1, max_length=800)


class AIProvider(Protocol):
    async def structure_ocr(self, raw_text: str, raw_json: object) -> StructuredCandidates: ...
    async def generate_article(self, target_words: list[str], length: int) -> ArticleGeneration: ...
    async def judge_meaning(
        self, word: str, user_meaning: str, context: str, source_meanings: list[str]
    ) -> JudgementSuggestion: ...
