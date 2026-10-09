// 后端 REST 封装：类型与 fetch 统一入口
async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.detail ?? `${r.status} ${r.statusText}`)
  }
  return r.json() as Promise<T>
}

// ---- Prompt 工坊 ----
export interface PromptSummary {
  layer: string
  effective: string
  default: string
  overridden: boolean
  history_count: number
}
export interface PromptVersionInfo {
  text: string
  source: string
  timestamp: string
  note: string
}
export interface PromptDetail extends PromptSummary {
  history: PromptVersionInfo[]
}
export interface DryRunResult {
  layer: string
  messages: { role: string; content: string }[]
  warnings: string[]
  prompt_bytes: number
  est_prompt_tokens: number
}

export const api = {
  promptsList: () => req<{ layers: PromptSummary[] }>('/api/prompts'),
  promptsDetail: (layer: string) => req<PromptDetail>(`/api/prompts/${layer}`),
  promptsEdit: (layer: string, text: string, note: string) =>
    req<{ layer: string; effective: string }>(`/api/prompts/${layer}`, {
      method: 'PUT',
      body: JSON.stringify({ text, note }),
    }),
  promptsReset: (layer: string) =>
    req<{ layer: string; effective: string }>(`/api/prompts/${layer}`, { method: 'DELETE' }),
  promptsDryRun: (layer: string, xBlock: string, userDirective: string) =>
    req<DryRunResult>(`/api/prompts/${layer}/dry_run`, {
      method: 'POST',
      body: JSON.stringify({ x_block: xBlock, user_directive: userDirective }),
    }),
}

// ---- 写作模块 ----
export interface SkillModule {
  name: string
  title: string
  stages: string[]
  default_for: string[]
  rules_bytes: number
}

// ---- 语料 ----
export interface Excerpt {
  excerpt_id: string
  article_id: string
  title: string
  excerpt_type: string
  tags: string[]
  text: string
}

// ---- 运行 ----
export interface LayerRunBody {
  x_pairs: [string, string][]
  materials?: { source_id: string; family: string; content: string; title?: string }[]
  user_directive?: string
  x_chunk_length?: number
  modules?: string[]
  corpus_query?: { excerpt_type?: string; tag?: string; keyword?: string; limit?: number }
}
export interface Receipt {
  index: number
  x_hash: string
  y: string
  status: string
  completion_status: string
  attempts: number
  error: string
  timestamp: string
}
export interface LayerRunResult {
  run_id: string
  layer: string
  y: string
  pairs: [string, string][]
  resumed_indices: number[]
  skills_injected: string[]
  corpus_injected: string[]
  calls: { completion_status: string; attempts: number }[]
  journal: {
    layer: string
    run_key: string
    total: number
    completed: number
    failed: number
    receipts: Receipt[]
  }
}

export const skillsApi = {
  list: () => req<{ modules: SkillModule[] }>('/api/skills/modules'),
  detail: (name: string) =>
    req<{ name: string; title: string; rules: string }>(`/api/skills/modules/${name}`),
}

export const corpusApi = {
  types: () => req<{ types: { type: string; count: number }[] }>('/api/corpus/types'),
  tags: () => req<{ tags: { tag: string; count: number }[] }>('/api/corpus/tags'),
  search: (q: { excerpt_type?: string; tag?: string; keyword?: string; limit?: number }) => {
    const p = new URLSearchParams()
    Object.entries(q).forEach(([k, v]) => {
      if (v) p.set(k, String(v))
    })
    return req<{ total: number; excerpts: Excerpt[] }>(`/api/corpus/search?${p}`)
  },
}

export const runsApi = {
  run: (layer: string, body: LayerRunBody) =>
    req<LayerRunResult>(`/api/runs/${layer}`, { method: 'POST', body: JSON.stringify(body) }),
  retry: (layer: string, body: LayerRunBody) =>
    req<LayerRunResult>(`/api/runs/${layer}/retry`, { method: 'POST', body: JSON.stringify(body) }),
  status: (runId: string) => req<LayerRunResult['journal']>(`/api/runs/${runId}`),
}

export const LAYERS = ['intro', 'outline', 'plot', 'draft', 'polish'] as const
export const LAYER_LABELS: Record<string, string> = {
  intro: '简介',
  outline: '大纲',
  plot: '剧情',
  draft: '正文',
  polish: '润色',
}
