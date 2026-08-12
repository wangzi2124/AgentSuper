"""轻量用户身份鉴权：X-User-Id 绑定签名 token。

威胁模型：X-User-Id 请求头即身份，无认证时可在局域网中伪造头越权读取他人会话。
本模块提供两类身份（信任模型对齐 opencode device / account）：

1. 设备身份（trust-on-first-use，历史兼容）：
   - 客户端首次注册：生成随机 user_id + device_secret，POST /api/auth/register
   - 服务端只存储 user_id → hmac(secret, device_secret)（不存明文设备密钥）
2. 账号身份（用户名 + 密码登录）：
   - POST /api/auth/account/register 注册用户名/密码（服务端存储 PBKDF2 加盐哈希）
   - POST /api/auth/account/login 校验密码并签发 token

token = base64(uid) . base64(exp) . hmac(secret, "uid:exp:seed")，
seed 为账号的随机 token_seed 或设备的密钥哈希。中间件校验每个 /api/* 请求的
X-User-Id + X-Auth-Token；伪造 uid 无法通过签名校验。

未配置 AUTH_TOKEN_SECRET 时所有校验关闭（保持默认本地部署行为）。
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_users_cache: dict[str, dict] | None = None  # user_id -> 身份记录

# PBKDF2 迭代次数（密码哈希）
_PBKDF2_ITERATIONS = 200_000


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


def _new_uid() -> str:
    return "u-" + uuid.uuid4().hex[:16]


def _new_salt() -> str:
    return os.urandom(16).hex()


def _pbkdf2(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS
    ).hex()


def _load_users() -> dict[str, dict]:
    """加载全部用户记录，并迁移历史明文格式（user_id -> 设备密钥哈希串）。"""
    global _users_cache
    if _users_cache is not None:
        return _users_cache
    path = _users_path()
    if not path.exists():
        _users_cache = {}
        return _users_cache
    migrated = False
    raw: dict = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("auth users load failed: %s", e)
        raw = {}
    users: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            users[str(k)] = v
        elif isinstance(v, str):
            users[str(k)] = {"type": "device", "device_hash": v, "created_at": 0}
            migrated = True
    if migrated:
        _save_users(users)
    _users_cache = users
    return _users_cache


def _save_users(users: dict[str, dict]) -> None:
    path = _users_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("auth users save failed: %s", e)


def _record(user_id: str) -> dict | None:
    return _load_users().get(user_id)


def _token_seed(user_id: str) -> str:
    """token 签名绑定的种子：账号用 token_seed，设备用 device_hash。"""
    rec = _load_users().get(user_id)
    if isinstance(rec, dict):
        return rec.get("token_seed") or rec.get("device_hash") or ""
    return rec if isinstance(rec, str) else ""


def register(user_id: str, device_secret: str) -> tuple[bool, str]:
    """首次注册绑定 user_id 与 device_secret（幂等，设备身份）。

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
        if existing is not None:
            existing_hash = (
                existing.get("device_hash") if isinstance(existing, dict) else existing
            )
            if existing_hash and not hmac.compare_digest(existing_hash, new_hash):
                return False, "user_id 已被其他设备注册"
        users[user_id] = {"type": "device", "device_hash": new_hash, "created_at": int(time.time())}
        _save_users(users)
        _users_cache = users
    return True, ""


def verify_device(user_id: str, device_secret: str) -> bool:
    """校验 device_secret 是否为该 user_id 的注册密钥。"""
    if not enabled() or not user_id or not device_secret:
        return False
    rec = _record(user_id)
    if rec is None:
        return False
    existing_hash = rec.get("device_hash") if isinstance(rec, dict) else rec
    if not isinstance(existing_hash, str) or not existing_hash:
        return False
    return hmac.compare_digest(existing_hash, _secret_hash(device_secret))


def register_account(username: str, password: str) -> tuple[bool, str, str]:
    """注册账号（用户名 + 密码）。返回 (ok, error, user_id)。"""
    username = (username or "").strip()
    if not enabled():
        return False, "身份签名未启用（未配置 AUTH_TOKEN_SECRET）", ""
    if not 3 <= len(username) <= 32:
        return False, "用户名长度需在 3-32 个字符之间", ""
    if not username.replace("_", "").replace("-", "").isalnum():
        return False, "用户名只能包含字母、数字、下划线或连字符", ""
    if len(password) < 6:
        return False, "密码至少 6 位", ""
    global _users_cache
    with _lock:
        users = dict(_load_users())
        if _username_index(users).get(username.lower()):
            return False, "用户名已被注册", ""
        uid = _new_uid()
        salt = _new_salt()
        users[uid] = {
            "type": "account",
            "username": username,
            "pw_salt": salt,
            "pw_hash": _pbkdf2(password, salt),
            "token_seed": os.urandom(24).hex(),
            "created_at": int(time.time()),
        }
        _save_users(users)
        _users_cache = users
    return True, "", uid


def _username_index(users: dict[str, dict]) -> dict[str, str]:
    """username(小写) -> user_id 索引，用于登录与唯一性检查。"""
    idx: dict[str, str] = {}
    for uid, rec in users.items():
        if isinstance(rec, dict) and rec.get("type") == "account":
            name = str(rec.get("username", "")).lower()
            if name:
                idx[name] = uid
    return idx


def authenticate_account(username: str, password: str) -> str:
    """校验用户名密码，成功返回 user_id，失败返回空串。"""
    if not enabled() or not username or not password:
        return ""
    users = _load_users()
    uid = _username_index(users).get(str(username).strip().lower(), "")
    if not uid:
        return ""
    rec = users.get(uid)
    if not isinstance(rec, dict) or rec.get("type") != "account":
        return ""
    stored = rec.get("pw_hash", "")
    salt = rec.get("pw_salt", "")
    if not stored or not salt:
        return ""
    if hmac.compare_digest(stored, _pbkdf2(password, salt)):
        return uid
    return ""


def account_info(user_id: str) -> dict | None:
    """返回用户公开信息（供 /account/me）。"""
    rec = _record(user_id)
    if rec is None:
        return None
    if isinstance(rec, dict) and rec.get("type") == "account":
        return {
            "user_id": user_id,
            "username": str(rec.get("username", user_id)),
            "account_type": "account",
            "created_at": int(rec.get("created_at", 0) or 0),
        }
    return {
        "user_id": user_id,
        "username": user_id,
        "account_type": "device",
        "created_at": int(rec.get("created_at", 0) or 0) if isinstance(rec, dict) else 0,
    }


def issue_token(user_id: str) -> tuple[str, int]:
    """为已注册 user_id 签发签名 token，返回 (token, expires_at_epoch)。"""
    exp = int(time.time()) + settings.auth_token_ttl
    uid_b = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
    exp_b = base64.urlsafe_b64encode(str(exp).encode()).decode().rstrip("=")
    seed = _token_seed(user_id)
    sig = hmac.new(
        settings.auth_secret.encode(), f"{user_id}:{exp}:{seed}".encode(), hashlib.sha256
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
    seed = _token_seed(user_id)
    expected = hmac.new(
        settings.auth_secret.encode(), f"{user_id}:{exp}:{seed}".encode(), hashlib.sha256
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
