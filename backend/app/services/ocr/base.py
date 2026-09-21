from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class OCRProviderError(RuntimeError):
    pass


class OCRLine(BaseModel):
    text: str
    confidence: float = Field(ge=0, le=1)
    box: list[list[float]] = Field(default_factory=list)


class OCRDocument(BaseModel):
    provider: str
    lines: list[OCRLine]
    raw: object

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text.strip())


class OCRProvider(Protocol):
    name: str

    def extract(self, image_path: Path) -> OCRDocument: ...
