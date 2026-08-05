"""轻量用户身份鉴权：X-User-Id 绑定签名 token。

威胁模型：X-User-Id 请求头即身份，无认证时可在局域网中伪造头越权读取他人会话。
本模块提供 trust-on-first-use（首次信任）的签名身份：

1. 客户端首次注册：生成随机 user_id + device_secret，POST /api/auth/register
   - 服务端只存储 user_id → hmac(secret, device_secret)（不存明文设备密钥）
2. 之后客户端通过 /api/auth/token 用 device_secret 换取短期签名 token
   - token = base64(uid) . base64(exp) . hmac(secret, "uid:exp:registered_hash")
3. 中间件校验每个 /api/* 请求的 X-User-Id + X-Auth-Token；伪造 uid 无法通过签名校验

未配置 AUTH_TOKEN_SECRET 时所有校验关闭（保持默认本地部署行为）。
"""

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_users_cache: dict[str, str] | None = None  # user_id -> device_secret 的 HMAC 哈希


def enabled() -> bool:
    """是否启用身份签名校验（设置了 AUTH_TOKEN_SECRET）。"""
    return bool(settings.auth_secret)


def _users_path() -> Path:
    p = settings.auth_users_path or "data/auth_users.json"
    path = Path(p)
    return path if path.is_absolute() else Path(__file__).resolve().parents[1] / p


def _secret_hash(device_secret: str) -> str:
    return hmac.new(
        settings.auth_secret.encode(), device_secret.encode(), hashlib.sha256
    ).hexdigest()


def _load_users() -> dict[str, str]:
    global _users_cache
    if _users_cache is not None:
        return _users_cache
    path = _users_path()
    if not path.exists():
        _users_cache = {}
        return _users_cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _users_cache = {str(k): str(v) for k, v in data.items()}
    except Exception as e:  # noqa: BLE001
        logger.warning("auth users load failed: %s", e)
        _users_cache = {}
    return _users_cache


def _save_users(users: dict[str, str]) -> None:
    path = _users_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("auth users save failed: %s", e)


def register(user_id: str, device_secret: str) -> tuple[bool, str]:
    """首次注册绑定 user_id 与 device_secret（幂等）。

    已存在且密钥不符时拒绝，防止冒充已注册用户。返回 (ok, error)。
    """
    if not user_id or not device_secret:
        return False, "user_id 与 device_secret 不能为空"
    if not enabled():
        return False, "身份签名未启用（未配置 AUTH_TOKEN_SECRET）"
    new_hash = _secret_hash(device_secret)
    global _users_cache
    with _lock:
        users = dict(_load_users())
        existing = users.get(user_id)
        if existing is not None and not hmac.compare_digest(existing, new_hash):
            return False, "user_id 已被其他设备注册"
        users[user_id] = new_hash
        _save_users(users)
        _users_cache = users
    return True, ""


def verify_device(user_id: str, device_secret: str) -> bool:
    """校验 device_secret 是否为该 user_id 的注册密钥。"""
    if not enabled() or not user_id or not device_secret:
        return False
    existing = _load_users().get(user_id)
    if existing is None:
        return False
    return hmac.compare_digest(existing, _secret_hash(device_secret))


def issue_token(user_id: str) -> tuple[str, int]:
    """为已注册 user_id 签发签名 token，返回 (token, expires_at_epoch)。"""
    exp = int(time.time()) + settings.auth_token_ttl
    uid_b = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
    exp_b = base64.urlsafe_b64encode(str(exp).encode()).decode().rstrip("=")
    reg_hash = _load_users().get(user_id, "")
    sig = hmac.new(
        settings.auth_secret.encode(), f"{user_id}:{exp}:{reg_hash}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{uid_b}.{exp_b}.{sig}", exp


def verify_token(user_id: str, token: str) -> bool:
    """校验 token 是否绑定 user_id 且未过期。"""
    if not enabled() or not token:
        return False
    try:
        uid_b, exp_b, sig = token.split(".")
        uid = base64.urlsafe_b64decode(uid_b + "=" * (-len(uid_b) % 4)).decode()
        exp = int(base64.urlsafe_b64decode(exp_b + "=" * (-len(exp_b) % 4)).decode())
    except Exception:  # noqa: BLE001
        return False
    if uid != user_id:
        return False
    if exp < time.time():
        return False
    reg_hash = _load_users().get(user_id, "")
    expected = hmac.new(
        settings.auth_secret.encode(), f"{user_id}:{exp}:{reg_hash}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


class AuthMiddleware:
    """校验每个 /api/* 请求的 X-User-Id + X-Auth-Token。

    仅当配置了 AUTH_TOKEN_SECRET 时生效。放行：
      - 非 /api/* 路径（文档、健康检查等）
      - /api/auth/*（注册 / 令牌 / 状态）
      - OPTIONS 预检请求（CORS，不含业务头）
    其余请求必须携带与 user_id 匹配且未过期的签名 token，否则返回 401。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not enabled():
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        path = scope.get("path", "")
        if (
            not path.startswith("/api/")
            or path.startswith("/api/auth/")
            or method == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        uid = headers.get("x-user-id", "").strip()
        token = headers.get("x-auth-token", "").strip()
        if not uid or not verify_token(uid, token):
            await _send_json(send, 401, {
                "detail": "Unauthorized: invalid or missing X-Auth-Token for X-User-Id",
            })
            return
        await self.app(scope, receive, send)


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})
