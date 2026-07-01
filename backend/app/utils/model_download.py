from pathlib import Path
from typing import Optional

MODELSCOPE_MAP = {
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-small-zh-v1.5": "BAAI/bge-small-zh-v1.5",
}


def download_model(model_name: str, cache_dir: Optional[Path] = None) -> Path:
    if cache_dir is None:
        cache_dir = Path("data/models")
    cache_dir = cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    modelscope_id = MODELSCOPE_MAP.get(model_name, model_name)

    structured_path = cache_dir / model_name
    modelscope_path = cache_dir / modelscope_id
    flat_path = cache_dir / model_name.replace("/", "_")

    for candidate in (modelscope_path, structured_path, flat_path):
        if candidate.exists():
            return candidate

    try:
        from modelscope import snapshot_download
        local_path = snapshot_download(modelscope_id, cache_dir=str(cache_dir))
        return Path(local_path)
    except Exception:
        pass

    from huggingface_hub import snapshot_download as hf_download
    hf_download(
        model_name,
        local_dir=str(structured_path),
        local_dir_use_symlinks=False,
    )
    return structured_path
