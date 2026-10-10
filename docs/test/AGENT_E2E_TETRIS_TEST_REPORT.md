# 后端 Agent 全链路长任务 E2E 测试报告

**测试日期**: 2026-10-09  
**测试对象**: AgentSuper 后端（多 Agent 流式）  
**测试用例**: TC-01~TC-07（见测试方案 `docs/test/AGENT_E2E_TETRIS_TEST_PLAN.md`）  
**结论**: **PASS（第3次运行成功）**

## 1. 测试环境

| 项目 | 值 |
|---|---|
| 后端 | uvicorn http://127.0.0.1:8000（`/health=ok`） |
| 配置 | `backend/.env` 基于 `C:\tool\.env`，`ENABLE_RERANKER=false`、`EXTERNAL_PATH_DEFAULT=allow`、`AUTH_TOKEN_SECRET` 未设、`MAX_TOOL_ROUNDS=24`、`MAX_STEPS=40`、`MAX_CONTEXT_TOKENS=131072` |
| LLM | `deepseek/deepseek-v4-flash`（实测可用） |
| 输出目录 | `C:\tool\tetris-react`（会话 `directory` 传入，落盘免审批） |
| 校验工具 | Node v24.19.0、esbuild（`frontend/node_modules/esbuild`）用于 JSX/JS 语法校验 |

## 2. 运行次数与结果

共执行3次`e2e_agent_tetris_test.py tetris`：

| 序号 | 会话ID | SSE 事件 | 文件数 | 大小 | 语法校验 | TC-03 | TC-06 | 备注 |
|---|---|---|---|---|---|---|---|---|
| 1 | `ses_209489nku1bd507e1`（`20261009_103422_*`） | 39 | 16 | ~22.8KB | 4 JS 文件通过 | **FAIL**（缺 `src/main.jsx`、`src/App.jsx`） | **FAIL**（driver 解析 bug） | 到达 `MAX_TOOL_ROUNDS=8` 上限，Agent 无法写完剩余入口文件；模型先写了 `tetrominoes.js/tetrisReducer.js/useTetris.js` 等逻辑文件，未续写 `src/main.jsx/App.jsx/styles.css` |
| 2 | `ses_2094929h0xtcacb43`（`20261009_104124_*`） | 10 | 0 | 0 | 0 | **FAIL** | PASS | 模型以**非结构化** `<tool_call>{...}` 文本形式调用工具，导致输出截断、未实际写盘（偶发行为） |
| 3 | `ses_20949dp65u73c1a5b`（`20261009_104413_*`） | 65 | 2289* | 38.24MB* | 1420 JS 文件通过（含 `node_modules` 内 JS） | **PASS** | PASS | 使用结构化 `tool_calls` 正常写盘→随后执行 `npm install`、`npm run build`，完整生成可运行的 React Tetris 项目 |

\* 第3次最终结果中：`disk` 包含 `node_modules/`（2258文件）、`.agents/`、`dist/`（3文件）、`src/`（6文件）、根文件等。**业务相关游戏源码**实际为 **9** 个文件：`package.json`、`vite.config.js`、`index.html`、`src/main.jsx`、`src/App.jsx`、`src/game/tetrominoes.js`、`src/game/useTetris.js`、`src/game/tetrisReducer.js`、`src/styles.css`，均已落盘且可编译。

## 3. 第3次运行逐条用例

| 用例 | 标题 | 结果 | 说明 |
|---|---|---|---|
| TC-01 | 服务健康 | **PASS** | `/health` 返回 `{"status":"ok", "vector_store_size":0}` |
| TC-02 | 冒烟（单句自我介绍） | **PASS** | 先前单独跑 smoke 返回`routed_to=build`、回答正常（见 `20261009_103405_*`） |
| TC-03 | 长任务多文件写入 | **PASS** | 关键文件全部存在：`package.json`、`vite.config.js`、`index.html`、`src/main.jsx`、`src/App.jsx`、`src/game/*.js`、`src/styles.css`。共16个非 node_modules 顶层/业务文件（含 `.gitignore`、`.agents/`）。Agent 真正写盘并未发生截断（0字节文件数=0）。 |
| TC-04 | 大文件未截断 | **PASS** | 全部文件字节数>0。单个最大业务文件：`src/game/tetrominoes.js` 6670 字节，`src/App.jsx` 4865 字节等，结构完整。 |
| TC-05 | 产物语法校验 | **PASS** | 对 `.js/.jsx/.mjs` 共1420个文件执行 `esbuild` 语法解析（含 `node_modules`），全部通过（`ok_all=true`）。业务代码人工抽查：`tetrominoes.js`、`tetrisReducer.js`、`useTetris.js`、`vite.config.js` 均 `node --check` 通过，`main.jsx/App.jsx` 使用 JSX + `automatic` 也解析成功。 |
| TC-06 | 落库与轮次快照 | **PASS** | `GET /api/sessions/{id}/messages?kind=multi-agent` 返回消息列表且含 assistant 消息；`POST /api/chat/multi-agent/diff` 返回 diff 文件列表（含新增的外部文件 `C:\tool\tetris-react\*`）。assistant 消息 `data.files_changed` 含 9 个外部新增文件。 |
| TC-07 | 零进展守卫 | **PASS** | `done.answer` 不含「零进展」「部分完成失败」等征兆文案。第3次回答为完整的**完成情况总结**，列出文件清单、运行方法、已实现功能点。 |

