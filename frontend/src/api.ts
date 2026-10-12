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
  promptsDetail: (layer: string, page?: number, pageSize?: number) =>
    req<PromptDetail & PageMeta>(
      `/api/prompts/${layer}${page != null ? `?page=${page}&page_size=${pageSize ?? 10}` : ''}`,
    ),
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
  chapter_no?: number
  project?: string
  project_state?: boolean
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
  state_injected?: string[]
  markers_injected: MarkerInfo[]
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

// ---- 审稿（M2）----
export interface ReviewItem {
  name: string
  status: string // 稳 | 不稳
  problem: string
}
export interface ChapterReview {
  items: ReviewItem[]
  verdict: string // pass | blocked
  summary: string
  unstable_count: number
  attempts: number
  rhythm_warning?: string // 审稿附带的节奏饥饿提示（纯数据检查，不参与 blocked 判定）
}

// ---- 必现标记（M2）----
export interface MarkerInfo {
  id: string
  target_chapter: number
  content: string
  status: string // pending | done | dropped
  source: string // manual | extracted | review
  created_at: string
}
export interface MarkersSummary {
  markers: MarkerInfo[]
  pending_count: number
  added?: MarkerInfo[]
}

export const reviewApi = {
  chapter: (outline: string, prose: string) =>
    req<ChapterReview>('/api/review/chapter', {
      method: 'POST',
      body: JSON.stringify({ outline, prose }),
    }),
}

export const refineApi = {
  // 迭代润色：在 text 基础上按 directive 调整，SSE 实时回调，结束返回调整后全文
  stream: (text: string, directive: string, h: SSEHandlers): Promise<{ y: string }> =>
    fetch('/api/refine/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, directive }),
    }).then((r) => consumeSSE<{ y: string }>(r, h)),
}

export interface PageMeta {
  total?: number
  page?: number
  page_size?: number
}

export const markersApi = {
  list: (project?: string, page?: number, pageSize?: number) => {
    const q = new URLSearchParams()
    if (project) q.set('project', project)
    if (page != null) {
      q.set('page', String(page))
      q.set('page_size', String(pageSize ?? 20))
    }
    const qs = q.toString()
    return req<MarkersSummary & PageMeta>(`/api/markers${qs ? `?${qs}` : ''}`)
  },
  add: (target_chapter: number, content: string, project?: string) =>
    req<MarkersSummary>(
      `/api/markers${project ? `?project=${encodeURIComponent(project)}` : ''}`,
      { method: 'POST', body: JSON.stringify({ target_chapter, content }) },
    ),
  extract: (text: string, project?: string) =>
    req<MarkersSummary>(
      `/api/markers/extract${project ? `?project=${encodeURIComponent(project)}` : ''}`,
      { method: 'POST', body: JSON.stringify({ text }) },
    ),
  setStatus: (id: string, status: string, project?: string) =>
    req<MarkersSummary>(
      `/api/markers/${encodeURIComponent(id)}/status${project ? `?project=${encodeURIComponent(project)}` : ''}`,
      { method: 'PUT', body: JSON.stringify({ status }) },
    ),
  verify: (prose: string, chapter_no?: number, project?: string) =>
    req<{ results: { marker: MarkerInfo; hit: boolean }[] }>(
      `/api/markers/verify${project ? `?project=${encodeURIComponent(project)}` : ''}`,
      { method: 'POST', body: JSON.stringify({ prose, chapter_no }) },
    ),
  fromReview: (problems: string[], next_chapter: number, project?: string) =>
    req<MarkersSummary>(
      `/api/markers/from_review${project ? `?project=${encodeURIComponent(project)}` : ''}`,
      { method: 'POST', body: JSON.stringify({ problems, next_chapter }) },
    ),
}

export const skillsApi = {
  list: () => req<{ modules: SkillModule[] }>('/api/skills/modules'),
  detail: (name: string) =>
    req<{ name: string; title: string; rules: string }>(`/api/skills/modules/${name}`),
}

