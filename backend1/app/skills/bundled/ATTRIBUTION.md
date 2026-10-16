# 内置技能来源说明

本目录下的技能随 AgentSuper 一起分发（MIT License），由 `seed_bundled()` 在**首次启动时**
自动复制到受管库 `backend/data/skills/`，之后即作为普通受管技能对待 —— **可编辑、可删除**，
删除后不会在下次启动时自动恢复（播种清单见受管库的 `.bundled.json`）。

## 上游

- 项目：`Skills_Real_Engineers`（https://github.com/wangzi2124/Skills_Real_Engineers）
- 版权：Copyright (c) 2026 Matt Pocock
- 许可：MIT，见同目录 [`LICENSE`](./LICENSE)

## 目录结构

上游仓库把技能按用途分在分类目录下，本目录**保持了上游的原始布局**：

```
bundled/
├── LICENSE              # MIT 许可证原文
├── ATTRIBUTION.md       # 本文件
├── deprecated/          # 4 个：上游已废弃，保留仅供旧工作流参考
├── engineering/         # 10 个
├── in-progress/         # 4 个
├── misc/                # 4 个
├── personal/            # 2 个
└── productivity/        # 4 个
```

每个技能是 `<分类>/<名字>/SKILL.md`，同级目录下的其它文件（`scripts/`、`references/`、
`templates/` 等）是该技能的配套资产，会随目录一起播种。

`SkillLoader` 的递归发现规则：根层 `*.md` 视为平铺技能；深度 ≥1 只识别**含 `SKILL.md` 的目录**，
因此各分类下的 `README.md` 不会被误当成技能。扫描会跳过隐藏目录、符号链接以及
`node_modules` / `.venv` / `dist` 等常见杂物目录。

## 说明

- 上游技能的正文与配套脚本**未经改动**地随包分发。
- 带 `disable-model-invocation: true` 的技能（`ubiquitous-language`、`zoom-out`）不会暴露为
  模型工具，只能由用户在界面上显式加载。
- 少数技能面向 Claude Code / macOS 生态（如 `git-guardrails-claude-code`、`setup-matt-pocock-skills`），
  在本项目中不保证完全可用，按原样收录。
