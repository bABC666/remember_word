from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field, field_validator


class AIProviderError(RuntimeError):
    pass


class CandidateDraft(BaseModel):
    word: str = Field(min_length=1, max_length=160)
    phonetic: str = Field(default="", max_length=200)
    part_of_speech: str = Field(default="", max_length=80)
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

    @field_validator("source_meanings")
    @classmethod
    def normalize_meanings(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class StructuredCandidates(BaseModel):
    candidates: list[CandidateDraft]


class ArticleGeneration(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    article: str = Field(min_length=100)
    actual_used_words: list[str]


class JudgementSuggestion(BaseModel):
    suggestion: str = Field(pattern="^(know|fuzzy|fail)$")
    explanation: str = Field(min_length=1, max_length=800)


class WordLookupResult(BaseModel):
    normalized_word: str = Field(min_length=1, max_length=160)
    phonetic: str = Field(default="", max_length=200)
    part_of_speech: str = Field(default="", max_length=80)
    meaning: str = Field(min_length=1, max_length=500)
    explanation: str = Field(default="", max_length=1200)


class ArticleTranslation(BaseModel):
    translation: str = Field(min_length=20)


class AIProvider(Protocol):
    async def structure_ocr(self, raw_text: str, raw_json: object) -> StructuredCandidates: ...
    async def generate_article(self, target_words: list[str], length: int) -> ArticleGeneration: ...
    async def judge_meaning(
        self, word: str, user_meaning: str, context: str, source_meanings: list[str]
    ) -> JudgementSuggestion: ...
    async def lookup_word(self, word: str, context: str) -> WordLookupResult: ...
    async def translate_article(self, title: str, content: str) -> ArticleTranslation: ...