export const corpusApi = {
  types: () => req<{ types: { type: string; count: number }[] }>('/api/corpus/types'),
  tags: () => req<{ tags: { tag: string; count: number }[] }>('/api/corpus/tags'),
  search: (
    q: { excerpt_type?: string; tag?: string; keyword?: string; limit?: number },
    page?: number,
    pageSize?: number,
  ) => {
    const p = new URLSearchParams()
    Object.entries(q).forEach(([k, v]) => {
      if (v) p.set(k, String(v))
    })
    if (page != null) {
      p.set('page', String(page))
      p.set('page_size', String(pageSize ?? 20))
    }
    return req<{ total: number; excerpts: Excerpt[] } & PageMeta>(`/api/corpus/search?${p}`)
  },
  importChaishu: (dir: string, title?: string, tags?: string[], sourceTxt?: string) =>
    req<{
      added: number
      by_type: Record<string, number>
      title: string
      article_id: string
      chapters_total: number
      labeled: number
      project_id: string
      entities?: Record<string, number>
      entities_preextracted?: boolean
    }>('/api/corpus/import_chaishu', {
      method: 'POST',
      body: JSON.stringify({ dir, title: title ?? '', tags: tags ?? [], source_txt: sourceTxt ?? '' }),
    }),
}

// SSE 公共消费器：解析 thinking/delta/resumed/block_done/notice/result/fail 事件
interface SSEHandlers {
  onThinking?: (text: string, index: number) => void
  onDelta?: (text: string, index: number) => void
  onResumed?: (index: number) => void
  onBlockDone?: (index: number, status: string, chars: number) => void
  onNotice?: (text: string) => void
}

async function consumeSSE<T>(r: Response, h: SSEHandlers): Promise<T> {
  if (!r.ok || !r.body) {
    const d = await r.json().catch(() => ({}))
    throw new Error(d.detail ?? `${r.status} ${r.statusText}`)
  }
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let result: T | null = null
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      let event = 'message'
      let data = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (!data) continue
      const p = JSON.parse(data)
      if (event === 'thinking') h.onThinking?.(p.text, p.index ?? 0)
      else if (event === 'delta') h.onDelta?.(p.text, p.index ?? 0)
      else if (event === 'resumed') h.onResumed?.(p.index)
      else if (event === 'block_done') h.onBlockDone?.(p.index, p.status, p.chars)
      else if (event === 'notice') h.onNotice?.(p.text)
      else if (event === 'result') result = p
      else if (event === 'fail') throw new Error(p.detail)
    }
  }
  if (result === null) throw new Error('流式运行未返回结果')
  return result
}

export const runsApi = {
  run: (layer: string, body: LayerRunBody) =>
    req<LayerRunResult>(`/api/runs/${layer}`, { method: 'POST', body: JSON.stringify(body) }),
  retry: (layer: string, body: LayerRunBody) =>
    req<LayerRunResult>(`/api/runs/${layer}/retry`, { method: 'POST', body: JSON.stringify(body) }),
  status: (runId: string) => req<LayerRunResult['journal']>(`/api/runs/${runId}`),
  // SSE 流式运行：实时收思考/正文增量，结束返回完整结果（fail 时抛错）
  stream: (layer: string, body: LayerRunBody, h: SSEHandlers): Promise<LayerRunResult> =>
    fetch(`/api/runs/${layer}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => consumeSSE<LayerRunResult>(r, h)),
}

// ---- 小说项目（M3a）----
export interface WizardSummary {
  done: number
  graduated: boolean
  current_station: string | null
  chapters_total: number
  chapters_finalized: number
}
export interface ProjectMeta {
  id: string
  title: string
  intro: string
  kind?: 'own' | 'reference'   // reference = 拆书参考项目（仅借鉴，不写作）
  created_at: string
  updated_at: string
  current?: Record<string, number>
  wizard_summary?: WizardSummary
}
export interface ProjectLayerSummary {
  seq: number
  layer: string
  note: string
  timestamp: string
  y_chars: number
  x_preview: string
}
export interface ProjectDetail extends ProjectMeta {
  layers: Record<string, { current: ProjectLayerSummary | null; history: ProjectLayerSummary[] }>
}
export interface LayerVersionFull {
  seq: number
  layer: string
  x_pairs: [string, string][]
  y: string
  note: string
  timestamp: string
}

export const projectsApi = {
  list: (page?: number, pageSize?: number) =>
    req<{ projects: ProjectMeta[] } & PageMeta>(
      `/api/projects${page != null ? `?page=${page}&page_size=${pageSize ?? 20}` : ''}`,
    ),
  create: (title: string, intro: string) =>
    req<ProjectMeta>('/api/projects', { method: 'POST', body: JSON.stringify({ title, intro }) }),
  detail: (pid: string) => req<ProjectDetail>(`/api/projects/${pid}`),
  update: (pid: string, title: string, intro: string) =>
    req<ProjectMeta>(`/api/projects/${pid}`, { method: 'PUT', body: JSON.stringify({ title, intro }) }),
  remove: (pid: string) =>
    req<{ deleted: string }>(`/api/projects/${pid}`, { method: 'DELETE' }),
  layer: (pid: string, layer: string, seq?: number, page?: number, pageSize?: number) => {
    const q = new URLSearchParams()
    if (seq) q.set('seq', String(seq))
    if (page != null) {
      q.set('page', String(page))
      q.set('page_size', String(pageSize ?? 20))
    }
    const qs = q.toString()
    return req<{ version: LayerVersionFull | null; history: ProjectLayerSummary[] } & PageMeta>(
      `/api/projects/${pid}/layers/${layer}${qs ? `?${qs}` : ''}`,
    )
  },
  adopt: (pid: string, layer: string, y: string, xPairs: [string, string][], note: string) =>
    req<LayerVersionFull>(`/api/projects/${pid}/layers/${layer}`, {
      method: 'POST',
      body: JSON.stringify({ y, x_pairs: xPairs, note }),
    }),
  rollback: (pid: string, layer: string, seq: number) =>
    req<LayerVersionFull>(`/api/projects/${pid}/layers/${layer}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ seq }),
    }),
}

