"""中性工具层（无业务依赖）：SSRF 校验 / 模型下载 / JSON 修复等。

从 `app.utils` 迁出，供新核心（graph/execution/cognition/models/observability/prompts）
直接使用；`app.utils.*` 保留为再导出 shim。
"""
