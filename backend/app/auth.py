"""轻量用户身份鉴权：X-User-Id 绑定 JWT。

威胁模型：X-User-Id 请求头即身份，无认证时可在局域网中伪造头越权读取他人会话。
本模块提供两类身份（信任模型对齐 opencode device / account）：

1. 设备身份（trust-on-first-use，历史兼容）：
   - 客户端首次注册：生成随机 user_id + device_secret，POST /api/auth/register
   - 服务端只存储 user_id → hmac(secret, device_secret)（不存明文设备密钥）
2. 账号身份（用户名 + 密码登录）：
   - POST /api/auth/account/register 注册用户名/密码（服务端存储 PBKDF2 加盐哈希）
   - POST /api/auth/account/login 校验密码并签发 token

签发的 token 为标准 JWT（HS256，RFC 7519），claims 含 sub(user_id)/exp/iat；
伪造 uid 无法通过签名校验。中间件校验每个 /api/* 请求的 X-User-Id + X-Auth-Token。

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

from config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_legacy_import_lock = threading.Lock()
_legacy_import_checked = False

# PBKDF2 迭代次数（密码哈希）
_PBKDF2_ITERATIONS = 200_000

# JWT
_JWT_HEADER = {"alg": "HS256", "typ": "JWT"}
_JWT_ISSUER = "agent-super"


def enabled() -> bool:
    """是否启用身份签名校验（设置了 AUTH_TOKEN_SECRET）。"""
    return bool(settings.auth_secret)


def _legacy_users_path() -> Path:
    """旧 JSON 用户文件路径（仅用于一次性导入，导入成功后删除）。"""
    from storage.paths import data_path

    return data_path(settings.auth_users_path, "auth_users.json")


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


# ── JWT 编解码（HS256，stdlib 实现，无第三方依赖）───────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _jwt_claims(user_id: str, exp: int) -> dict:
    return {
        "iss": _JWT_ISSUER,
        "sub": user_id,
        "iat": int(time.time()),
        "exp": exp,
    }


def issue_token(user_id: str) -> tuple[str, int]:
    """为已注册 user_id 签发 JWT，返回 (token, expires_at_epoch)。"""
    exp = int(time.time()) + settings.auth_token_ttl
    header = _b64url(json.dumps(_JWT_HEADER, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(_jwt_claims(user_id, exp), separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}"
    sig = hmac.new(
        settings.auth_secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(sig)}", exp


def verify_token(user_id: str, token: str) -> bool:
    """校验 JWT 是否绑定 user_id 且未过期（签名 + exp + sub）。"""
    if not enabled() or not token:
        return False
    try:
        header, payload, sig_b = token.split(".")
        # 1) 校验签名（防伪造）
        expected = hmac.new(
            settings.auth_secret.encode(), f"{header}.{payload}".encode(),
            hashlib.sha256,
        ).digest()
        actual = _b64url_decode(sig_b)
        if not hmac.compare_digest(expected, actual):
            return False
        # 2) 解析 payload
        claims = json.loads(_b64url_decode(payload).decode())
        # 3) 校验过期时间
        if int(claims.get("exp", 0)) < time.time():
            return False
        # 4) 校验主题（绑定 X-User-Id）
        if str(claims.get("sub", "")) != user_id:
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def _db():
    """会话库连接（auth_users 表与 session.db 同库，支持 sqlite/mysql/postgresql）。"""
    from app.session.db import _get_db

    return _get_db()


def _row_to_record(row) -> dict:
    rec: dict = {"type": str(row["type"] or "device"), "created_at": int(row["created_at"] or 0)}
    for key in ("username", "device_hash", "pw_salt", "pw_hash", "token_seed"):
        value = row[key]
        if value is not None:
            rec[key] = value
    return rec


def _normalize_legacy(raw: dict) -> dict[str, dict]:
    users: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            users[str(k)] = v
        elif isinstance(v, str):
            users[str(k)] = {"type": "device", "device_hash": v, "created_at": 0}
    return users


def _ensure_legacy_imported() -> None:
    """把旧 auth_users.json 一次性导入 DB；成功后删除 JSON，之后只读写数据库。"""
    global _legacy_import_checked
    if _legacy_import_checked:
        return
    with _legacy_import_lock:
        if _legacy_import_checked:
            return
        _legacy_import_checked = True
        path = _legacy_users_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("legacy auth users load failed: %s", e)
            return
        if not isinstance(raw, dict):
            return
        users = _normalize_legacy(raw)
        if not users:
            try:
                path.unlink()
            except OSError:
                pass
            return
        conn = _db()
        try:
            rows = conn.execute("SELECT id, username_lower FROM auth_users").fetchall()
            existing_ids = {str(r["id"]) for r in rows}
            existing_names = {
                str(r["username_lower"]) for r in rows if r["username_lower"]
            }
            for uid, rec in users.items():
                if uid in existing_ids:
                    continue
                username = rec.get("username")
                lower = str(username).lower() if username else None
                if lower and lower in existing_names:
                    continue
                conn.execute(
                    "INSERT INTO auth_users"
                    " (id, type, username, username_lower, device_hash, pw_salt,"
                    " pw_hash, token_seed, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        uid,
                        str(rec.get("type") or "device"),
                        username,
                        lower,
                        rec.get("device_hash"),
                        rec.get("pw_salt"),
                        rec.get("pw_hash"),
                        rec.get("token_seed"),
                        int(rec.get("created_at") or 0),
                    ),
                )
                if lower:
                    existing_names.add(lower)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("legacy auth users import failed: %s", e)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return
        finally:
            conn.close()
        try:
            path.unlink()
            logger.info("auth users migrated to database, removed %s", path)
        except Exception as e:  # noqa: BLE001
            logger.warning("legacy auth users cleanup failed: %s", e)


def _record(user_id: str) -> dict | None:
    _ensure_legacy_imported()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_record(row) if row is not None else None
    finally:
        conn.close()


def register(user_id: str, device_secret: str) -> tuple[bool, str]:
    """首次注册绑定 user_id 与 device_secret（幂等，设备身份）。

    已存在且密钥不符时拒绝，防止冒充已注册用户。返回 (ok, error)。
    """
    if not user_id or not device_secret:
        return False, "user_id 与 device_secret 不能为空"
    if not enabled():
        return False, "身份签名未启用（未配置 AUTH_TOKEN_SECRET）"
    new_hash = _secret_hash(device_secret)
    _ensure_legacy_imported()
    with _lock:
        existing = _record(user_id)
        if existing is not None:
            if existing.get("type") == "account":
                return False, "user_id 已被账号注册"
            existing_hash = existing.get("device_hash")
            if existing_hash and not hmac.compare_digest(existing_hash, new_hash):
                return False, "user_id 已被其他设备注册"
        conn = _db()
        try:
            if existing is None:
                conn.execute(
                    "INSERT INTO auth_users (id, type, device_hash, created_at)"
                    " VALUES (?,?,?,?)",
                    (user_id, "device", new_hash, int(time.time())),
                )
            else:
                conn.execute(
                    "UPDATE auth_users SET type = 'device', device_hash = ?,"
                    " created_at = ? WHERE id = ?",
                    (new_hash, int(time.time()), user_id),
                )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("auth device register failed: %s", e)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return False, "user_id 已被其他设备注册"
        finally:
            conn.close()
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
    lower = username.lower()
    uid = _new_uid()
    salt = _new_salt()
    _ensure_legacy_imported()
    with _lock:
        conn = _db()
        try:
            existing = conn.execute(
                "SELECT id FROM auth_users WHERE username_lower = ?", (lower,)
            ).fetchone()
            if existing is not None:
                return False, "用户名已被注册", ""
            conn.execute(
                "INSERT INTO auth_users"
                " (id, type, username, username_lower, pw_salt, pw_hash,"
                " token_seed, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    uid,
                    "account",
                    username,
                    lower,
                    salt,
                    _pbkdf2(password, salt),
                    os.urandom(24).hex(),
                    int(time.time()),
                ),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("auth account register failed: %s", e)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return False, "用户名已被注册", ""
        finally:
            conn.close()
    return True, "", uid


def authenticate_account(username: str, password: str) -> str:
    """校验用户名密码，成功返回 user_id，失败返回空串。"""
    if not enabled() or not username or not password:
        return ""
    _ensure_legacy_imported()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM auth_users WHERE username_lower = ?",
            (str(username).strip().lower(),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return ""
    rec = _row_to_record(row)
    if rec.get("type") != "account":
        return ""
    stored = rec.get("pw_hash", "")
    salt = rec.get("pw_salt", "")
    if not stored or not salt:
        return ""
    if hmac.compare_digest(stored, _pbkdf2(password, salt)):
        return str(row["id"])
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


class AuthMiddleware:
    """校验每个 /api/* 请求的 X-User-Id + X-Auth-Token。

    仅当配置了 AUTH_TOKEN_SECRET 时生效。放行：
      - 非 /api/* 路径（文档、健康检查等）
      - /api/auth/*（注册 / 令牌 / 状态）
      - GET /api/voice/status（只读语音可用性探测，前端免登录判断
        「显示麦克风/朗读」按钮；transcribe/tts 仍要求鉴权）
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
            or (path == "/api/voice/status" and method == "GET")
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
                "code": 401,
                "message": "未登录或会话已过期，请重新登录",
                "data": None,
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
