# Token 优化补丁包

针对 deepseek 平台 600W token 消耗核查结论,对 `backend/app` 下 4 个文件做精确修改,
自动备份、可一键回滚。

> 背景:本沙箱会话对既有代码目录(`backend/app`)有写保护,无法直接在线修改,
> 因此封装为本地脚本,由你本机运行一次即可生效。

---

## 用法

在 `E:\AgentSuper\backend` 目录下执行:

```bash
# 1. 应用补丁(每个文件修改前自动备份为 *.bak_token_patch)
python token_patch/apply_token_patch.py

# 2. 只检查当前状态(不修改)
python token_patch/apply_token_patch.py --verify

# 3. 回滚(从备份恢复全部文件)
python token_patch/apply_token_patch.py --rollback
```

应用成功后**重启后端服务**(Python 进程需重启,`uvicorn --reload` 则自动生效)。

---

## 改动清单

| 文件 | 改动 | 效果 |
|---|---|---|
| `app/config.py` | `max_steps` 40→24、`max_tool_rounds` 24→16、`max_context_tokens` 64K→48K、工具输出保护/清理阈值 40K/20K→24K/12K | 单请求理论上限从 40×56K≈224 万 token 降至 16×40K≈64 万 |
| `app/agent/sub_tools.py` | 轮数 8→5、工具结果截断 4000→1500 字符、新增 `_trim_messages` 每轮前按"轮"裁剪(保持 tool_call 配对) | 子 Agent 上下文不再无限膨胀,多 agent 请求输入 token 大幅下降 |
| `app/agent/supervisor.py` | LLM 分解重试 2→1;≤24 字符短问题免 LLM 直接路由 rag;kb/code/web 关键词扩充 | 减少"分解"这一步的额外 LLM 调用;更多请求走关键词直路由 |
| `app/api/chat.py` | `MAX_HISTORY_TOKENS` 80K→48K | 普通 chat 历史窗口收紧,每次请求输入变小 |

## 预期收益

- multi-agent 请求:上下文膨胀是最大头,修复后单请求输入可降 **50%+**
- 普通 chat:历史窗口 80K→48K,输入降 ~30%
- 总体 token 消耗预计下降 **40%~60%**(multi-agent 占比越高收益越大)

## 回滚

任何时候执行 `--rollback` 即可恢复原文件(备份为同目录 `.bak_token_patch`)。

## 注意

- 脚本对每个替换做 `count` 校验:匹配数为 0(已应用/版本不同)或 >1(歧义)时跳过并报告,**不会破坏文件**
- 若报告 `[MISS]`,说明你的源码与本补丁基线不同,请把输出发我,我更新补丁
