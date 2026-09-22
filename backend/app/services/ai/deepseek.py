from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.services.ai.base import (
    AIProviderError,
    ArticleGeneration,
    ArticleTranslation,
    JudgementSuggestion,
    StructuredCandidates,
    WordLookupResult,
)

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


class DeepSeekProvider:
    def __init__(
        self, api_key: str | None = None, base_url: str | None = None, model: str | None = None
    ) -> None:
        config = get_settings().ai_config()
        self.api_key = api_key if api_key is not None else config["api_key"]
        self.base_url = (base_url or config["base_url"]).rstrip("/")
        self.model = model or config["model"]

    def _prompt(self, name: str) -> str:
        return (PROMPT_DIR / name).read_text(encoding="utf-8")

    async def _chat_json(
        self, system: str, user: str, response_model: type[ResponseModel]
    ) -> ResponseModel:
        if not self.api_key:
            raise AIProviderError("尚未配置 DeepSeek API Key")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return response_model.model_validate(json.loads(content))
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise AIProviderError(f"DeepSeek 返回无效结果：{error}") from error

    async def structure_ocr(self, raw_text: str, raw_json: object) -> StructuredCandidates:
        schema = json.dumps(StructuredCandidates.model_json_schema(), ensure_ascii=False)
        user = f"OCR_TEXT:\n{raw_text}\n\nOCR_JSON:\n{json.dumps(raw_json, ensure_ascii=False)}\n\nJSON_SCHEMA:\n{schema}"
        return await self._chat_json(self._prompt("structure_ocr.txt"), user, StructuredCandidates)

    async def generate_article(self, target_words: list[str], length: int) -> ArticleGeneration:
        schema = json.dumps(ArticleGeneration.model_json_schema(), ensure_ascii=False)
        user = f"目标词：{json.dumps(target_words, ensure_ascii=False)}\n目标长度：{length}词\nJSON_SCHEMA:\n{schema}"
        return await self._chat_json(self._prompt("generate_article.txt"), user, ArticleGeneration)

    async def judge_meaning(
        self, word: str, user_meaning: str, context: str, source_meanings: list[str]
    ) -> JudgementSuggestion:
        schema = json.dumps(JudgementSuggestion.model_json_schema(), ensure_ascii=False)
        user = (
            f"单词：{word}\n用户理解：{user_meaning}\n文章语境：{context}\n"
            f"原书释义：{json.dumps(source_meanings, ensure_ascii=False)}\nJSON_SCHEMA:\n{schema}"
        )
        return await self._chat_json(self._prompt("judge_meaning.txt"), user, JudgementSuggestion)

    async def lookup_word(self, word: str, context: str) -> WordLookupResult:
        schema = json.dumps(WordLookupResult.model_json_schema(), ensure_ascii=False)
        user = f"待解释词：{word}\n文章语境：{context}\nJSON_SCHEMA:\n{schema}"
        return await self._chat_json(self._prompt("lookup_word.txt"), user, WordLookupResult)

    async def translate_article(self, title: str, content: str) -> ArticleTranslation:
        schema = json.dumps(ArticleTranslation.model_json_schema(), ensure_ascii=False)
        user = f"标题：{title}\n英文原文：\n{content}\nJSON_SCHEMA:\n{schema}"
        return await self._chat_json(
            self._prompt("translate_article.txt"), user, ArticleTranslation
        )