export const LAYERS = ['intro', 'outline', 'plot', 'draft', 'polish'] as const
export const LAYER_LABELS: Record<string, string> = {
  intro: '简介',
  outline: '大纲',
  plot: '剧情',
  draft: '正文',
  polish: '润色',
}

// ---- 实体库（M3b）----
export const ENTITY_TYPES = ['character', 'location', 'item', 'plotline', 'foreshadow'] as const
export type EntityType = (typeof ENTITY_TYPES)[number]
export const ENTITY_TYPE_LABELS: Record<string, string> = {
  character: '角色',
  location: '地点',
  item: '道具',
  plotline: '情节线',
  foreshadow: '伏笔',
}
export const ENTITY_STATUS_LABELS: Record<string, string> = {
  active: '活跃',
  retired: '退场',
  planted: '已埋',
  paid: '已回收',
  dropped: '已弃',
}
export interface EntityInfo {
  id: string
  type: EntityType
  name: string
  summary: string
  details: string
  status: string
  source: string
  appearances: number[]
  created_at: string
  updated_at: string
}
export interface EntitiesSummary {
  project: string
  entities: EntityInfo[]
  counts: Record<string, number>
  total: number
}
export interface ExtractResult extends EntitiesSummary {
  created: EntityInfo[]
  updated: EntityInfo[]
  dropped: { raw: unknown; reason: string }[]
  attempts: number
}
export interface VaultExportResult {
  out_dir: string
  files: number
  chapters: number
  entities: number
  layers_exported: string[]
  warnings: string[]
}

