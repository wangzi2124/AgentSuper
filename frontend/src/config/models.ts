// 支持的模型列表（multi-agent 会话共享）
export const SUPPORTED_MODELS = [
  { value: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash', desc: '轻量高速模型，日常问答与多智能体调度首选，速度与质量兼顾' },
  { value: 'deepseek/deepseek-v4-pro', label: 'DeepSeek V4 Pro', desc: '旗舰推理模型，复杂任务、长文本与深度分析能力更强' },
  { value: 'openai/gpt-4o', label: 'OpenAI GPT-4o', desc: 'OpenAI 多模态旗舰模型，图文理解与通用任务表现均衡' },
  { value: 'openai/gpt-4o-mini', label: 'OpenAI GPT-4o-mini', desc: 'GPT-4o 轻量版，成本更低、响应更快，适合高频简单任务' },
] as const
