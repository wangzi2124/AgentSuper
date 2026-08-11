# Agent 工具全量目录（AGENT TOOL CATALOG）

> 生成时间：2026-08-11 ｜ 数据来源：代码实扫（`app/agent/graph.py`、`app/agent/tools.py`、`app/agent/sub_tools.py`、`app/skills/`、`app/plugins/`、`plugins/`、`skills/`、`data/pinned_tools.json`）
> 本文档为当前运行态快照。技能/插件可在前端「Skills」「Plugins」页面启停，启停后工具集随之变化。

---

## 0. 总览

| 类别 | 数量 | 说明 |
|---|---|---|
| 内置文件系统工具（常驻） | 10 | `tool_*` 前缀，始终挂载 schema |
| 技能加载工具 | 39 | `load_skill_*`，按需挂载（v5 意图匹配） |
| 插件工具 | 21 | `plugin_<plugin>_tool_*`，按需挂载 |
| 固定（pin）工具 | 1 | `load_skill_code_review`（前端固定，schema 始终挂载） |
| **主 Agent 工具合计（去重后）** | **70** | 10 + 39 + 21 |
| 子 Agent（写作/编辑 Agent）工具 | 10 | 仅文件系统工具 + 白名单 shell |

主 Agent 组装顺序（`graph.py __init__`）：
`create_filesystem_tools()` → `create_skill_tools(skill_loader)` → `create_plugin_tools(plugin_loader)`；
`self.tools` 为全量执行清单（`_execute_tool` 可执行任意已注册工具），
但发给 LLM 的 schema 由 `_build_tool_defs()` 按需筛选（见 §5）。

---

## 1. 内置文件系统工具（常驻，10 个）

定义于 `app/tools/filesystem.py`，注册于 `app/agent/tools.py::create_filesystem_tools()`。

| 工具名 | 用途 | 关键参数 |
|---|---|---|
| `tool_ls` | 列出目录内容 | path |
| `tool_read_file` | 读文件（文本/base64 图片/PDF/音视频） | path, offset, limit |
| `tool_write_file` | 创建/覆写文本文件（自动建目录） | path, content, overwrite |
| `tool_append_file` | 追加内容到文件（大文件分段写入） | path, content |
| `tool_edit_file` | 替换文本（单次/全部） | path, old_string, new_string, replace_all |
| `tool_glob` | 按 glob 找文件 | pattern, root |
| `tool_grep` | 正则搜索文件内容 | pattern, include, context, count_only, files_only, root |
| `tool_execute` | 执行 shell（仅 build/install/test，禁网络命令） | command, timeout(≤600), work_dir |
| `tool_delete_file` | 删除文件/空目录 | path |
| `tool_rename_file` | 重命名/移动文件目录 | path, new_path |

> 说明：`tool_execute` 在带事件队列时走流式输出路径（`_execute_tool_streaming`），失败自动回退同步执行。
> 工作区外路径写入触发 `NeedsPermission`，走前端审批流（见 §7）。

---

## 2. 技能加载工具（39 个，`load_skill_*`）

定义于 `app/agent/tools.py::create_skill_tools()`；来源为 `skills/<name>/SKILL.md`（YAML frontmatter 解析），
当前 **39 个全部启用**（`skills/example_skill.md` 为 `enabled: false`，不加载）。
调用后返回对应 SKILL.md 全文（最佳实践指导），供 Agent 按规范执行任务。

