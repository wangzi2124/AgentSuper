"""Windows Job Object 封装（设计 §5.1，仅 win32 生效；ctypes 直调 Win32 API）。

- 创建 Job → 正常 spawn 子进程 → AssignProcessToJobObject（assign-after-spawn，v2.2）。
  早期 v2.1 方案「CREATE_SUSPENDED 启动 → assign → ResumeThread」已放弃：PopenProcess
  句柄不等于主线程句柄，ResumeThread 会报 ERROR_INVALID_HANDLE（见 session.py `_spawn`）。
- KILL_ON_JOB_CLOSE：宿主进程/句柄释放时保证子进程全灭。
- 杀树优先 TerminateJobObject（比 taskkill /T /F 更彻底）。
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Job Object 限制标志
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_CPU_RATE_CONTROL = 0x00040000

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", wintypes.ULARGE_INTEGER),
        ("WriteOperationCount", wintypes.ULARGE_INTEGER),
        ("OtherOperationCount", wintypes.ULARGE_INTEGER),
        ("ReadTransferCount", wintypes.ULARGE_INTEGER),
        ("WriteTransferCount", wintypes.ULARGE_INTEGER),
        ("OtherTransferCount", wintypes.ULARGE_INTEGER),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _winapi():
    return {
        "CreateJobObjectW": ctypes.windll.kernel32.CreateJobObjectW,
        "SetInformationJobObject": ctypes.windll.kernel32.SetInformationJobObject,
        "AssignProcessToJobObject": ctypes.windll.kernel32.AssignProcessToJobObject,
        "TerminateJobObject": ctypes.windll.kernel32.TerminateJobObject,
        "CloseHandle": ctypes.windll.kernel32.CloseHandle,
        "GetLastError": ctypes.windll.kernel32.GetLastError,
    }


class JobObject:
    """Windows Job Object（非 win32 时 create() 返回 None，调用方 fallback taskkill）。"""

    def __init__(self, handle: int, max_memory_mb: int, max_processes: int):
        self.handle = handle
        self._max_memory_mb = max_memory_mb
        self._max_processes = max_processes

    @classmethod
    def create(cls, max_memory_mb: int = 512, max_processes: int = 16) -> "JobObject | None":
        """创建并配置 Job Object；失败返回 None（调用方 fallback 到 taskkill /T /F）。"""
        if os.name != "nt":
            return None
        if max_memory_mb <= 0:
            return None
        api = _winapi()
        job = api["CreateJobObjectW"](None, None)
        if not job:
            logger.warning("codeact: CreateJobObjectW failed (err=%d)", api["GetLastError"]() & 0xFFFFFFFF)
            return None
        try:
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | JOB_OBJECT_LIMIT_JOB_MEMORY
                | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            info.ProcessMemoryLimit = max_memory_mb * 1024 * 1024
            info.JobMemoryLimit = max(2, max_memory_mb * 2) * 1024 * 1024
            info.BasicLimitInformation.ActiveProcessLimit = max(1, max_processes)
            ok = api["SetInformationJobObject"](
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                logger.warning(
                    "codeact: SetInformationJobObject failed (err=%d); "
                    "falling back to taskkill /T /F", api["GetLastError"]() & 0xFFFFFFFF,
                )
                api["CloseHandle"](job)
                return None
            return cls(job, max_memory_mb, max_processes)
        except Exception as e:  # noqa: BLE001
            logger.warning("codeact: Job Object setup failed: %s", e)
            try:
                api["CloseHandle"](job)
            except Exception:  # noqa: BLE001
                pass
            return None

    def assign(self, process_handle: int) -> bool:
        """把已启动的进程挂进 Job（assign-after-spawn）。"""
        if os.name != "nt" or not self.handle:
            return False
        api = _winapi()
        ok = api["AssignProcessToJobObject"](self.handle, process_handle)
        if not ok:
            logger.warning(
                "codeact: AssignProcessToJobObject failed (err=%d); "
                "breaking out of parent job may be unsupported → fallback taskkill",
                api["GetLastError"]() & 0xFFFFFFFF,
            )
        return bool(ok)

    def terminate(self, exit_code: int = 1) -> bool:
        """TerminateJobObject：杀 Job 内全进程树。"""
        if os.name != "nt" or not self.handle:
            return False
        api = _winapi()
        ok = api["TerminateJobObject"](self.handle, exit_code)
        if not ok:
            logger.warning("codeact: TerminateJobObject failed (err=%d)", api["GetLastError"]() & 0xFFFFFFFF)
        return bool(ok)

    def close(self) -> None:
        if os.name != "nt" or not self.handle:
            return
        try:
            _winapi()["CloseHandle"](self.handle)
        except Exception:  # noqa: BLE001
            pass
        self.handle = 0
        # KILL_ON_JOB_CLOSE：句柄关闭即杀剩余子进程（兜底）。
