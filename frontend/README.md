# AgentSuper Frontend

Vue 3 + TypeScript + Vite 单页应用（SPA），Pinia 状态管理，Vue Router 路由，移动端用 Vant。

## 开发

```bash
npm install
npm run dev        # http://localhost:5173，Vite 代理 /api → http://localhost:8000
```

后端需先启动（见仓库根 README）。开发态下 `/api/*` 由 Vite dev server 代理到 `localhost:8000`（见 `vite.config.ts`）。

## 构建与预览

```bash
npm run build      # vue-tsc -b && vite build → dist/
npm run preview    # 本地预览构建产物（默认 4173）
```

## 校验

```bash
npm run typecheck  # vue-tsc -b
npm run check      # typecheck + vitest run
npm test           # vitest run
```

---

## 部署（前后端分离）

前端所有 API 调用都是**相对路径 `/api/...`**。部署有两种方式：

### 方案 A：同源反向代理（推荐，前端零改动）

前端静态文件和后端**挂在同一个域名**下，`/api/*` 反代到后端。相对路径直接可用，**不用改前端、不用配 CORS**。

1. `npm run build` 生成 `dist/`
2. 把 `dist/` 交给 Nginx/Caddy，并把 `/api` 反代到后端
3. 后端只监听本机：`uvicorn main:app --host 127.0.0.1 --port 8000`

现成配置见仓库 `deploy/nginx.conf` 与 `deploy/Caddyfile`（已处理 SPA history 回退 + SSE 关缓冲）。

> ⚠️ SSE（`/api/chat/multi-agent/stream`）是 `text/event-stream`，反代/网关**必须关闭缓冲**，否则前端收不到逐字流。

### 方案 B：跨域（前端域名 ≠ 后端域名）

设置两个构建期环境变量（见下），并在后端 `.env` 配 CORS：

```bash
# frontend/.env.production
VITE_API_BASE=https://api.example.com        # 后端地址（去掉尾部斜杠）
VITE_ADMIN_TOKEN=<和后端 ADMIN_TOKEN 一致>    # 仅后端配了 ADMIN_TOKEN 时需要
```

```ini
# backend/.env
CORS_ORIGINS=["https://app.example.com"]     # 允许的前端源（JSON 数组）
ADMIN_TOKEN=<强随机>                          # 管理接口鉴权；不配则仅 localhost 可管理
```

- 前端 HTTPS → 后端也必须 HTTPS（否则浏览器拦截混合内容）。
- 不配 `VITE_ADMIN_TOKEN` 而后端配了 `ADMIN_TOKEN` → 前端管理操作（工作目录/插件/技能/配置）会 **403**。

### 环境变量（构建期注入）

| 变量 | 说明 |
|------|------|
| `VITE_API_BASE` | API 基址。留空 = 同源（方案 A）。设置后所有 `/api/...` 请求自动前缀该地址 |
| `VITE_ADMIN_TOKEN` | 后端 `ADMIN_TOKEN` 对应值。设置后请求自动带 `Authorization: Bearer <token>` |
| `VITE_TTS_BASE` | 语音 TTS 基址（默认同源 `/api/voice`） |
| `VITE_TTS_SPEAKER` | TTS 音色（默认 `Vivian`） |

实现：`src/api/base.ts` 的 `installApiFetch()`（在 `src/main.ts` 挂载前调用）——**全局包装 `window.fetch`**：以 `/` 开头的相对 URL 自动加 `API_BASE` 前缀，并注入管理员令牌；两个变量都没配时不改动全局 fetch。

复制 `frontend/.env.example` 为 `frontend/.env.production` 按需填写。

### 注意

- **工作目录/权限都是「后端主机」的本地路径**：前端「选择目录」列的是后端机器的目录，不是用户浏览器所在机器。
- 文件生成、向量库、模型（Ollama）、语音 TTS 全部在后端主机运行。
- 若前端部署在**子路径**（`example.com/app/`），需在 `vite.config.ts` 设 `base: '/app/'`（当前默认 `/`）。
