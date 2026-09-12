// 支持的模型列表（multi-agent 会话共享）
export const SUPPORTED_MODELS = [
  { value: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash', desc: '轻量高速模型，日常问答与多智能体调度首选，速度与质量兼顾' },
  { value: 'deepseek/deepseek-v4-pro', label: 'DeepSeek V4 Pro', desc: '旗舰推理模型，复杂任务、长文本与深度分析能力更强' },
  { value: 'openai/gpt-4o', label: 'OpenAI GPT-4o', desc: 'OpenAI 多模态旗舰模型，图文理解与通用任务表现均衡' },
  { value: 'openai/gpt-4o-mini', label: 'OpenAI GPT-4o-mini', desc: 'GPT-4o 轻量版，成本更低、响应更快，适合高频简单任务' },
  { value: 'ollama/qwen2.5-coder:latest', label: 'Ollama Qwen2.5-Coder', desc: '本地代码生成模型，编程与代码任务首选' },
  { value: 'ollama/qwen2.5:7b', label: 'Ollama Qwen2.5 7B', desc: '通义千问轻量版，中文理解与生成能力均衡' },
  { value: 'ollama/qwen2.5:14b', label: 'Ollama Qwen2.5 14B', desc: '通义千问中型版，复杂推理与长文本处理能力更强' },
  { value: 'ollama/qwen2.5:32b', label: 'Ollama Qwen2.5 32B', desc: '通义千问大型版，高质量生成与深度分析' },
  { value: 'ollama/codellama:latest', label: 'Ollama CodeLlama', desc: 'Meta 代码专用模型，Python/JavaScript 编程优化' },
  { value: 'ollama/llama3.1:latest', label: 'Ollama Llama 3.1', desc: 'Meta 最新通用模型，多语言支持与推理能力' },
  { value: 'ollama/mistral:latest', label: 'Ollama Mistral', desc: '轻量高效模型，快速响应与低资源占用' },
] as const