| # | 工具名 | 技能（目录） | 用途 |
|---|---|---|---|
| 1 | `load_skill_algorithmic_art` | algorithmic-art | p5.js 算法艺术（种子随机+参数探索） |
| 2 | `load_skill_ask_matt` | ask-matt | 技能路由：该用哪个技能/流程 |
| 3 | `load_skill_brand_guidelines` | brand-guidelines | Anthropic 官方品牌色/字体规范 |
| 4 | `load_skill_canvas_design` | canvas-design | 高设计质量 PNG/PDF 视觉图 |
| 5 | `load_skill_claude_api` | claude-api | Claude API / Anthropic SDK 应用开发 |
| 6 | `load_skill_code_review` | code-review | 代码评审（标准轴+规格轴） |
| 7 | `load_skill_codebase_design` | codebase-design | 深模块设计词汇 |
| 8 | `load_skill_diagnosing_bugs` | diagnosing-bugs | 疑难 bug / 性能回退诊断循环 |
| 9 | `load_skill_doc_coauthoring` | doc-coauthoring | 文档共创工作流 |
| 10 | `load_skill_docx` | docx | 高级 .docx 编辑（提取/重组/插图等） |
| 11 | `load_skill_domain_modeling` | domain-modeling | 领域语言打磨（术语/重载词/决策记录） |
| 12 | `load_skill_frontend_design` | frontend-design | 高设计质量前端界面 |
| 13 | `load_skill_grill_me` | grill-me | 方案/设计压力测试访谈 |
| 14 | `load_skill_grill_with_docs` | grill-with-docs | 压力访谈+产出 ADR/术语表 |
| 15 | `load_skill_grilling` | grilling | 对计划/决策/想法持续盘问 |
| 16 | `load_skill_handoff` | handoff | 对话压缩为交接文档 |
| 17 | `load_skill_implement` | implement | 按规格/票据实施 |
| 18 | `load_skill_improve_codebase_architecture` | improve-codebase-architecture | 架构深化扫描+可视化 HTML 报告 |
| 19 | `load_skill_internal_comms` | internal-comms | 内部沟通文档撰写 |
| 20 | `load_skill_mcp_builder` | mcp-builder | 高质量 MCP Server 构建 |
| 21 | `load_skill_pdf` | pdf | 高级 PDF 任务（合并/拆分/水印/加密等） |
| 22 | `load_skill_pptx` | pptx | .pptx 生成/编辑 |
| 23 | `load_skill_prototype` | prototype | 一次性原型验证设计问题 |
| 24 | `load_skill_research` | research | 高可信一手来源调研，产出 Markdown |
| 25 | `load_skill_resolving_merge_conflicts` | resolving-merge-conflicts | git 合并/变基冲突解决 |
| 26 | `load_skill_setup_matt_pocock_skills` | setup-matt-pocock-skills | 工程技能仓库初始化配置 |
| 27 | `load_skill_skill_creator` | skill-creator | 技能创建/改进/度量 |
| 28 | `load_skill_slack_gif_creator` | slack-gif-creator | Slack 优化动图 GIF 制作 |
| 29 | `load_skill_tdd` | tdd | 测试驱动开发 |
| 30 | `load_skill_teach` | teach | 教授用户新技能/概念 |
| 31 | `load_skill_theme_factory` | theme-factory | 主题化样式（幻灯片/文档/HTML 等） |
| 32 | `load_skill_to_spec` | to-spec | 对话转规格并发布到 issue tracker |
| 33 | `load_skill_to_tickets` | to-tickets | 计划/规格拆分为 tracer-bullet 票据 |
| 34 | `load_skill_triage` | triage | issue/PR 三态流转管理 |
| 35 | `load_skill_wayfinder` | wayfinder | 超大工程任务决策地图规划 |
| 36 | `load_skill_web_artifacts_builder` | web-artifacts-builder | 多组件 claude.ai HTML artifact 构建 |
| 37 | `load_skill_webapp_testing` | webapp-testing | 本地 Web 应用 Playwright 测试 |
| 38 | `load_skill_writing_great_skills` | writing-great-skills | 优秀技能写作参考 |
| 39 | `load_skill_xlsx` | xlsx | 高级电子表格（公式/图表/格式） |

> 技能工具 schema 描述截断至 200 字符（[token 优化 v3]），完整描述仍在 SKILL.md 内。

---

## 3. 插件工具（21 个，`plugin_*`）

定义于 `app/agent/tools.py::create_plugin_tools()`；来源为 `plugins/*.py`（扫描 `tool_*` 函数），
启用状态由 `plugins/<module>.enabled` 文件控制。当前 **11 个插件启用**。

### 3.1 character-analysis（3 个）
| 工具名 | 用途 |
|---|---|
| `plugin_character-analysis_tool_list_characters` | 列出全部角色及台词数 |
| `plugin_character-analysis_tool_get_character_dialogues` | 获取某角色全部台词（character_name, limit） |
| `plugin_character-analysis_tool_analyze_character_interactions` | 分析同章共现角色 |

### 3.2 docx-generator（1 个）
| 工具名 | 用途 |
|---|---|
| `plugin_docx-generator_tool_create_docx` | 按 sections 生成 .docx（title, sections, output_path） |

### 3.3 excel-generator（1 个）
| 工具名 | 用途 |
|---|---|
| `plugin_excel-generator_tool_create_excel` | 按 sheets 生成 .xlsx |

### 3.4 http-client（3 个）
| 工具名 | 用途 |
|---|---|
| `plugin_http-client_tool_http_request` | 通用 HTTP 请求 |
| `plugin_http-client_tool_http_get` | GET 请求（headers 为 JSON 字符串） |
| `plugin_http-client_tool_http_post` | POST 请求 |

