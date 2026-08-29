# -*- coding: utf-8 -*-
"""
同步脚本：更新 docs/优化行动计划.md 的 F1/F2/F3 排期状态注释，
以及 README.md 的「消息滚动」描述（回到底部按钮 + isTrusted 用户手势优先）。

安全策略：
- 每个 (old, new) 替换必须且仅命中 1 次，否则中止并抛出错误（不写回）。
- 全部校验通过后才写回文件，避免半更新状态。
幂等：重复执行时 old 文本已不存在，会报错中止（即不允许重复执行，需人工确认）。
"""
import io
import sys

BASE = r"E:\AgentSuper"
FILES = {
    "优化行动计划": {
        "path": BASE + r"\docs\优化行动计划.md",
        "replacements": [
            # 1) 2.4 表格头：增加「排期状态」列（锚定到紧随的 F1 行，2.1~2.6 同名表头有 6 处）
            (
                "| # | 具体问题 | 风险 | 优先级 |\n|---|---|---|---|\n| F1 | 聊天优化清单大量未实施（B4 重试幂等、B6 路由关键词、B10 统一历史管线、S2-S8、A1-A5、F4/F8-F10） | 功能缺口 | P1 |",
                "| # | 具体问题 | 风险 | 优先级 | 排期状态 |\n|---|---|---|---|---|\n| F1 | 聊天优化清单大量未实施（B4 重试幂等、B6 路由关键词、B10 统一历史管线、S2-S8、A1-A5、F4/F8-F10） | 功能缺口 | P1 |",
            ),
            # 2) F1 行：标注已排期
            (
                "| F1 | 聊天优化清单大量未实施（B4 重试幂等、B6 路由关键词、B10 统一历史管线、S2-S8、A1-A5、F4/F8-F10） | 功能缺口 | P1 |",
                "| F1 | 聊天优化清单大量未实施（B4 重试幂等、B6 路由关键词、B10 统一历史管线、S2-S8、A1-A5、F4/F8-F10） | 功能缺口 | P1 | ⏳ 已排期（4.2 第二批「聊天链路补全」） |",
            ),
            # 3) F2 行：标注已排期
            (
                "| F2 | `message.part.delta` 真增量未实现 | 流式体验差 | P2 |",
                "| F2 | `message.part.delta` 真增量未实现 | 流式体验差 | P2 | ⏳ 已排期（4.3 第三批「消息模型补全」） |",
            ),
            # 4) F3 行：标注已排期
            (
                "| F3 | 多 Agent 子会话的 parts 未落库 | 历史不完整 | P2 |",
                "| F3 | 多 Agent 子会话的 parts 未落库 | 历史不完整 | P2 | ⏳ 已排期（4.3 第三批「消息模型补全」） |",
            ),
            # 5) 4.2 聊天链路补全行：标注未实施
            (
                "| 聊天链路补全 | B4 后端去重（client_msg_id）、B10 统一历史管线、B11 断连落库；前端 S2~S8 SSE 增强 | 断连重连不丢消息 | F1 |",
                "| 聊天链路补全 | B4 后端去重（client_msg_id）、B10 统一历史管线、B11 断连落库；前端 S2~S8 SSE 增强 | 断连重连不丢消息 | F1（⏳ 未实施） |",
            ),
            # 6) 4.3 消息模型补全行：标注未实施
            (
                "| 消息模型补全 | 多 Agent parts 落库、reasoning part、`message.part.delta` 真增量 | 流式按增量渲染、历史完整 | F2/F3 |",
                "| 消息模型补全 | 多 Agent parts 落库、reasoning part、`message.part.delta` 真增量 | 流式按增量渲染、历史完整 | F2/F3（⏳ 未实施） |",
            ),
        ],
    },
    "README": {
        "path": BASE + r"\README.md",
        "replacements": [
            # 7) L37 消息滚动描述：补充回到底部按钮 + isTrusted 用户手势优先
            (
                "| **消息滚动** | 聊天消息列表智能自动滚动（靠近底部才跟随），用户上翻浏览时不强制滚动 |",
                "| **消息滚动** | 聊天消息列表智能自动滚动（靠近底部才跟随，`isTrusted` 用户手势优先，用户上翻不被打断）；上翻浏览时右下角浮现「回到底部」悬浮按钮，点击平滑回底并恢复自动跟随 |",
            ),
        ],
    },
}


def apply_replacements(path: str, replacements: list) -> int:
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()

    applied = 0
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                "ABORT: 替换目标命中 %d 次（期望 1 次），文件未写回：%s\n  old=%r"
                % (count, path, old)
            )
        text = text.replace(old, new)
        applied += 1

    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return applied


def main():
    for name, cfg in FILES.items():
        n = apply_replacements(cfg["path"], cfg["replacements"])
        print("[OK] %s: 完成 %d 处替换 -> %s" % (name, n, cfg["path"]))
    print("全部完成。")


if __name__ == "__main__":
    main()
