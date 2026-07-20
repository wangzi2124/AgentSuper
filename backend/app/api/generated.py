"""生成文件管理 API 路由模块。

提供生成文件的列表查询、下载和删除功能。
"""

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
    """生成文件信息模型。"""
    filename: str
    size: int
    created_at: str


class GeneratedFileList(BaseModel):
    """生成文件列表响应模型。"""
    files: list[GeneratedFile]
    total: int


@router.get("/", response_model=GeneratedFileList)
async def list_generated(q: Optional[str] = None):
    """获取生成目录下所有文件的列表，支持按文件名搜索。"""
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
    """下载指定的生成文件。"""
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
    """删除指定的生成文件。"""
    filepath = GENERATED_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(str(filepath))
    return {"message": f"Deleted {filename}"}
