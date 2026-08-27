"""Quality Control API endpoints.

Handles product image upload and pre-processing for the Manufacturing Quality
Control vertical slice (ARCHITECTURE.md section 2, API_CONTRACT.yaml).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict

from app.core.logging import get_logger
from app.security.dependencies import CurrentPrincipal

logger = get_logger(__name__)

router = APIRouter(prefix="/quality", tags=["Quality Control"])

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
UPLOAD_DIR = Path("./data/uploads")


class ImageUploadResponse(BaseModel):
    """Reference to an uploaded image ready for quality check."""

    model_config = ConfigDict(frozen=True)

    ref: str
    content_type: str
    size_bytes: int
    classification: str = "manufacturing_product_image"


@router.post(
    "/upload",
    summary="Upload a product image for quality inspection",
    response_model=ImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_quality_image(
    file: Annotated[UploadFile, File(...)],
    principal: CurrentPrincipal,
) -> ImageUploadResponse:
    """Upload product image, validate, and store temporarily for inspection."""
    content_type = file.content_type or ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{content_type}'. Allowed types: {list(ALLOWED_CONTENT_TYPES.keys())}",
        )

    content = await file.read()
    size_bytes = len(content)

    if size_bytes == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    if size_bytes > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE} bytes",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = ALLOWED_CONTENT_TYPES[content_type]
    file_id = str(uuid.uuid4())
    stored_filename = f"{file_id}{ext}"
    dest_path = UPLOAD_DIR / stored_filename

    # Save to disk
    with open(dest_path, "wb") as f:
        f.write(content)

    logger.info(
        "quality_image_uploaded",
        extra={
            "tenant_id": principal.tenant_id,
            "ref": str(dest_path),
            "size_bytes": size_bytes,
            "content_type": content_type,
        },
    )

    return ImageUploadResponse(
        ref=str(dest_path),
        content_type=content_type,
        size_bytes=size_bytes,
        classification="manufacturing_product_image",
    )