### 3.5 internet-search（2 个）
| 工具名 | 用途 |
|---|---|
| `plugin_internet-search_tool_internet_search` | 联网搜索（region: cn/global） |
| `plugin_internet-search_tool_extract_urls` | 提取 URL 内容 |

### 3.6 kb-export（1 个）
| 工具名 | 用途 |
|---|---|
| `plugin_kb-export_tool_export_kb_to_docx` | 按 query 检索知识库并导出 .docx |

### 3.7 pdf-generator（1 个）
| 工具名 | 用途 |
|---|---|
| `plugin_pdf-generator_tool_create_pdf` | 按 sections 生成 .pdf |

### 3.8 pptx-generator（1 个）
| 工具名 | 用途 |
|---|---|
| `plugin_pptx-generator_tool_create_pptx` | 按 slides 生成 .pptx |

### 3.9 voice-clone（4 个）
| 工具名 | 用途 |
|---|---|
| `plugin_voice-clone_tool_voice_clone` | 声音克隆 |
| `plugin_voice-clone_tool_custom_voice` | 自定义声音 |
| `plugin_voice-clone_tool_voice_design` | 声音设计 |
| `plugin_voice-clone_tool_voice_transcribe` | 语音转写（audio_path） |

### 3.10 weather（1 个）
| 工具名 | 用途 |
|---|---|
| `plugin_weather_tool_get_weather` | 天气查询（city, forecast_days） |

### 3.11 weather-alert（3 个）
| 工具名 | 用途 |
|---|---|
| `plugin_weather-alert_tool_get_weather_alert` | 天气预警 |
| `plugin_weather-alert_tool_get_typhoon_info` | 台风信息 |
| `plugin_weather-alert_tool_get_weather_summary` | 多城市天气汇总（默认 北京,上海,广州） |

### 3.12 已存在但未启用的插件（不产生工具）
- `example_plugin.py`（无 .enabled）：`tool_calculate`、`tool_get_current_time`、`tool_hello`
- `file_reader.py`（无 .enabled）：`tool_read_file`
- `filesystem.enabled` 残留文件存在，但 `filesystem.py` 已删除（内置文件工具替代），实际不加载

---

## 4. 固定（pin）工具

`data/pinned_tools.json` 当前内容：`load_skill_code_review`（enabled）。
固定工具在按需挂载（v5）时**始终挂载 schema**（v6 机制），不受意图关键词筛选影响，
解决"模型看不到工具"的顾虑。当前仅 1 个 pin，与技能工具同名（去重后仍为 1 个）。

---

## 5. 按需挂载机制（token 优化 v5 / v6）

`graph.py::_build_tool_defs()` 决定每轮发给 LLM 的工具 schema（system prompt 仍列出全部工具名）：

1. **常驻**：`tool_` 前缀（核心文件工具 10 个）；
2. **固定**：`data/pinned_tools.json` 中 pin 的工具（v6）；
3. **已使用保留**：本会话用过的工具；
4. **意图关键词命中**：`_INTENT_RULES` 表（15 组），问题含关键词且工具名前缀匹配即挂载。

意图规则摘要（关键词 → 挂载工具组）：
| 意图关键词（示例） | 挂载 |
|---|---|
| 天气/台风/气象/温度/降雨/下雪/weather/typhoon/forecast | plugin_weather*, plugin_weather-alert* |
| 文档/word/docx/pdf/excel/xlsx/ppt/报告/表格/幻灯片 | docx/pdf/excel/pptx 生成器 + load_skill_docx/pdf/xlsx/pptx/doc_coauthoring |
| 网页/前端/react/vue/html/css/网站/页面/artifact/frontend/web | load_skill_frontend_design / web_artifacts_builder / webapp_testing / theme_factory / canvas_design |
| 搜索/查一下/新闻/资讯/上网/search/news/internet | plugin_internet-search_* |
| 图片/海报/设计/艺术/绘图/生成图/image/poster/art/draw | load_skill_canvas_design / algorithmic_art / slack_gif_creator |
| 语音/声音/配音/克隆/合成/voice/audio/speech | plugin_voice-clone_* |
| 角色/人物/对话/台词/character/dialogue | plugin_character-analysis_* |
| 知识库/kb/导出 | plugin_kb-export_* |
| 代码/编程/bug/调试/重构/code/debug/test/tdd/review/实现 | load_skill_tdd / code_review / diagnosing_bugs / implement / to_tickets / grilling / grill_me / codebase_design |
| 技能/skill | load_skill_*（全部） |
| 插件/plugin | plugin_*（全部） |
| 教学/学习/teach | load_skill_teach |
| 研究/research | load_skill_research |
| 模型/api/claude/大模型 | load_skill_claude_api |
| 架构/模块/设计模式/architecture | load_skill_codebase_design / domain_modeling / improve_codebase_architecture |

