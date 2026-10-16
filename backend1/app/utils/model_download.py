import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import requests

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

# 一次“干净完整”下载完成的哨兵文件；带它的缓存目录可免检直接复用
_COMPLETE_MARKER = ".agents-complete"
# 权重文件大小的经验下限（字节）：低于它视为不完整缓存，需续传/重建
_WEIGHTS_FLOOR = 1_000_000
# 单次下载僵尸线程的回收软上限（秒）：超时后仍不结束则放弃本轮，由下次启动续传
_ZOMBIE_GRACE = 300
# 单文件分块大小（断点续传读缓冲）
_CHUNK_SIZE = 1 << 16


class _DownloadAbandoned(RuntimeError):
    """超时后回收失败（僵尸下载仍在跑）：中止本轮并交给下次启动续传，
    避免与下一个并发尝试同写一个缓存目录造成损坏。"""


def _run_attempt_with_timeout(fn, timeout: float, *args, **kwargs):
    """在守护线程执行阻塞下载；超时则尽力回收该次尝试。

    - 成功/异常：等线程结束返回结果或抛出其异常。
    - 超时：回收线程至多 `_ZOMBIE_GRACE`（串行优先，防止两次尝试并写同一缓存）；
      回收成功则沿用其最终结果；仍无法终止则抛 `_DownloadAbandoned`，
      由上层终止本轮、下次启动/下次调用从既有局部缓存续传。
    - 底层库（hf_hub / modelscope snapshot_download）自带断点续传：同一缓存目录
      被反复进入只会补齐缺失文件，不重复下载已完成的部分。
    """
    if timeout <= 0:
        return fn(*args, **kwargs)

    result: dict = {}
    done = threading.Event()

    def _worker():
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 —— 线程边界统一收口
            result["error"] = e
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if done.wait(timeout):
        pass
    else:
        t.join(min(float(_ZOMBIE_GRACE), float(timeout)))
        if t.is_alive():
            raise _DownloadAbandoned(
                f"download attempt did not stop within "
                f"{min(float(_ZOMBIE_GRACE), float(timeout)):.0f}s after timeout "
                f"({timeout:.0f}s); resume on next startup"
            )
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _retry(label: str, fn, *args, **kwargs) -> Path:
    """带重试的下载：每次尝试套整体超时；全部失败抛出带引导信息的 RuntimeError。

    串行语义：尝试被逐个回收后再进入下一次（底层库在既有局部缓存上续传），
    不再出现“放弃的僵尸线程”与新一轮尝试并发写同一缓存。
    """
    last: Exception | None = None
    for attempt in range(1 + _DOWNLOAD_RETRIES):
        try:
            started = time.monotonic()
            result = _run_attempt_with_timeout(fn, float(_DOWNLOAD_TIMEOUT), *args, **kwargs)
            logger.info(
                "[download] %s attempt %d/%d ok in %.1fs",
                label, attempt + 1, 1 + _DOWNLOAD_RETRIES, time.monotonic() - started,
            )
            return result
        except _DownloadAbandoned as e:
            raise RuntimeError(
                f"Download of model ({label}) abandoned after {attempt + 1} attempt(s): {e}"
            ) from e
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


# ---------------------------------------------------------------------------
# 通用单文件断点续传（HTTP Range + .part + 原子替换 + 可选大小校验）
# ---------------------------------------------------------------------------

_CONTENT_RANGE_RE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")


def _resume_offset(part: Path) -> int:
    try:
        return part.stat().st_size
    except OSError:
        return 0


def _parse_content_range(value: Optional[str]):
    """解析 `bytes start-end/total`；total 为 * 或缺失时 None。"""
    if not value:
        return None
    m = _CONTENT_RANGE_RE.match(value)
    if not m:
        return None
    start, end, total = int(m.group(1)), int(m.group(2)), m.group(3)
    return start, end, (int(total) if total.isdigit() else None)


def resumable_download(
    url: str,
    dest,
    *,
    timeout: float = None,
    retries: int = None,
    expected_size: Optional[int] = None,
    headers: Optional[dict] = None,
) -> Path:
    """通用断点续传下载器。

    - 未完成的写入落在 `<dest>.part`；重启/重试时基于已下载字节发 `Range` 请求续传。
    - 服务器不支持 Range（200 全量响应）时自动从头重建 `.part`。
    - 成功后原子 `os.replace` 到目标路径；`expected_size` 不符则报错并保留 `.part` 待续传。
    - 每次尝试串行执行；失败按指数退避重试（默认 `model_download_retries` 次）。
    """
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    timeout = float(timeout) if timeout is not None else float(_DOWNLOAD_TIMEOUT)
    retries = int(retries) if retries is not None else int(_DOWNLOAD_RETRIES)
    req = dict(headers or {})
    last: Exception | None = None
    for attempt in range(1 + retries):
        try:
            _resumable_download_once(url, dest, part, req, timeout, expected_size)
            return dest
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning(
                "[dl-resume] %s attempt %d/%d failed: %r (partial=%d bytes)",
                dest.name, attempt + 1, 1 + retries, e, _resume_offset(part),
            )
            time.sleep(min(1.0 * attempt, 4.0))
    raise RuntimeError(
        f"Download failed after {1 + retries} attempts: {url}: {last!r}"
    ) from last


