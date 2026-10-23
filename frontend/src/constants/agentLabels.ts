/** 后端 agent_id → 中文展示名（agent_name 目前是 build/plan 这类内部标识）。 */
export const AGENT_LABELS: Record<string, string> = {
  build: '执行助手',
  plan: '任务规划',
  supervisor: '调度',
  research: '研究助手',
  code: '编码助手',
  file: '文件助手',
}

/** 图节点 → 中文阶段名（后台执行过程展示）。 */
export const NODE_LABELS: Record<string, string> = {
  filter: '识别意图',
  plan: '任务规划',
  rag: '知识库检索',
  memory_recall: '记忆召回',
  context_compress: '整理上下文',
  execute_subagent: '执行任务',
  parallel: '并行执行',
  parallel_worker: '并行执行',
  grade_docs: '评估检索结果',
  rewrite: '改写查询',
  synthesize: '汇总结果',
  memory_store: '保存记忆',
}
