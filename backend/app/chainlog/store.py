"""全链路日志的存储层：内存缓冲 + 后台批量落库 + 查询 API。

写入模型
--------
链路埋点散布在 ASGI 中间件、chat 端点、AgentBus 事件循环、子 Agent 协程与 LLM
调用点，若每条都同步 INSERT 会把 DB 延迟直接压进请求耗时。因此：

- `append()` 只把条目塞进有界 `queue.Queue`（O(1)，不碰 DB），立即返回；
- 一个**后台守护线程**按 `chain_log_flush_interval` / `chain_log_flush_batch`
  批量 `executemany` 落库，单次事务提交；
- 队列满时丢弃最旧条目并计数（`dropped()` 暴露给 /api/logs/config 观测），
  保证日志压力永远不会反压业务链路；
- **任何异常都被吞掉并记入 `errors()` 计数** —— 日志系统故障绝不允许影响主流程。

读取模型
--------
`list_entries` / `list_traces` / `trace_detail` / `stats` 供 `app/api/logs.py`
调用，全部走独立连接，不与写入共享事务。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from typing import Any, Iterable, Optional

from app.config import settings
from app.storage import backends

from .db import _get_db, _new_writer_conn

logger = logging.getLogger(__name__)

# 允许入库的日志级别（用于查询参数白名单，防注入到 ORDER BY 之类的位置）
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 链路阶段（前端分组/着色依据；顺序即展示顺序）
STAGES = (
    "http",       # HTTP 请求收发
    "session",    # 会话解析/创建、历史装配
    "routing",    # supervisor 意图识别/分解/路由决策
    "agent",      # 子 Agent 生命周期（bus 派发/回复/错误）
    "tool",       # 工具调用（tool_start/tool_end）
    "llm",        # LLM 调用（模型/token/耗时）
    "permission", # 权限审批
    "persist",    # 消息落库
    "system",     # 启动/维护/其它
)
STAGE_LABELS = {
    "http": "HTTP",
    "session": "会话",
    "routing": "路由",
    "agent": "Agent",
    "tool": "工具",
    "llm": "模型",
    "permission": "权限",
    "persist": "落库",
    "system": "系统",
}

_INSERT_SQL = (
    "INSERT INTO chain_logs (id, trace_id, parent_id, seq, ts, level, stage, component,"
    " event, agent_id, session_id, user_id, path, message, data, duration_ms)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def new_trace_id() -> str:
    return "tr_" + uuid.uuid4().hex[:24]


def new_entry_id() -> str:
    return "lg_" + uuid.uuid4().hex[:24]


def _row_dict(row: Any) -> dict:
    """行 → dict。显式按 keys() 取值，兼容 sqlite3.Row 与 backends.Row。"""
    if row is None:
        return {}
    return {k: row[i] for i, k in enumerate(row.keys())}


def _like(term: str) -> str:
    """把用户输入包成安全的 LIKE 模式（转义 % _ 与转义符）。"""
    escaped = (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


class ChainLogStore:
    """全链路日志存储（进程级单例由 `app.chainlog` 暴露）。"""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=_queue_size())
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._dropped = 0
        self._errors = 0
        self._written = 0
        self._writer_conn: Any = None

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动后台落盘线程（幂等）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="chainlog-writer", daemon=True,
            )
            self._thread.start()

    def stop(self, flush: bool = True) -> None:
        """停止后台线程（可先冲刷残留）。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None
        if flush:
            self._drain_all()
        self._close_writer()

    def _close_writer(self) -> None:
        conn, self._writer_conn = self._writer_conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ── 写入 ──────────────────────────────────────────────────────────────

    def append(self, entry: dict) -> None:
        """入队一条链路日志（非阻塞；失败/满载均静默降级）。"""
        if not _enabled():
            return
        try:
            self.start()
            self._queue.put_nowait(entry)
        except queue.Full:
            # 队列打满：丢最旧的一条腾位置（链路日志可容忍少量丢失，不可阻塞业务）
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(entry)
            except Exception:  # noqa: BLE001
                pass
            self._dropped += 1
        except Exception:  # noqa: BLE001
            self._errors += 1

    def flush(self) -> int:
        """同步冲刷队列（测试/关停用）。返回本次落库条数。"""
        return self._drain(max_batch=10 ** 9)

    def _run(self) -> None:
        """后台落盘循环：按间隔批量取队列并 executemany。"""
        interval = _flush_interval()
        while not self._stop.is_set():
            try:
                self._drain()
            except Exception:  # noqa: BLE001
                self._errors += 1
            self._stop.wait(interval)
        # 退出前冲刷残留
        try:
            self._drain_all()
        except Exception:  # noqa: BLE001
            self._errors += 1
        self._close_writer()

    def _drain_all(self) -> int:
        total = 0
        while True:
            n = self._drain(max_batch=_flush_batch())
            total += n
            if n == 0:
                return total

    def _drain(self, max_batch: Optional[int] = None) -> int:
        """取一批（最多 max_batch 条）并落库。"""
        limit = max_batch or _flush_batch()
        batch: list[dict] = []
        while len(batch) < limit:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return 0
        conn = self._writer_conn
        if conn is None:
            conn = self._writer_conn = _new_writer_conn()
        rows = [tuple(_row_values(e)) for e in batch]
        conn.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                conn.execute(_INSERT_SQL, row)
            conn.commit()
        except Exception:  # noqa: BLE001
            # 逐条重试以定位坏行（如 data 超长）：能插的插，坏的记数丢弃
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            self._insert_individually(conn, rows)
        self._written += len(batch)
        return len(batch)

    def _insert_individually(self, conn: Any, rows: list[tuple]) -> None:
        for row in rows:
            try:
                conn.execute(_INSERT_SQL, row)
                conn.commit()
            except Exception:  # noqa: BLE001
                self._errors += 1
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass

    # ── 统计（运维观测）──────────────────────────────────────────────────

    def stats_counters(self) -> dict:
        return {
            "queued": self._queue.qsize(),
            "queue_size": self._queue.maxsize,
            "dropped": self._dropped,
            "errors": self._errors,
            "written": self._written,
        }

    # ── 查询 ──────────────────────────────────────────────────────────────

    def list_entries(
        self,
        *,
        trace_id: str = "",
        session_id: str = "",
        level: str = "",
        stage: str = "",
        component: str = "",
        agent_id: str = "",
        keyword: str = "",
        since: Optional[int] = None,
        until: Optional[int] = None,
        order: str = "desc",
        offset: int = 0,
        limit: int = 50,
        user_id: str = "",
    ) -> dict:
        """按条件分页查询链路日志条目（默认按时间倒序，新→旧）。"""
        where, params = self._build_filters(
            trace_id=trace_id, session_id=session_id, level=level, stage=stage,
            component=component, agent_id=agent_id, keyword=keyword,
            since=since, until=until, user_id=user_id,
        )
        conn = _get_db()
        try:
            total = _fetch_scalar(
                conn, f"SELECT COUNT(*) FROM chain_logs{where}", params,
            ) or 0
            direction = "ASC" if str(order).lower() == "asc" else "DESC"
            sql = (
                f"SELECT id, trace_id, parent_id, seq, ts, level, stage, component, event,"
                f" agent_id, session_id, user_id, path, message, data, duration_ms"
                f" FROM chain_logs{where} ORDER BY ts {direction}, seq {direction}"
                f" LIMIT {int(limit)} OFFSET {int(offset)}"
            )
            rows = [_decode_entry(_row_dict(r)) for r in conn.execute(sql, params)]
        finally:
            _safe_close(conn)
        return {"entries": rows, "total": int(total), "offset": offset, "limit": limit}

    def list_traces(
        self,
        *,
        session_id: str = "",
        level: str = "",
        stage: str = "",
        component: str = "",
        agent_id: str = "",
        keyword: str = "",
        has_error: bool = False,
        since: Optional[int] = None,
        until: Optional[int] = None,
        offset: int = 0,
        limit: int = 30,
        user_id: str = "",
    ) -> dict:
        """按 trace 聚合分页：一次请求 = 一行摘要（含节点数/错误数/总耗时）。"""
        where, params = self._build_filters(
            trace_id="", session_id=session_id, level=level, stage=stage,
            component=component, agent_id=agent_id, keyword=keyword,
            since=since, until=until, user_id=user_id,
        )
        # has_error 走 HAVING（聚合后过滤），其余条件走 WHERE
        having = ""
        having_expr = ""
        if has_error:
            having = " HAVING errors > 0"
            # 总数查询的 SELECT 列表里没有 errors 别名，必须重复聚合表达式，
            # 否则 `HAVING errors` 在 COUNT 查询上报 "no such column: errors"
            having_expr = " HAVING SUM(CASE WHEN level = 'ERROR' THEN 1 ELSE 0 END) > 0"
        base = f"FROM chain_logs{where}"
        conn = _get_db()
        try:
            total = _fetch_scalar(
                conn,
                f"SELECT COUNT(DISTINCT trace_id) {base}{having_expr}", params,
            ) or 0
            sql = (
                "SELECT trace_id, MIN(ts) AS start_ts, MAX(ts) AS end_ts,"
                " COUNT(*) AS nodes,"
                f" SUM(CASE WHEN level = 'ERROR' THEN 1 ELSE 0 END) AS errors,"
                f" SUM(CASE WHEN level = 'WARNING' THEN 1 ELSE 0 END) AS warnings,"
                " MAX(session_id) AS session_id, MAX(user_id) AS user_id,"
                " MAX(path) AS path"
                # 排序键必须确定：ts 是毫秒级，同一毫秒内的多个 trace 顺序
                # 不稳定会让列表页翻页出现重复/遗漏，故补 end_ts / trace_id 兜底
                f" {base} GROUP BY trace_id{having}"
                f" ORDER BY start_ts DESC, end_ts DESC, trace_id DESC"
                f" LIMIT {int(limit)} OFFSET {int(offset)}"
            )
            rows = [
                _row_dict(r) for r in conn.execute(sql, params)
            ]
            for row in rows:
                row["duration_ms"] = _span_ms(row.get("start_ts"), row.get("end_ts"))
                row["nodes"] = int(row.get("nodes") or 0)
                row["errors"] = int(row.get("errors") or 0)
                row["warnings"] = int(row.get("warnings") or 0)
                row["title"] = self._trace_title(row.get("trace_id", ""))
        finally:
            _safe_close(conn)
        return {"traces": rows, "total": int(total), "offset": offset, "limit": limit}

    def _trace_title(self, trace_id: str) -> str:
        """取 trace 的首个节点作为摘要标题（trace_id 已有索引，逐条查询成本极低）。"""
        if not trace_id:
            return ""
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT message, event, component FROM chain_logs"
                " WHERE trace_id = ? ORDER BY seq ASC LIMIT 1",
                (trace_id,),
            ).fetchone()
        except Exception:  # noqa: BLE001
            return ""
        finally:
            _safe_close(conn)
        if row is None:
            return ""
        d = _row_dict(row)
        return str(d.get("message") or d.get("event") or d.get("component") or "")

    def trace_detail(self, trace_id: str, limit: int = 2000, user_id: str = "") -> dict:
        """返回一条完整链路的所有节点（按 seq 升序，供前端时间线还原）。"""
        sql = (
            "SELECT id, trace_id, parent_id, seq, ts, level, stage, component,"
            " event, agent_id, session_id, user_id, path, message, data,"
            " duration_ms FROM chain_logs WHERE trace_id = ?"
        )
        params: tuple = (trace_id,)
        if user_id:
            sql += " AND (user_id = ? OR user_id = '')"
            params = (trace_id, user_id)
        sql += " ORDER BY seq ASC LIMIT ?"
        conn = _get_db()
        try:
            rows = [
                _decode_entry(_row_dict(r))
                for r in conn.execute(sql, (*params, int(limit)))
            ]
        finally:
            _safe_close(conn)
        start = rows[0]["ts"] if rows else 0
        end = rows[-1]["ts"] if rows else 0
        return {
            "trace_id": trace_id,
            "entries": rows,
            "total": len(rows),
            "start_ts": start,
            "end_ts": end,
            "duration_ms": _span_ms(start, end),
        }

    def stats(
        self,
        *,
        since: Optional[int] = None,
        until: Optional[int] = None,
        session_id: str = "",
        user_id: str = "",
    ) -> dict:
        """聚合统计：总量/级别/阶段/组件分布、平均耗时、错误 Top、慢链路 Top。"""
        where, params = self._build_filters(
            trace_id="", session_id=session_id, level="", stage="", component="",
            agent_id="", keyword="", since=since, until=until, user_id=user_id,
        )
        base = f"FROM chain_logs{where}"
        # by_agent 需要「agent_id 非空」的附加条件（WHERE 与已有条件用 AND 连接）
        agent_base = base + (" AND" if where else " WHERE") + " agent_id <> ''"
        # top_errors 同理，只统计 ERROR/CRITICAL
        err_base = base + (" AND" if where else " WHERE") + " level IN ('ERROR','CRITICAL')"
        conn = _get_db()
        try:
            total = _fetch_scalar(conn, f"SELECT COUNT(*) {base}", params) or 0
            traces = _fetch_scalar(
                conn, f"SELECT COUNT(DISTINCT trace_id) {base}", params,
            ) or 0
            by_level = _group_counts(
                conn, f"SELECT level, COUNT(*) {base} GROUP BY level", params,
            )
            by_stage = _group_counts(
                conn, f"SELECT stage, COUNT(*) {base} GROUP BY stage", params,
            )
            by_component = _group_counts(
                conn, f"SELECT component, COUNT(*) {base} GROUP BY component", params,
            )
            by_agent = _group_counts(
                conn, f"SELECT agent_id, COUNT(*) {agent_base} GROUP BY agent_id", params,
            )
            avg = _fetch_scalar(
                conn,
                f"SELECT AVG(duration_ms) FROM chain_logs{where}"
                + (" AND" if where else " WHERE") + " duration_ms IS NOT NULL",
                params,
            )
            slow = _group_rows(
                conn,
                f"SELECT trace_id, MIN(ts) AS start_ts, MAX(ts) AS end_ts,"
                f" COUNT(*) AS nodes {base} GROUP BY trace_id"
                " ORDER BY (MAX(ts) - MIN(ts)) DESC LIMIT 10",
                params,
            )
            for row in slow:
                row["duration_ms"] = _span_ms(row.get("start_ts"), row.get("end_ts"))
                row["nodes"] = int(row.get("nodes") or 0)
            top_errors = _group_rows(
                conn,
                f"SELECT event, component, COUNT(*) AS n {err_base}"
                " GROUP BY event, component ORDER BY n DESC LIMIT 10",
                params,
            )
        finally:
            _safe_close(conn)
        return {
            "total": int(total),
            "traces": int(traces),
            "by_level": by_level,
            "by_stage": by_stage,
            "by_component": by_component,
            "by_agent": by_agent,
            "avg_duration_ms": round(float(avg), 1) if avg else 0.0,
            "slow_traces": slow,
            "top_errors": top_errors,
            "counters": self.stats_counters(),
        }

    # ── 清理 ──────────────────────────────────────────────────────────────

    def cleanup(self) -> dict:
        """按 TTL 与行数上限裁剪历史日志。返回各类删除条数。"""
        result = {"expired": 0, "overflow": 0}
        conn = _get_db()
        try:
            days = _retention_days()
            if days > 0:
                cutoff = int((time.time() - days * 86400) * 1000)
                cur = conn.execute("DELETE FROM chain_logs WHERE ts < ?", (cutoff,))
                result["expired"] = _rowcount(cur)
                conn.commit()
            max_rows = _max_rows()
            if max_rows > 0:
                # 保留最新 max_rows 行。不能用「第 N+1 行的 ts 作水位」——
                # ts 是毫秒级，工具密集的一轮会在同一毫秒写几十上百行，
                # 水位等于最小 ts 时一条都删不掉（行数上限形同虚设）。
                # 改为按 (ts, id) 排序取保留集，NOT IN 反选删除；派生表包一层
                # 以兼容 MySQL（IN 子句里不能直接带 LIMIT）。
                cur = conn.execute(
                    "DELETE FROM chain_logs WHERE id NOT IN ("
                    "  SELECT id FROM ("
                    f"    SELECT id FROM chain_logs ORDER BY ts DESC, id DESC LIMIT {int(max_rows)}"
                    "  ) AS keep_recent"
                    ")",
                )
                result["overflow"] = _rowcount(cur)
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("chain log cleanup failed: %s", e)
        finally:
            _safe_close(conn)
        return result

    def clear(
        self, *, trace_id: str = "", before: Optional[int] = None, all_rows: bool = False,
    ) -> dict:
        """按条件删除链路日志（管理端操作）。"""
        deleted = 0
        conn = _get_db()
        try:
            if all_rows:
                cur = conn.execute("DELETE FROM chain_logs")
            elif trace_id:
                cur = conn.execute(
                    "DELETE FROM chain_logs WHERE trace_id = ?", (trace_id,),
                )
            elif before is not None:
                cur = conn.execute("DELETE FROM chain_logs WHERE ts < ?", (int(before),))
            else:
                return {"deleted": 0}
            deleted = _rowcount(cur)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("chain log clear failed: %s", e)
        finally:
            _safe_close(conn)
        return {"deleted": deleted}

    def distinct_values(self, user_id: str = "") -> dict:
        """返回可用于前端筛选下拉的取值集合（组件/阶段/级别 + 最近会话）。"""
        scope = " AND (user_id = ? OR user_id = '')" if user_id else ""
        p: tuple = (user_id,) if user_id else ()
        conn = _get_db()
        try:
            components = _group_counts(
                conn,
                "SELECT component, COUNT(*) AS n FROM chain_logs"
                f" WHERE component <> ''{scope} GROUP BY component ORDER BY n DESC LIMIT 50",
                p,
            )
            agents = _group_counts(
                conn,
                "SELECT agent_id, COUNT(*) AS n FROM chain_logs"
                f" WHERE agent_id <> ''{scope} GROUP BY agent_id ORDER BY n DESC LIMIT 30",
                p,
            )
            sessions = _group_counts(
                conn,
                "SELECT session_id, COUNT(*) AS n FROM chain_logs"
                f" WHERE session_id <> ''{scope} GROUP BY session_id ORDER BY n DESC LIMIT 30",
                p,
            )
        except Exception:  # noqa: BLE001
            return {"components": [], "agents": [], "sessions": [], "stages": list(STAGES)}
        finally:
            _safe_close(conn)
        return {
            "components": list(components.keys()),
            "agents": list(agents.keys()),
            "sessions": list(sessions.keys()),
            "stages": list(STAGES),
            "levels": list(LEVELS),
        }

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _build_filters(
        self,
        *,
        trace_id: str,
        session_id: str,
        level: str,
        stage: str,
        component: str,
        agent_id: str,
        keyword: str,
        since: Optional[int],
        until: Optional[int],
        user_id: str = "",
    ) -> tuple[str, tuple]:
        """构造 WHERE 子句 + 参数（全部走占位符，值域白名单化）。

        `user_id` 非空时做数据隔离：**下推到 SQL**（而不是查询后在 Python 里
        过滤）—— 否则分页 total 与聚合统计会按全库算，非管理员既能翻到别人的
        链路，也能通过 total/slow_traces 推断出别人的活动量。
        `user_id = ''` 的行是系统级日志（启动、维护），对所有人可见。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if level and level.upper() in LEVELS:
            clauses.append("level = ?")
            params.append(level.upper())
        if stage and stage in STAGES:
            clauses.append("stage = ?")
            params.append(stage)
        if component:
            clauses.append("component = ?")
            params.append(component)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(int(since))
        if until is not None:
            clauses.append("ts <= ?")
            params.append(int(until))
        if keyword:
            pattern = _like(keyword)
            clauses.append(
                "(message LIKE ? ESCAPE '\\' OR event LIKE ? ESCAPE '\\'"
                " OR component LIKE ? ESCAPE '\\' OR data LIKE ? ESCAPE '\\'"
                " OR path LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 5)
        if user_id:
            clauses.append("(user_id = ? OR user_id = '')")
            params.append(user_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, tuple(params)


# ── 行/值编解码 ────────────────────────────────────────────────────────────

_ROW_ORDER = (
    "id", "trace_id", "parent_id", "seq", "ts", "level", "stage", "component",
    "event", "agent_id", "session_id", "user_id", "path", "message", "data",
    "duration_ms",
)


def _dump_data(data) -> str:
    """data 序列化为 JSON 文本，并在超长时**只裁剪字符串值**。

    不能直接对已序列化的文本做 `text[:max]` —— 那会把 JSON 从中间切断，
    反序列化失败后 `_decode_entry` 只能退化成 `{"_raw": ...}`，等于把
    answer_chars / duration / tool_args 这类**结构化关键字段全丢了**。
    这里改为保留所有标量、压缩长字符串，保证产出的仍是合法 JSON。
    """
    if data is None or data == {}:
        return ""
    if isinstance(data, str):
        text = data
    else:
        try:
            text = json.dumps(data, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            text = str(data)
    max_chars = _max_data_chars()
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    if not isinstance(data, dict):
        return text[:max_chars] + f'…" [truncated {len(text)} chars]"'

    # 长字符串按预算均摊裁剪；仍超长则逐步收紧，最后退化为保留标量的精简对象
    longs = [v for v in data.values() if isinstance(v, str) and len(v) > 32]
    for share in (0.5, 0.25, 0.1, 0.0):
        per = int(max_chars * share) // max(len(longs), 1)
        reduced = {}
        for key, value in data.items():
            if isinstance(value, str) and len(value) > 32:
                reduced[key] = value[:max(per, 32)] + "…"
            else:
                reduced[key] = value
        reduced["_truncated"] = True
        try:
            out = json.dumps(reduced, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            continue
        if len(out) <= max_chars:
            return out
    scalars = {k: v for k, v in data.items() if isinstance(v, (int, float, bool)) or v is None}
    scalars["_truncated"] = True
    try:
        return json.dumps(scalars, ensure_ascii=False, default=str)[:max_chars]
    except Exception:  # noqa: BLE001
        return text[:max_chars]


def _row_values(entry: dict) -> list:
    """按 `_ROW_ORDER` 取出待入库值（缺失补默认值，data 统一序列化）。"""
    return [
        entry.get("id") or new_entry_id(),
        entry.get("trace_id") or new_trace_id(),
        entry.get("parent_id") or "",
        int(entry.get("seq") or 0),
        int(entry.get("ts") or time.time() * 1000),
        (entry.get("level") or "INFO").upper(),
        entry.get("stage") or "system",
        entry.get("component") or "",
        entry.get("event") or "",
        entry.get("agent_id") or "",
        entry.get("session_id") or "",
        entry.get("user_id") or "",
        (entry.get("path") or "")[:512],
        entry.get("message") or "",
        _dump_data(entry.get("data")),
        entry.get("duration_ms"),
    ]


def _decode_entry(row: dict) -> dict:
    """行 → 前端友好结构（data 反序列化为对象，时间戳补毫秒/秒双字段）。"""
    out = dict(row)
    raw = out.get("data") or ""
    if isinstance(raw, str) and raw:
        try:
            out["data"] = json.loads(raw)
        except Exception:  # noqa: BLE001
            out["data"] = {"_raw": raw[:2000]}
    else:
        out["data"] = {}
    ts = int(out.get("ts") or 0)
    out["ts"] = ts
    out["time"] = ts / 1000.0
    return out


# ── 模块级小工具 ──────────────────────────────────────────────────────────

def _enabled() -> bool:
    try:
        return bool(settings.chain_log_enabled)
    except Exception:  # noqa: BLE001
        return False


def _queue_size() -> int:
    try:
        return max(100, int(settings.chain_log_queue_size))
    except Exception:  # noqa: BLE001
        return 5000


def _flush_interval() -> float:
    try:
        return max(0.05, float(settings.chain_log_flush_interval))
    except Exception:  # noqa: BLE001
        return 1.0


def _flush_batch() -> int:
    try:
        return max(1, int(settings.chain_log_flush_batch))
    except Exception:  # noqa: BLE001
        return 200


def _retention_days() -> int:
    try:
        return int(settings.chain_log_retention_days)
    except Exception:  # noqa: BLE001
        return 7


def _max_rows() -> int:
    try:
        return int(settings.chain_log_max_rows)
    except Exception:  # noqa: BLE001
        return 200000


def _max_data_chars() -> int:
    try:
        return int(settings.chain_log_max_data_chars)
    except Exception:  # noqa: BLE001
        return 4000


def _span_ms(start: Any, end: Any) -> float:
    try:
        return round(max(0.0, float(end or 0) - float(start or 0)), 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _fetch_scalar(conn: Any, sql: str, params: Iterable) -> Any:
    row = conn.execute(sql, tuple(params)).fetchone()
    return row[0] if row is not None else None


def _rowcount(cur: Any) -> int:
    try:
        return max(0, int(cur.rowcount))
    except Exception:  # noqa: BLE001
        return 0


def _group_counts(conn: Any, sql: str, params: Iterable) -> dict:
    out: dict[str, int] = {}
    for row in conn.execute(sql, tuple(params)):
        d = _row_dict(row)
        key = str(d.get(list(d.keys())[0]) or "")
        out[key] = int(d.get(list(d.keys())[1]) or 0)
    return out


def _group_rows(conn: Any, sql: str, params: Iterable) -> list[dict]:
    return [_row_dict(r) for r in conn.execute(sql, tuple(params))]


def _safe_close(conn: Any) -> None:
    """非 sqlite 后端门面每次调用都要关；sqlite 线程内连接复用不关。"""
    if conn is None or backends.is_sqlite():
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
