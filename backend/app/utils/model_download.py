import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

MODELSCOPE_MAP = {
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-small-zh-v1.5": "BAAI/bge-small-zh-v1.5",
    "cross-encoder/ms-marco-MiniLM-L-6-v2": "cross-encoder/ms-marco-MiniLM-L6-v2",
}

# 每次尝试的整体超时（秒）；0 = 交给底层库自身的 HTTP 超时/续传机制
_DOWNLOAD_TIMEOUT = settings.model_download_timeout
# 每个下载源内的额外重试次数
_DOWNLOAD_RETRIES = settings.model_download_retries


def _call_with_timeout(fn, timeout: float, *args, **kwargs):
    """在独立线程执行阻塞下载；超时则放弃本次尝试（线程无法强杀，仅不再等待）。

    snapshot_download 自带断点续传与 HTTP 重试，多次干净退出不会损坏缓存，
    因此超时后直接视为本次尝试失败并触发重试/回退即可。
    """
    if timeout <= 0:
        return fn(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise TimeoutError(
                f"model download timed out after {timeout}s (attempt abandoned)"
            ) from None


def _retry(label: str, fn, *args, **kwargs) -> str:
    """带重试的下载：每次尝试套整体超时；全部失败抛出带引导信息的 RuntimeError。"""
    last: Exception | None = None
    for attempt in range(1 + _DOWNLOAD_RETRIES):
        try:
            started = time.monotonic()
            result = _call_with_timeout(fn, float(_DOWNLOAD_TIMEOUT), *args, **kwargs)
            logger.info(
                "[download] %s attempt %d/%d ok in %.1fs",
                label, attempt, 1 + _DOWNLOAD_RETRIES, time.monotonic() - started,
            )
            return result
        except Exception as e:  # noqa: BLE001 —— 下载源内部异常统一转为下一尝试
            last = e
            logger.warning(
                "[download] %s attempt %d/%d failed: %s",
                label, attempt + 1, 1 + _DOWNLOAD_RETRIES,
                getattr(e, "message", None) or repr(e),
            )
            time.sleep(1.0 * attempt)  # 短暂退避，避免并发源压力
    raise RuntimeError(
        f"Failed to download model ({label}) after {1 + _DOWNLOAD_RETRIES} attempts: {last!r}"
    ) from last


def download_model(model_name: str, cache_dir: Optional[Path] = None) -> Path:
    """下载嵌入模型，优先从ModelScope下载，失败后回退到HuggingFace。

    容错（C3）：ModelScope / HuggingFace 各做 `model_download_retries` 次重试、
    单次尝试整体超时 `model_download_timeout` 秒；两个源都失败时抛出带
    引导信息的 RuntimeError，由调用方（embeddings/reranker）的降级路径接管。
    """
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

    # 1) ModelScope 优先
    try:
        from modelscope import snapshot_download
    except ImportError:
        snapshot_download = None

    if snapshot_download is not None:
        try:
            local_path = _retry(
                "ModelScope",
                snapshot_download,
                modelscope_id,
                cache_dir=str(cache_dir),
            )
            return Path(local_path)
        except Exception:  # noqa: BLE001 —— 网络/下载失败 → 回退 HuggingFace
            logger.warning("[download] ModelScope download failed, falling back to HuggingFace")

    # 2) HuggingFace 回退
    from huggingface_hub import snapshot_download as hf_download

    hf_dir = cache_dir / model_name.replace("/", "_")
    if hf_dir.exists():
        return hf_dir

    try:
        structured_path = Path(
            _retry(
                "HuggingFace",
                hf_download,
                model_name,
                local_dir=str(structured_path),
                local_dir_use_symlinks=False,
            )
        )
        return structured_path
    except Exception as e:
        # 两个源都失败：给出降级引导信息（延续旧行为：局部缓存目录已被战果保留）
        if structured_path.exists() and any(structured_path.iterdir()):
            logger.warning(
                "[download] partial cache remains at %s; retry will resume (%s)",
                structured_path, e,
            )
        raise RuntimeError(
            "Failed to download embedding model '%s' from both ModelScope and HuggingFace. "
            "In offline environments download via ModelScope CLI: "
            "'modelscope download --model %s --local_dir %s'"
            % (model_name, modelscope_id, structured_path)
        ) from e