def _resumable_download_once(url: str, dest: Path, part: Path, headers: dict,
                             timeout: float, expected_size: Optional[int]):
    offset = _resume_offset(part)
    req = dict(headers)
    if offset:
        req["Range"] = f"bytes={offset}-"
    conn_timeout = min(60.0, timeout) if timeout > 0 else 30.0
    read_timeout = timeout if timeout > 0 else None
    with requests.get(url, headers=req, stream=True,
                      timeout=(conn_timeout, read_timeout)) as r:  # type: ignore[arg-type]
        if r.status_code == 206:
            cr = _parse_content_range(r.headers.get("Content-Range"))
            if cr is not None and cr[0] != offset:
                # 服务器续传起点与我们不一致 → 重建
                mode = "wb"
            else:
                mode = "ab"
        elif r.status_code == 200:
            mode = "wb"  # 服务器忽略 Range → 全量重来
        else:
            r.raise_for_status()
            raise RuntimeError(f"unexpected status {r.status_code}")

        with open(part, mode) as f:
            for chunk in r.iter_content(_CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
        size = _resume_offset(part)
        if expected_size is not None and size != expected_size:
            raise RuntimeError(
                f"size mismatch: got {size}, expected {expected_size} (partial kept for resume)"
            )
    os.replace(part, dest)


# ---------------------------------------------------------------------------
# 快照缓存完整性（哨兵 + 启发式）
# ---------------------------------------------------------------------------

def _looks_complete(d: Path) -> bool:
    """启发式完整性：存在配置类文件且树内最大文件达到权重下限。

    仅凭“目录存在”不可靠（半途失败也会留下目录）——这会把残缺缓存当成品。
    """
    if not d.is_dir():
        return False
    if not any((d / k).exists() for k in ("config.json", "tokenizer.json", "tokenizer_config.json")):
        return False
    largest = 0
    try:
        for p in d.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                largest = max(largest, p.stat().st_size)
    except OSError:
        return False
    return largest >= _WEIGHTS_FLOOR


def _write_marker(d: Path):
    try:
        (d / _COMPLETE_MARKER).touch(exist_ok=True)
    except OSError as e:
        logger.debug("[download] cannot write complete marker at %s: %s", d, e)


def _accept(candidate: Path) -> bool:
    """候选目录可用：带哨兵 → 直接复用；无哨兵但启发式完整 → 补哨兵后复用；
    否则视为不完整，需要进入续传流程。"""
    if candidate.exists():
        if (candidate / _COMPLETE_MARKER).exists() or _looks_complete(candidate):
            _write_marker(candidate)
            return True
        logger.warning("[download] partial cache at %s (resume needed)", candidate)
    return False


def download_model(model_name: str, cache_dir: Optional[Path] = None) -> Path:
    """下载嵌入模型，优先从 ModelScope 下载，失败后回退到 HuggingFace。

    容错（C3）：每个源做 `model_download_retries` 次串行重试、单次尝试整体超时
    `model_download_timeout` 秒；重试间基于既有 `.part`/局部缓存续传（底层库自带
    Range 续传）。缓存目录带 `.agents-complete` 哨兵或启发式完整时免网络直接复用；
    半途失败留下的残缺目录会被识别并重新续传补齐，而非被当成成品。
    两个源都失败时抛出带引导信息的 RuntimeError，由调用方（embeddings/reranker）
    的降级路径接管。
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
        if _accept(candidate):
            return candidate

    # 1) ModelScope 优先
    try:
        from modelscope import snapshot_download
    except ImportError:  # noqa: F821
        snapshot_download = None

    if snapshot_download is not None:
        try:
            local_path = _retry(
                "ModelScope",
                snapshot_download,
                modelscope_id,
                cache_dir=str(cache_dir),
            )
            p = Path(str(local_path))
            _write_marker(p)
            return p
        except Exception:  # noqa: BLE001 —— 网络/下载失败 → 回退 HuggingFace
            logger.warning("[download] ModelScope download failed, falling back to HuggingFace")

    # 2) HuggingFace 回退
    from huggingface_hub import snapshot_download as hf_download

    try:
        _retry(
            "HuggingFace",
            hf_download,
            model_name,
            local_dir=str(structured_path),
            local_dir_use_symlinks=False,
        )
        _write_marker(structured_path)
        return structured_path
    except Exception as e:  # noqa: BLE001
        # 两个源都失败：给出降级引导信息（局部缓存目录已被战果保留）
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