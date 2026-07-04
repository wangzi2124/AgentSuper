import os
import mimetypes
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

GENERATED_DIR = Path(__file__).resolve().parents[2] / "data" / "generated"

router = APIRouter()


class GeneratedFile(BaseModel):
    filename: str
    size: int
    created_at: str


class GeneratedFileList(BaseModel):
    files: list[GeneratedFile]
    total: int


@router.get("/", response_model=GeneratedFileList)
async def list_generated(q: Optional[str] = None):
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    files: list[GeneratedFile] = []
    for f in sorted(GENERATED_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.is_file():
            if q and q.lower() not in f.stem.lower():
                continue
            stat = f.stat()
            files.append(GeneratedFile(
                filename=f.name,
                size=stat.st_size,
                created_at=str(f.stat().st_mtime),
            ))
    return GeneratedFileList(files=files, total=len(files))


@router.get("/download/{filename}")
async def download_generated(filename: str):
    filepath = GENERATED_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type, _ = mimetypes.guess_type(filename)
    return FileResponse(
        str(filepath),
        media_type=media_type or "application/octet-stream",
        filename=filename,
    )


@router.delete("/{filename}")
async def delete_generated(filename: str):
    filepath = GENERATED_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(str(filepath))
    return {"message": f"Deleted {filename}"}