export const entitiesApi = {
  list: (
    pid: string,
    q: { type?: string; status?: string; keyword?: string } = {},
    page?: number,
    pageSize?: number,
  ) => {
    const p = new URLSearchParams()
    Object.entries(q).forEach(([k, v]) => {
      if (v) p.set(k, String(v))
    })
    if (page != null) {
      p.set('page', String(page))
      p.set('page_size', String(pageSize ?? 20))
    }
    const qs = p.toString()
    return req<EntitiesSummary & PageMeta>(`/api/projects/${pid}/entities${qs ? `?${qs}` : ''}`)
  },
  add: (pid: string, body: { type: string; name: string; summary?: string; details?: string; status?: string; chapter?: number }) =>
    req<EntityInfo>(`/api/projects/${pid}/entities`, { method: 'POST', body: JSON.stringify(body) }),
  update: (pid: string, eid: string, body: { name?: string; summary?: string; details?: string; status?: string }) =>
    req<EntityInfo>(`/api/projects/${pid}/entities/${encodeURIComponent(eid)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  remove: (pid: string, eid: string) =>
    req<{ deleted: string }>(`/api/projects/${pid}/entities/${encodeURIComponent(eid)}`, { method: 'DELETE' }),
  extract: (pid: string, text: string, chapter?: number) =>
    req<ExtractResult>(`/api/projects/${pid}/entities/extract`, {
      method: 'POST',
      body: JSON.stringify({ text, chapter }),
    }),
}

export const vaultApi = {
  export: (pid: string, outDir?: string) =>
    req<VaultExportResult>(`/api/projects/${pid}/export/vault`, {
      method: 'POST',
      body: JSON.stringify({ out_dir: outDir || null }),
    }),
}

// ---- 导入拆解（M4）----
export interface ImportPreviewChapter {
  no: number
  title: string
  chars: number
}
export interface ImportTextResult {
  chapters: ImportPreviewChapter[]
  total: number
  adopted: boolean
  draft_version?: number
}
export interface AnalyzeChapterResult {
  chapter: number
  entities: { reused?: boolean; created?: string[]; updated?: string[]; dropped?: number }
  outline: string
  outline_reused: boolean
}
export interface ImportStatusChapter {
  no: number
  title: string
  chars: number
  entities_status: string | null
  outline_status: string | null
  outline_preview: string
  error: string
}
export interface ImportStatus {
  project: string
  chapters: ImportStatusChapter[]
  total: number
  done: number
}

export const importApi = {
  text: (pid: string, text: string, dryRun: boolean) =>
    req<ImportTextResult>(`/api/projects/${pid}/import/text`, {
      method: 'POST',
      body: JSON.stringify({ text, dry_run: dryRun }),
    }),
  analyzeChapter: (pid: string, chapter: number, opts: { text?: string; force?: boolean } = {}) =>
    req<AnalyzeChapterResult>(`/api/projects/${pid}/import/analyze_chapter`, {
      method: 'POST',
      body: JSON.stringify({ chapter, text: opts.text || null, force: opts.force || false }),
    }),
  status: (pid: string, page?: number, pageSize?: number) =>
    req<ImportStatus & PageMeta>(
      `/api/projects/${pid}/import/status${page != null ? `?page=${page}&page_size=${pageSize ?? 50}` : ''}`,
    ),
  adoptOutline: (pid: string) =>
    req<{ seq: number; chapters: number }>(`/api/projects/${pid}/import/adopt_outline`, {
      method: 'POST',
    }),
}

// ---- M5 引导式写作：向导 + 章台 ----
export const STATION_ORDER = ['topic', 'brainstorm', 'architecture', 'plotplan', 'writing', 'polish'] as const
export const STATION_LABELS: Record<string, string> = {
  topic: '选题',
  brainstorm: '脑洞',
  architecture: '故事架构',
  plotplan: '剧情规划',
  writing: '成稿',
  polish: '润色',
}
export interface WizardIdea {
  context: string
  desire: string
  obstacle: string
  text: string
}
export interface WizardData {
  idea: WizardIdea
  seed: string
  direction: string
  direction_source: string
  n_chapters: number
  skipped: string[]
}
export interface StationState {
  station: string
  label: string
  done: boolean
  skipped: boolean
}
export interface WizardDetail {
  project: string
  wizard: WizardData
  stations: StationState[]
  current_station: string | null
  graduated: boolean
  gates_ok: boolean
}
export interface DirectionCard {
  direction: string
  reason: string
  hook: string
  premise: string
}
export interface WizardUpdateBody {
  idea?: Partial<WizardIdea>
  seed?: string
  direction?: string
  direction_source?: string
  n_chapters?: number
}

export const wizardApi = {
  detail: (pid: string) => req<WizardDetail>(`/api/projects/${pid}/wizard`),
  update: (pid: string, body: WizardUpdateBody) =>
    req<{ project: string; wizard: WizardData }>(`/api/projects/${pid}/wizard`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  brainstorm: (pid: string) =>
    req<{ cards: DirectionCard[]; attempts: number }>(`/api/projects/${pid}/wizard/brainstorm`, {
      method: 'POST',
    }),
  skip: (pid: string, station: string) =>
    req<WizardDetail>(`/api/projects/${pid}/wizard/${station}/skip`, { method: 'POST' }),
}

export interface DeskChapter {
  no: number
  title: string
  has_outline: boolean
  plot_done: boolean
  draft_done: boolean
  polish_done: boolean
  review_verdict: string | null
  finalized_at: string
  payoff: string
  chars: number
  outline_preview: string
}
export interface DeskDetail {
  project: string
  chapters: DeskChapter[]
  total: number
  finalized: number
  outline_chapters: number
}
export interface DeskWorkText {
  text: string
  run_id: string
  updated_at: string
}
export interface DeskChapterFull {
  no: number
  title: string
  plot: DeskWorkText
  draft: DeskWorkText
  polish: DeskWorkText
  review: ChapterReview | null
  finalized_at: string
  payoff: string // 本章兑现规模：none/small/medium/climax，空=未记录
}
export interface DeskChapterDetail {
  project: string
  chapter: DeskChapterFull
  outline: string
}
export interface DeskRunResult {
  chapter: number
  stage: string
  y: string
  run_id: string
  resumed_indices: number[]
  calls: { completion_status: string; attempts: number }[]
}
export interface FinalizeResult {
  chapter: number
  chars: number
  entities: {
    created?: EntityInfo[]
    updated?: EntityInfo[]
    dropped?: number
    attempts?: number
    error?: string
  } | null
  foreshadow?: {
    paid?: string[]
    advanced?: string[]
    attempts?: number
    warning?: string
    error?: string
  } | null
  marker_hits: { marker: MarkerInfo; hit: boolean }[]
  draft_version: number
  total_chapters: number
}

export const deskApi = {
  detail: (pid: string, page?: number, pageSize?: number) =>
    req<DeskDetail & PageMeta>(
      `/api/projects/${pid}/desk${page != null ? `?page=${page}&page_size=${pageSize ?? 50}` : ''}`,
    ),
  chapter: (pid: string, chapter: number) =>
    req<DeskChapterDetail>(`/api/projects/${pid}/desk/${chapter}`),
  run: (
    pid: string,
    chapter: number,
    stage: string,
    opts: { directive?: string; force?: boolean; checklist?: boolean; materials?: { source_id: string; family: string; content: string; title?: string }[] } = {},
  ) =>
    req<DeskRunResult>(`/api/projects/${pid}/desk/${chapter}/run`, {
      method: 'POST',
      body: JSON.stringify({
        stage,
        directive: opts.directive || null,
        force: opts.force || false,
        checklist: opts.checklist !== false,
        materials: opts.materials?.length ? opts.materials : null,
      }),
    }),
  // 流式生成：正文增量实时回调，结束返回完整运行结果
  runStream: (
    pid: string,
    chapter: number,
    stage: string,
    opts: { directive?: string; force?: boolean; checklist?: boolean; materials?: { source_id: string; family: string; content: string; title?: string }[] } = {},
    h: SSEHandlers,
  ): Promise<DeskRunResult> =>
    fetch(`/api/projects/${pid}/desk/${chapter}/run/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stage,
        directive: opts.directive || null,
        force: opts.force || false,
        checklist: opts.checklist !== false,
        materials: opts.materials?.length ? opts.materials : null,
      }),
    }).then((r) => consumeSSE<DeskRunResult>(r, h)),
  saveText: (pid: string, chapter: number, stage: string, text: string) =>
    req<{ chapter: number; stage: string; chars: number }>(
      `/api/projects/${pid}/desk/${chapter}/${stage}`,
      { method: 'PUT', body: JSON.stringify({ text }) },
    ),
  review: (pid: string, chapter: number) =>
    req<ChapterReview & { chapter: number }>(`/api/projects/${pid}/desk/${chapter}/review`, {
      method: 'POST',
    }),
  // 兑现四选一：随手记录/修改本章兑现规模（未定稿可先记，定稿后可改）
  setPayoff: (pid: string, chapter: number, payoff: string) =>
    req<{ chapter: number; payoff: string; payoff_label: string }>(
      `/api/projects/${pid}/desk/${chapter}/payoff`,
      { method: 'PUT', body: JSON.stringify({ payoff }) },
    ),
  finalize: (pid: string, chapter: number, autoEntities = true, payoff?: string) =>
    req<FinalizeResult>(`/api/projects/${pid}/desk/${chapter}/finalize`, {
      method: 'POST',
      body: JSON.stringify({ auto_entities: autoEntities, payoff: payoff ?? null }),
    }),
}