> 模型若调用了未挂载工具，`_execute_tool` 仍可执行（`self.tools` 为全量），下一轮该工具自动保留。
> 效果：schema 固定开销从 8–12K 降到 2–4K token。
> 附加优化：天气/台风插件结果 >1500 字符时截断（`_bound_plugin_result`），避免大块数据每轮重发。

---

## 6. 子 Agent（写作/编辑 Agent）工具（10 个）

`app/agent/sub_tools.py`（`SUB_AGENT_TOOLS`）——子 Agent 仅可用文件系统工具，**不含**技能/插件：

`tool_ls`、`tool_read_file`、`tool_glob`、`tool_grep`、`tool_write_file`、`tool_append_file`、
`tool_edit_file`、`tool_delete_file`、`tool_rename_file`、`tool_execute`

- 走 `run_tool()` → `asyncio.to_thread` 执行，参数经 `_coerce_args` 过滤（防多传参数 TypeError）；
- **权限桥**：工作区外路径抛 `NeedsPermission` → 生成 `permission_request` 事件 → 前端审批；
  无事件队列时直接拒绝（防死等）；审批通过后对该路径临时放行（`add_temp_approval`）；
- `tool_execute` 描述中声明"白名单 shell 命令、120s 超时"（实际超时上限 600s，由参数控制）。

---

## 7. 权限与安全

- **工作区白名单**：可写路径集合由 `app/permission.py::get_manager().list_workspaces()` 提供
  （当前：`E:\AgentSuper\backend`、`D:\doc`、`F:\DreamWeaver`、`E:\project\opencode-dev`、`E:\AgentSuper\backend\app`）；
  工作区外写入需审批。
- **tool_execute 限制**：仅 build/install/test，禁网络命令（curl/wget/ping 走 http-client 插件）。
- **长内容规则**：`LONG_CONTENT_FILE_RULE` 强制 >500 中文字符/1000 token 的内容写文件而非内联输出；
  .docx/.pdf/.xlsx/.pptx 走对应生成器插件；回复仅给路径+摘要+结构。
- **自定义工具安全壳**：前端「自定义工具」脚本写入 `plugins/custom_<name>.py` 并套 `_TEMPLATE` 壳
  （自动补 PLUGIN_* 元信息），名称经 `_slug` 净化防路径穿越；脚本至少含一个 `tool_*` 函数。

---

## 8. 运维速查

| 操作 | 方法 |
|---|---|
| 启用/禁用技能 | 前端「Skills」页 → 写回 SKILL.md frontmatter `enabled:`；或改 `skills/<name>/SKILL.md` |
| 启用/禁用插件 | 创建/删除 `plugins/<module>.enabled` 文件（前端「Plugins」页） |
| 新增插件 | `plugins/` 下新建 .py，定义 `PLUGIN_NAME/PLUGIN_VERSION/PLUGIN_DESCRIPTION` + `tool_*` 函数，再 touch .enabled |
| 新增技能 | `skills/<name>/SKILL.md`（YAML frontmatter 含 name/description/enabled） |
| 固定工具（pin） | 前端工具目录 pin → 写入 `data/pinned_tools.json` |
| 自定义脚本工具 | 前端「Skills → 自定义工具」→ `plugins/custom_<name>.py`（复用插件执行链路） |
| 文件工具扩展 | 修改 `app/tools/filesystem.py` + `create_filesystem_tools()` 的 `_PARAM_DOCS/_DESC` |
| 意图挂载规则调整 | 修改 `graph.py::_INTENT_RULES` |

---

## 附：工具名与执行链路映射

```
系统提示（全量工具名）─► LLM 选工具（schema 按需挂载）─► graph.py::_execute_tool(name, args)
        │                                                    ├─ tool_*      → app/tools/filesystem.py（权限检查）
        │                                                    ├─ load_skill_*→ 读取 skills/<name>/SKILL.md 全文
        │                                                    └─ plugin_*    → PluginLoader.call_function（工具执行+结果截断）
        └─ 子 Agent（长文写作/编辑）→ sub_tools.py::run_tool → 文件工具 + 权限桥（前端审批）
```
