# 后端测试执行报告

**日期**: 2026-10-09
**测试范围**: 
- tests/test_write_verification.py (语法校验 + 写入校验)
- tests/test_zero_progress_rescue.py (零进度救援 + 超长散文处理)
- tests/test_truncated_tool_args.py (工具参数截断防护)

**环境**: Python 3.13.2, Windows (win32)

## 总体结果

`
34 passed, 18 failed, 0 skipped
`

**失败占比**: ~35% (18/52)

## 1. test_write_verification.py (17/17 通过)

语法/读回校验机制健全：
- JS/Python/JSON/CSS/HTML 均衡校验正常
- 分块 append 中间态仅警告、不误判
- 读回校验可容忍 CRLF 差异、能检测截断
- 坏 JS 禁止谎报完成

## 2. test_truncated_tool_args.py (2/6 通过)

**通过**
- test_parse_tool_args_would_have_accepted_truncated_json
- test_generate_source_gates_truncated_args_on_length_finish_reason

**失败** (4)
- test_truncated_args_message_is_actionable
- test_generate_blocks_tool_execution_when_args_truncated
- test_generate_error_message_reaches_the_model
- test_generate_normal_length_tool_calls_still_execute

> 涉及 graphmod/generate 分支：当 finish_reason=length 且参数被截断时，阻止工具执行并将可操作错误回灌给模型的路径未完全符合预期。

## 3. test_zero_progress_rescue.py (15/29 通过)

**主要通过**
- 零进度判定（空 content/reasoning 回退/工具调用）
- rescue 预算边界、掩码为诊断而非独白
- round 文件登记、cur_tool_calls 规范化
- 部分完成广播等基础路径

**失败** (14)
- test_zero_progress_reasoning_size_threshold
- test_oversized_prose_detected_when_truncated_without_tools
- test_oversized_prose_not_triggered_when_tools_called
- test_oversized_prose_requires_length_finish_reason
- test_oversized_prose_below_threshold_ignored
- test_generate_injects_oversized_prose_nudge_and_continues
- test_generate_shortcut_ignores_reasoning_fallback_content
- test_zero_progress_rescue_ignores_finish_reason
- test_plain_empty_answer_still_goes_to_empty_answer_retry
- test_oversized_prose_still_requires_length_finish_reason

> 与“超长散文（oversized prose）检测 + 零进度 rescue”相关的判定时机/注入逻辑与最新实现存在偏差。

## 建议

1. 按失败分组优先排查 zero_progress_rescue 的 oversized_prose 判定阈值与触发条件（finish_reason/是否有 tool_calls/threshold）。
2. 排查 	runcated_tool_args 在 generate 流程中的 gating 时序（generate_source_gates_* 测试已通过，说明源文件 gating 正确，但执行分支未按预期阻断/回灌）。
3. 先修复判定条件（constants/guards），再跑失败集逐个验证。

