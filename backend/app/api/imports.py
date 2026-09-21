from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import batch_dict, candidate_dict
from app.config import get_settings
from app.db import get_session
from app.models import AppSetting, ImportBatch, ImportCandidate, ImportImage
from app.schemas import CandidateUpdate, ConfirmCandidatesRequest
from app.services.ai import AIProviderError, DeepSeekProvider
from app.services.imports import confirm_candidates, record_import_failure
from app.services.ocr import OCRProviderError, PaddleOCRProvider

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _load_batch(session: Session, batch_id: int) -> ImportBatch:
    batch = session.scalar(
        select(ImportBatch)
        .where(ImportBatch.id == batch_id)
        .options(selectinload(ImportBatch.images), selectinload(ImportBatch.candidates))
    )
    if batch is None:
        raise HTTPException(404, "导入批次不存在")
    return batch


@router.post("")
async def create_import(
    files: list[UploadFile] = File(...), session: Session = Depends(get_session)
) -> dict[str, object]:
    if not files:
        raise HTTPException(400, "请选择至少一张图片")
    settings = get_settings()
    batch = ImportBatch(status="uploaded", stage="upload")
    session.add(batch)
    session.flush()
    batch_dir = settings.uploads_dir / str(batch.id)
    batch_dir.mkdir(parents=True, exist_ok=True)
    for upload in files:
        suffix = Path(upload.filename or "image.jpg").suffix.lower() or ".jpg"
        path = batch_dir / f"{uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        with path.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                digest.update(chunk)
                destination.write(chunk)
        width = height = None
        try:
            with Image.open(path) as image:
                width, height = image.size
        except OSError:
            path.unlink(missing_ok=True)
            raise HTTPException(400, f"{upload.filename} 不是可读取的图片")
        batch.images.append(
            ImportImage(
                original_name=upload.filename or path.name,
                file_path=str(path),
                sha256=digest.hexdigest(),
                mime_type=upload.content_type or "application/octet-stream",
                width=width,
                height=height,
            )
        )
    session.commit()
    return batch_dict(_load_batch(session, batch.id))


@router.get("")
def list_imports(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    batches = session.scalars(
        select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(30)
    ).all()
    return [
        {"id": item.id, "status": item.status, "stage": item.stage, "created_at": item.created_at}
        for item in batches
    ]


@router.get("/{batch_id}")
def get_import(batch_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    return batch_dict(_load_batch(session, batch_id))


@router.post("/{batch_id}/ocr")
def run_ocr(batch_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    batch = _load_batch(session, batch_id)
    language_setting = session.get(AppSetting, "ocr_language")
    gpu_setting = session.get(AppSetting, "ocr_use_gpu")
    provider = PaddleOCRProvider(
        language=language_setting.value if language_setting else "en",
        use_gpu=gpu_setting is not None and gpu_setting.value.lower() == "true",
    )
    documents: list[dict[str, object]] = []
    texts: list[str] = []
    try:
        for image in batch.images:
            document = provider.extract(Path(image.file_path))
            image.ocr_text = document.text
            image.ocr_raw_json = document.model_dump(mode="json")
            image.error_message = ""
            documents.append(document.model_dump(mode="json"))
            texts.append(document.text)
            session.commit()
        batch.raw_ocr_text = "\n\n".join(texts)
        batch.raw_ocr_json = {"provider": provider.name, "documents": documents}
        batch.status = "ocr_complete"
        batch.stage = "ocr"
        batch.error_stage = ""
        batch.error_message = ""
        session.commit()
    except OCRProviderError as error:
        record_import_failure(session, batch.id, "ocr", error)
        raise HTTPException(503, str(error)) from error
    return batch_dict(_load_batch(session, batch.id))


@router.post("/{batch_id}/structure")
async def structure_import(
    batch_id: int, session: Session = Depends(get_session)
) -> dict[str, object]:
    batch = _load_batch(session, batch_id)
    if not batch.raw_ocr_text.strip():
        raise HTTPException(409, "请先完成 OCR")
    try:
        response = await DeepSeekProvider().structure_ocr(batch.raw_ocr_text, batch.raw_ocr_json)
        if not batch.candidates:
            for draft in response.candidates:
                batch.candidates.append(
                    ImportCandidate(**draft.model_dump(), ai_raw_json=draft.model_dump())
                )
        batch.status = "review"
        batch.stage = "review"
        batch.error_stage = ""
        batch.error_message = ""
        session.commit()
    except AIProviderError as error:
        record_import_failure(session, batch.id, "ai", error)
        raise HTTPException(503, str(error)) from error
    return batch_dict(_load_batch(session, batch.id))


@router.patch("/{batch_id}/candidates/{candidate_id}")
def update_candidate(
    batch_id: int,
    candidate_id: int,
    payload: CandidateUpdate,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    candidate = session.scalar(
        select(ImportCandidate).where(
            ImportCandidate.id == candidate_id, ImportCandidate.batch_id == batch_id
        )
    )
    if candidate is None:
        raise HTTPException(404, "候选词条不存在")
    if candidate.confirmed:
        raise HTTPException(409, "已入库候选不可再编辑")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(candidate, key, value)
    session.commit()
    session.refresh(candidate)
    return candidate_dict(candidate)


@router.post("/{batch_id}/confirm")
def confirm_import(
    batch_id: int, payload: ConfirmCandidatesRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    try:
        created = confirm_candidates(session, batch_id, payload.candidate_ids)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return {"created": len(created), "word_ids": [word.id for word in created]}