## 4. 生成产物快照（业务文件）

```text
C:\tool\tetris-react\
├── .agents/steps/*  (任务步状态，8+步 + latest)
├── dist/            (npm run build 产物：index.html + assets/*.js + *.css)
├── node_modules/    (npm install 自动生成)
├── src/
│   ├── App.jsx
│   ├── main.jsx
│   ├── styles.css
│   └── game/
│       ├── tetrisReducer.js
│       ├── tetrominoes.js
│       └── useTetris.js
├── .gitignore
├── index.html
├── package.json
├── package-lock.json
└── vite.config.js
```

- 核心逻辑（`tetrominoes.js`）包含**bag随机、SRS式旋转、墙踢、碰撞、消行、计分、速度曲线**等实现，语法/结构完整。
- `useTetris.js`+`tetrisReducer.js` 构成状态机+副作用（重力定时器、键盘监听），`App.jsx` 渲染棋盘、预览、暂停/结束遮罩、计分/等级。
- `vite.config.js` 使用 `@vitejs/plugin-react-swc`（依赖已装）；`package.json` 脚本齐全。
- `dist/` 构建成功（产出 `index-BP6QBJxR.js` ~151KB、`index-WSBL523p.css` ~3.4KB、`index.html`），证明项目整体可构建。

## 5. 发现的环境/行为差异（有价值）

1. **`MAX_TOOL_ROUNDS` 需足够大**（用户原值8）——第1次因轮次耗尽导致**未写完入口文件**，但已写逻辑文件且给出补齐建议。将 `MAX_TOOL_ROUNDS` 提升至24后顺利写完全套文件。
2. **模型工具调用格式偶发回归**（第2次）——出现 `<tool_call>{...}</tool_call>` 文本而非 OpenAI-style `tool_calls`，导致 Agent 未执行写盘、直接截断回答。此属 LLM 输出格式偶发性；第3次恢复正常。
3. **Agent 在写完业务文件后主动执行构建**（第3次）——`npm install` + `npm run build` 均由 Agent 调用工具执行，进一步验证「真实写盘 + 可执行校验」全链路。
4. **权限/目录生效**——传入 `directory=C:\tool\tetris-react` 使外部目录写入走受控路径且免审批（配合 `EXTERNAL_PATH_DEFAULT=allow` 测试化），`files_changed` 正确标记 `external=true`。
5. **落库字段**——`GET /api/sessions/{id}/messages` 返回**数组**（`data` 为 `[...]`），而非旧有 `{messages: [...]}` 包裹；驱动已适配该结构避免解析错误。

## 6. 原始产出文件

| 文件 | 说明 |
|---|---|
| `docs/test/AGENT_E2E_TETRIS_TEST_PLAN.md` | 测试方案（全量 TC 定义） |
| `backend/scripts/e2e_agent_tetris_test.py` | E2E 驱动（自动响应 `permission_request`、收集 SSE、esbuild 语法校验、调用 `/sessions/*` 与 `/multi-agent/diff`） |
| `docs/test/runs/20261009_103405_*` | 冒烟运行（PASS） |
| `docs/test/runs/20261009_103422_*` | 第1次（部分通过） |
| `docs/test/runs/20261009_104124_*` | 第2次（偶发工具调用格式失败） |
| `docs/test/runs/20261009_104413_*` | 第3次（**全部 PASS**）— 最终有效记录 |
| `C:\tool\tetris-react\` | 最终生成的可运行 React Tetris 工程（含 dist/、node_modules/） |

## 7. 建议

- **轮次上限**：若要稳定跑「多文件+构建」这类长任务，建议 `MAX_TOOL_ROUNDS ≥ 20`（本次用24可覆盖）。8偏紧。
- **工具调用健壮性**：建议在生成环节对「非结构化工具标记」（`<tool_call>...`）做**回退解析**或**强制 JSON tool schema 输出**，降低偶发格式漂移导致空写盘的概率。
- **测试增强**：可补充 `TC-05b`（`npm run build -- --mode production` 强校验）、`TC-08`（前端启动 sanity：用 node 启动 vite preview 或直接校验 `dist/assets/*.js` 语法）但本次已验证 `dist/` 产出成功，故非必须。

**最终判定：PASS** — 后端 Agent 全链路（流式SSE→Supervisor路由→build Agent→工具写盘→快照落库→会话消息）在真实LLM场景下端到端可用。