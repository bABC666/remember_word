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
from app.models import AppSetting, HistoryEvent, ImportBatch, ImportCandidate, ImportImage
from app.schemas import CandidateUpdate, ConfirmCandidatesRequest
from app.services.ai import AIProviderError, DeepSeekProvider
from app.services.imports import confirm_candidates, process_batch_ocr, record_import_failure
from app.services.ocr import OCRProviderError, get_paddle_provider

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _load_batch(session: Session, batch_id: int) -> ImportBatch:
    batch = session.scalar(
        select(ImportBatch)
        .where(ImportBatch.id == batch_id, ImportBatch.is_deleted.is_(False))
        .options(selectinload(ImportBatch.images), selectinload(ImportBatch.candidates))
    )
    if batch is None:
        raise HTTPException(404, "导入批次不存在")
    return batch


async def _save_uploads(
    batch: ImportBatch, files: list[UploadFile], session: Session
) -> None:
    settings = get_settings()
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


@router.post("")
async def create_import(
    files: list[UploadFile] = File(...), session: Session = Depends(get_session)
) -> dict[str, object]:
    if not files:
        raise HTTPException(400, "请选择至少一张图片")
    batch = ImportBatch(status="uploaded", stage="upload")
    session.add(batch)
    session.flush()
    await _save_uploads(batch, files, session)
    session.commit()
    return batch_dict(_load_batch(session, batch.id))


@router.post("/{batch_id}/images")
async def append_import_images(
    batch_id: int,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    batch = _load_batch(session, batch_id)
    if batch.status == "confirmed" or batch.candidates:
        raise HTTPException(409, "已进入词条校对，不能再追加图片；请新建导入批次")
    if not files:
        raise HTTPException(400, "请选择至少一张图片")
    await _save_uploads(batch, files, session)
    batch.status = "uploaded"
    batch.stage = "upload"
    batch.error_stage = ""
    batch.error_message = ""
    session.commit()
    return batch_dict(_load_batch(session, batch.id))


def _rebuild_batch_ocr(batch: ImportBatch) -> None:
    active = [image for image in batch.images if not image.is_deleted]
    completed = [image for image in active if image.ocr_text.strip()]
    batch.raw_ocr_text = "\n\n".join(image.ocr_text for image in completed)
    batch.raw_ocr_json = {
        "provider": batch.provider,
        "documents": [image.ocr_raw_json for image in completed],
    }
    all_complete = bool(active) and len(completed) == len(active)
    batch.status = "ocr_complete" if all_complete else "uploaded"
    batch.stage = "ocr" if all_complete else "upload"


@router.delete("/{batch_id}/images/{image_id}")
def remove_import_image(
    batch_id: int, image_id: int, session: Session = Depends(get_session)
) -> dict[str, object]:
    batch = _load_batch(session, batch_id)
    if batch.status == "confirmed" or batch.candidates:
        raise HTTPException(409, "已进入词条校对，不能单独删除原图；可取消候选或放弃批次")
    image = next(
        (item for item in batch.images if item.id == image_id and not item.is_deleted), None
    )
    if image is None:
        raise HTTPException(404, "导入图片不存在")
    image.is_deleted = True
    _rebuild_batch_ocr(batch)
    session.add(
        HistoryEvent(
            event_type="import_image_removed",
            entity_type="import_image",
            entity_id=image.id,
            payload={"batch_id": batch.id, "file_path": image.file_path},
        )
    )
    session.commit()
    return batch_dict(_load_batch(session, batch.id))


@router.delete("/{batch_id}")
def abandon_import(
    batch_id: int, session: Session = Depends(get_session)
) -> dict[str, str]:
    batch = _load_batch(session, batch_id)
    if any(candidate.confirmed for candidate in batch.candidates):
        raise HTTPException(409, "已有词条正式入库，不能移除这个批次")
    batch.is_deleted = True
    session.add(
        HistoryEvent(
            event_type="import_batch_removed",
            entity_type="import_batch",
            entity_id=batch.id,
            payload={"recoverable": True},
        )
    )
    session.commit()
    return {"message": "导入批次已移除"}


@router.get("")
def list_imports(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    batches = session.scalars(
        select(ImportBatch)
        .where(ImportBatch.is_deleted.is_(False))
        .order_by(ImportBatch.created_at.desc())
        .limit(30)
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
    provider = get_paddle_provider(
        language=language_setting.value if language_setting else "en",
        use_gpu=gpu_setting is not None and gpu_setting.value.lower() == "true",
    )
    try:
        process_batch_ocr(session, batch, provider)
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
