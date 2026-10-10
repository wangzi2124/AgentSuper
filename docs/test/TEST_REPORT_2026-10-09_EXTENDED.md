# 后端测试执行报告（扩展集）

**日期**: 2026-10-09（第二轮）
**范围**: write_verification + zero_progress_rescue + truncated_tool_args + output_budget + model_catalog

## 总体

`
72 passed, 25 failed, 0 skipped
`

**核心稳定项（全通过）**
- test_write_verification: 17/17 通过（写入校验、读回、语法平衡稳定）

## 聚焦失败（按文件）

### test_truncated_tool_args (3/6 失败)
- generate_blocks_tool_execution_when_args_truncated
- generate_error_message_reaches_the_model
- generate_normal_length_tool_calls_still_execute
（gating 源码通过但执行分支行为与预期不符）

### test_zero_progress_rescue (2/29 失败)
- test_plain_empty_answer_still_goes_to_empty_answer_retry
- test_oversized_prose_still_requires_length_finish_reason
（oversized prose 判定时机/零进度 rescue 优先级）

### test_output_budget (1/11 失败)
- test_context_budget_modules_see_the_new_budget
（预算模块传播时机/导入路径相关）

### test_model_catalog (19/34 失败)
- 能力判定/预算函数/llm_call max_tokens/friendly_chat_error 等多处偏差

## 结论

写校验模块稳定。主要偏差集中在：graphmod 生成流程的 rescue/oversized_prose、truncated_args 执行阻断路径、model_catalog 能力与预算解析。建议按失败清单（docs/test/failed_list_extended_2026-10-09.txt）逐项修复。

