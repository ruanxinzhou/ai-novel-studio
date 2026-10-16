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
  digest_bytes: number
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
  strand_warnings?: string[] // 三线断档/占比/打卡率提示（纯数据检查）
  fix_applied?: boolean // 定点修复已执行（不再强制重审）
  fix_summary?: string
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
  // prev_tail：上一章结尾，给定时作为第七项「跨章联结」的对照材料
  chapter: (outline: string, prose: string, prevTail = '') =>
    req<ChapterReview>('/api/review/chapter', {
      method: 'POST',
      body: JSON.stringify({ outline, prose, prev_tail: prevTail }),
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
  importChaishu: (dir: string, title?: string, tags?: string[], sourceTxt?: string, distill = true) =>
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
      distill?: { status: string; cards?: number; error?: string }
    }>('/api/corpus/import_chaishu', {
      method: 'POST',
      body: JSON.stringify({ dir, title: title ?? '', tags: tags ?? [], source_txt: sourceTxt ?? '', distill }),
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

// ---- 主角（唐迟）档案 ----
export interface ProtagonistState {
  current_body_name: string
  current_body_identity: string
  current_location: string
  current_field_no: number | null
  current_day: number | null
  erosion_level: number
  sanity_hint: string
  held_items: string[]
  active_crafts: Array<{ name: string; proficiency?: string; from_field?: string; note?: string }>
  known_truths: string[]
  key_relations: string[]
  dead_echoes: Array<{ name: string; craft_hint?: string; obsession?: string; fade_stage?: string; from_field?: string }>
  god_marks: Array<{ god: string; seal_form?: string; risk?: string; from_field?: string }>
  brought_items: Array<{ name: string; contam?: number; effect?: string; from_field?: string }>
  blue_star_bonds: Array<{ name: string; relation: string; depth: string; note?: string }>
  kill_record: string[]
  contamination_level: number
  field_count: number
  updated_at: string
  updated_from_chapter: number | null
}

export interface ProtagonistPatch {
  current_body_name?: string
  current_body_identity?: string
  current_location?: string
  sanity_hint?: string
  known_truths?: string[]
  key_relations?: string[]
  blue_star_bonds?: Array<{ name: string; relation: string; depth: string; note?: string }>
  contamination_level?: number
  profile_text?: string
}

export const protagonistApi = {
  get: (pid: string) => req<{ protagonist: ProtagonistState }>(`/api/projects/${pid}/protagonist`),
  patch: (pid: string, body: ProtagonistPatch) =>
    req<{ ok: boolean; protagonist: ProtagonistState }>(`/api/projects/${pid}/protagonist`, {
      method: 'PATCH',
      body: JSON.stringify(body),
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

// ---- 创作会话（P5：每月规划 + 日更对话）----
export interface SessionMeta {
  id: string
  title: string
  scope: 'field' | 'chapter'
  field_no?: number
  chapter?: number
  current_phase: string
  status: 'active' | 'completed' | 'abandoned'
  updated_at: string
  can_advance: boolean
}
export interface SessionMessage {
  id: string
  role: 'author' | 'ai' | 'system'
  kind: string
  text: string
  meta: Record<string, unknown>
  edited_from?: string | null
  created_at: string
}
export interface Candidate {
  id: string
  phase: string
  card_key: string
  content: string
  axis_label: string
  axis_dimension: string
  rationale: string
  created_at: string
}
export interface EditingState {
  candidate_id: string | null
  card_key: string
  current_text: string
  started_at: string
  edit_round: number
  last_suggestion_msg_id: string | null
  selected_fork: string | null
}
export interface SessionState {
  id: string
  project_id: string
  scope: 'field' | 'chapter'
  field_no?: number | null
  chapter?: number | null
  title: string
  current_phase: string
  outputs: Record<string, unknown>
  messages: SessionMessage[]
  status: 'active' | 'completed' | 'abandoned'
  created_at: string
  updated_at: string
  artifacts: Record<string, unknown>
  candidates?: Candidate[]
  editing?: EditingState | null
  locked_cards?: Record<string, string>
  source_material?: {
    kind?: string
    premise?: string
    renamed_cast?: unknown[]
    changed_elements?: string[]
    risk_flags?: string[]
    original_excerpt?: string
    original_chars?: number
    filename?: string
    adapted_at?: string
    attempts?: number
  } | null
  phase_meta: {
    title: string
    prompt: string
    ai_mode: string
    next_phase: string | null
    required_outputs: Record<string, string>
  }
  missing_outputs: string[]
  can_advance: boolean
}
export interface KindleVariant {
  vid: string
  title: string
  flavor?: string
  flavor_hook?: string
  one_liner: string
  ending_label?: string
  core_twist?: string
  extra_npcs?: (string | { name?: string })[]
  final_san: number
  final_hp: number
  turns: number
  final_summary: string
}

export const sessionsApi = {
  list: (pid: string, filter: { scope?: string; field_no?: number; chapter?: number; status?: string } = {}) => {
    const p = new URLSearchParams()
    Object.entries(filter).forEach(([k, v]) => {
      if (v != null) p.set(k, String(v))
    })
    const qs = p.toString()
    return req<{ sessions: SessionMeta[] }>(`/api/projects/${pid}/sessions${qs ? `?${qs}` : ''}`)
  },
  create: (pid: string, body: { scope: 'field' | 'chapter'; field_no?: number; chapter?: number; title?: string; start_phase?: string }) =>
    req<{ ok: true; session: SessionState }>(`/api/projects/${pid}/sessions`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  get: (pid: string, sid: string) =>
    req<{ session: SessionState }>(`/api/projects/${pid}/sessions/${sid}`),
  send: (pid: string, sid: string, body: { text: string; kind?: string; as_ai?: boolean; output_key?: string; meta?: Record<string, unknown> }) =>
    req<{ ok: true; session: SessionState; message: SessionMessage }>(
      `/api/projects/${pid}/sessions/${sid}/send`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  gate: (pid: string, sid: string, key: string, value: unknown) =>
    req<{ ok: true; session: SessionState; can_advance: boolean; missing: string[] }>(
      `/api/projects/${pid}/sessions/${sid}/gate`,
      { method: 'POST', body: JSON.stringify({ key, value }) },
    ),
  advance: (pid: string, sid: string) =>
    req<{ ok: true; session: SessionState }>(`/api/projects/${pid}/sessions/${sid}/advance`, { method: 'POST' }),
  rollback: (pid: string, sid: string, to_phase: string) =>
    req<{ ok: true; session: SessionState }>(`/api/projects/${pid}/sessions/${sid}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ to_phase }),
    }),
  abandon: (pid: string, sid: string) =>
    req<{ ok: true; session: SessionState }>(`/api/projects/${pid}/sessions/${sid}`, { method: 'DELETE' }),
  action: (pid: string, sid: string, body: { action: string; args?: Record<string, unknown>; n_variants?: number }) =>
    req<{ ok: true; session: SessionState; variants?: KindleVariant[]; options?: string[] }>(
      `/api/projects/${pid}/sessions/${sid}/action`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  // ---- 回转寿司（M2）----
  generateCandidates: (
    pid: string,
    sid: string,
    body: {
      card_key: string
      phase: string
      axis_label?: string
      axis_dimension?: string
      n?: number
      context_hint?: string
    },
  ) =>
    req<{
      ok: true
      session: SessionState
      candidates: Candidate[]
      stub: boolean
      axis_label: string
      axis_dimension: string
    }>(`/api/projects/${pid}/sessions/${sid}/generate-candidates`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  pickCandidate: (
    pid: string,
    sid: string,
    body: { candidate_id?: string | null; card_key: string; initial_text?: string | null },
  ) =>
    req<{ ok: true; session: SessionState; editing: EditingState }>(
      `/api/projects/${pid}/sessions/${sid}/pick-candidate`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  editingUpdate: (pid: string, sid: string, text: string) =>
    req<{ ok: true; session: SessionState }>(`/api/projects/${pid}/sessions/${sid}/editing/update`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  editingEnd: (pid: string, sid: string) =>
    req<{ ok: true; session: SessionState }>(`/api/projects/${pid}/sessions/${sid}/editing/end`, {
      method: 'POST',
    }),
  editingAiSuggest: (
    pid: string,
    sid: string,
    body: { instruction: string; apply?: boolean },
  ) =>
    req<{
      ok: true
      session: SessionState
      message_id: string
      suggested_text: string
      reason: string
      applied: boolean
    }>(`/api/projects/${pid}/sessions/${sid}/editing/ai-suggest`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  lockCard: (
    pid: string,
    sid: string,
    body: {
      card_key: string
      text: string
      scope?: string
      field_no?: number | null
      chapter?: number | null
      source_msg_id?: string | null
      note?: string
    },
  ) =>
    req<{
      ok: true
      session: SessionState
      locked_ref: Record<string, unknown>
      diff_id: string | null
    }>(`/api/projects/${pid}/sessions/${sid}/lock`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  // ---- 素材上传（跑团 PDF 版权规避改编）----
  uploadSourcePdf: (pid: string, sid: string, file: File, userHint = '',
                    onStage?: (stage: string) => void) => {
    const fd = new FormData()
    fd.append('file', file)
    if (userHint) fd.append('user_hint', userHint)
    // XHR 支持上传进度（fetch 不支持 upload progress）
    return new Promise<{
      ok: true
      session: SessionState
      adapt: {
        premise: string
        renamed_cast: unknown[]
        changed_elements: string[]
        risk_flags: string[]
        original_excerpt: string
        original_chars: number
        filename: string
        attempts: number
      }
    }>((resolve, reject) => {
      onStage?.('正在上传 PDF…')
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `/api/projects/${pid}/sessions/${sid}/source-pdf`)
      // 4 分钟客户端超时：后端单次 LLM 调用上限 300s，改编最多重试 2 次，
      // 正常情况下 60-180s 就会回，4 分钟兜底避免用户无限等。
      const timeoutMs = 4 * 60 * 1000
      const timer = window.setTimeout(() => {
        xhr.abort()
        reject(new Error(`改编超时（>${Math.round(timeoutMs / 1000)}s），可能是大模型排队；可刷新页面后重试，或换一个小一点的 PDF。`))
      }, timeoutMs)
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100)
          onStage?.(`正在上传 PDF… ${pct}%`)
        }
      }
      xhr.onload = () => {
        window.clearTimeout(timer)
        if (xhr.status === 200) {
          try {
            resolve(JSON.parse(xhr.responseText))
          } catch (err) {
            reject(new Error(`解析响应失败：${err}`))
          }
        } else {
          let detail = `${xhr.status} ${xhr.statusText}`
          try {
            const d = JSON.parse(xhr.responseText || '{}')
            if (d?.detail) detail = d.detail
          } catch { /* ignore */ }
          reject(new Error(detail))
        }
      }
      xhr.onerror = () => {
        window.clearTimeout(timer)
        reject(new Error('网络错误，上传失败'))
      }
      xhr.onabort = () => {
        window.clearTimeout(timer)
        reject(new Error('上传已中断'))
      }
      // 上传完成后服务端正在抽文本/调 LLM，切阶段提示
      xhr.upload.onload = () => {
        onStage?.('上传完成，正在抽取文本…')
        // 5 秒后切换到"AI 改编中"提示，让用户知道还在工作
        window.setTimeout(() => onStage?.('AI 正在做版权规避改编（正常需 1-3 分钟，请稍候）…'), 5000)
        window.setTimeout(() => onStage?.('仍在改编中，长文档可能需要 2-3 分钟…'), 60000)
        window.setTimeout(() => onStage?.('快好了，模型在输出改编稿…'), 150000)
      }
      xhr.send(fd)
    })
  },
}

// ---- 后台长任务（jobs：PDF 5 版 / COC 批量模拟 / 大纲 5 版）----
export type JobKind = 'pdf5' | 'coc_batch' | 'outline5' | 'embody_outline5' | 'sushi' | 'scaffold_prefill'
export type JobStatus = 'pending' | 'running' | 'done' | 'error' | 'cancelled'

export interface Pdf5Variant {
  vid?: string
  title?: string
  one_liner?: string
  premise?: string
  player_cards?: unknown[]
  changed_elements?: string[]
  risk_flags?: string[]
  flavor?: string
  flavor_hook?: string
}
export interface CocDayBreakdown {
  day?: number | string
  summary?: string
  title?: string
}
export interface CocVariant {
  label?: string            // 同PL好运 / 同PL厄运 / 异PL反转
  one_liner?: string
  premise?: string
  day_breakdown?: CocDayBreakdown[]
  final_san?: number
  final_hp?: number
  turns?: number
  final_summary?: string
}
export interface Outline5Variant {
  label?: string
  one_liner?: string
  tangchi_entry?: string
  climax?: string
  day_breakdown?: CocDayBreakdown[]
  truth?: string
}

export interface Job {
  job_id: string
  kind: JobKind
  status: JobStatus
  progress: number       // 0-100
  message: string
  meta?: Record<string, unknown>
  sub_results?: unknown[]
  /** 完成后的结果：pdf5 → Pdf5Variant[]；coc_batch → CocVariant[]；outline5 → Outline5Variant[] */
  result?: Pdf5Variant[] | CocVariant[] | Outline5Variant[] | unknown
  error?: string | null
  created_at?: string
  updated_at?: string
}

export interface StartJobResp {
  job_id: string
  job: Job
}

export const jobsApi = {
  /** 启动 COC 三场批量模拟 */
  startCoc: (
    pid: string,
    sid: string,
    body: {
      tangchi_replace_idx?: number | null
      user_directive?: string
      max_pairs_per_session?: number
      max_turns_per_session?: number
    },
  ) =>
    req<StartJobResp>(`/api/projects/${pid}/sessions/${sid}/jobs/coc`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** 启动唐迟大纲 5 版 */
  startOutline5: (
    pid: string,
    sid: string,
    body: {
      tangchi_role?: string
      must_have?: string
      user_directive?: string
      selected_coc_idx?: number | null
      n?: number
    },
  ) =>
    req<StartJobResp>(`/api/projects/${pid}/sessions/${sid}/jobs/outline5`, {
      method: 'POST',
      body: JSON.stringify({ n: 5, ...body }),
    }),

  /** 列出会话下所有 jobs（用于进入页面时一次性同步状态） */
  list: (pid: string, sid: string) =>
    req<{ jobs: Job[] }>(`/api/projects/${pid}/sessions/${sid}/jobs`),

  /** 选中某一版，写入 session */
  pick: (pid: string, sid: string, jobId: string, pickIndex: number) =>
    req<{ ok: true; job: Job }>(`/api/projects/${pid}/sessions/${sid}/jobs/${jobId}/pick`, {
      method: 'POST',
      body: JSON.stringify({ pick_index: pickIndex }),
    }),

  /** 取消一个 pending/running 的 job */
  cancel: (pid: string, sid: string, jobId: string) =>
    req<{ ok: true; job: Job }>(`/api/projects/${pid}/sessions/${sid}/jobs/${jobId}/cancel`, {
      method: 'POST',
    }),

  /**
   * PDF 上传 5 版改编（multipart/form-data）
   * 字段：file, user_hint, n=5
   * onProgress 可选：上传阶段 0-100（仅 xhr.upload，服务端生成中进度由 SSE 返回）
   */
  uploadPdf5: (
    pid: string,
    sid: string,
    file: File,
    userHint = '',
    onProgress?: (pct: number) => void,
  ) => {
    const fd = new FormData()
    fd.append('file', file)
    if (userHint) fd.append('user_hint', userHint)
    fd.append('n', '5')
    return new Promise<StartJobResp>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `/api/projects/${pid}/sessions/${sid}/jobs/pdf5`)
      // 15 分钟兜底：PDF → 5 版改编串行跑，模型慢时可能较长
      const timeoutMs = 15 * 60 * 1000
      const timer = window.setTimeout(() => {
        xhr.abort()
        reject(new Error(`提交超时（>${Math.round(timeoutMs / 1000)}s），请刷新页面查看任务列表或重试。`))
      }, timeoutMs)
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }
      xhr.onload = () => {
        window.clearTimeout(timer)
        if (xhr.status === 200) {
          try { resolve(JSON.parse(xhr.responseText)) }
          catch (err) { reject(new Error(`解析响应失败：${err}`)) }
        } else {
          let detail = `${xhr.status} ${xhr.statusText}`
          try {
            const d = JSON.parse(xhr.responseText || '{}')
            if (d?.detail) detail = d.detail
          } catch { /* ignore */ }
          reject(new Error(detail))
        }
      }
      xhr.onerror = () => {
        window.clearTimeout(timer)
        reject(new Error('网络错误，上传失败'))
      }
      xhr.onabort = () => {
        window.clearTimeout(timer)
        reject(new Error('上传已中断'))
      }
      xhr.send(fd)
    })
  },

  /**
   * 订阅 jobs SSE 流（EventSource）
   * 事件类型：job_update（单 job 更新）、job_init（初次批量 jobs 列表）、job_idle（空闲）
   * 返回 { close } 用于卸载时关闭连接。
   * 注意：EventSource 不支持自定义 header；若后端需要 auth token，可通过 query 参数或 cookie 带。
   */
  subscribe: (
    pid: string,
    sid: string,
    onEvent: (ev: { type: 'job_update' | 'job_init' | 'job_idle'; job?: Job; jobs?: Job[] }) => void,
  ) => {
    const url = `/api/projects/${pid}/sessions/${sid}/jobs/stream`
    const es = new EventSource(url)
    const handle = (type: 'job_update' | 'job_init' | 'job_idle') => (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (type === 'job_init') onEvent({ type, jobs: Array.isArray(data) ? data : data.jobs })
        else if (type === 'job_update') onEvent({ type, job: data as Job })
        else onEvent({ type })
      } catch { /* ignore parse errors */ }
    }
    es.addEventListener('job_update', handle('job_update'))
    es.addEventListener('job_init', handle('job_init'))
    es.addEventListener('job_idle', handle('job_idle'))
    es.onerror = () => { /* SSE 断连浏览器会自动重连，这里仅静默 */ }
    return {
      close: () => {
        es.close()
      },
    }
  },
}

export const JOB_KIND_LABELS: Record<string, string> = {
  pdf_adapt5: 'PDF 5版改编',
  coc_batch: 'COC 三场模拟',
  outline5: '唐迟大纲 5版',
}

export const PHASE_TITLES: Record<string, string> = {
  setup: '开书设定',
  adapt: '剧本改编',
  adapt_empty: '新卷待改',
  simulate: '模拟跑团',
  embody: '魂穿设计',
  scaffold: '卷内详设',
  rhythm: '节奏骨架',
  briefing: '章前规划',
  desk: '日更',
  settle: '杀青归账',
  interlude: '蓝星间章',
}
export const AI_MODE_LABELS: Record<string, string> = {
  review: '列账',
  diverge: '发散',
  challenge: '挑刺',
  narrate: '朗读',
  plan: '排期',
  silent: '闭嘴',
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
  volume: string // 所属卷名（大纲「第N卷 卷名」行），空=未分卷
  has_outline: boolean
  plot_done: boolean
  draft_done: boolean
  polish_done: boolean
  review_verdict: string | null
  finalized_at: string
  payoff: string
  strands: string[] // 本章推进的线：quest/fire/bond 多选，空=未记录
  chars: number
  outline_preview: string
}
export interface DeskVolume {
  title: string // 空字符串 = 卷头出现前的无名首组
  note: string // 卷头到下一章之间的零散引言
  chapters: number[]
}
export interface DeskDetail {
  project: string
  chapters: DeskChapter[]
  total: number
  finalized: number
  outline_chapters: number
  volumes: DeskVolume[]
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
  strands: string[] // 本章推进的线：quest/fire/bond 多选，空=未记录
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
  checklist_injected?: boolean
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
    opts: { directive?: string; force?: boolean; checklist?: boolean; materials?: { source_id: string; family: string; content: string; title?: string }[]; modules?: string[]; fullModules?: boolean } = {},
  ) =>
    req<DeskRunResult>(`/api/projects/${pid}/desk/${chapter}/run`, {
      method: 'POST',
      body: JSON.stringify({
        stage,
        directive: opts.directive || null,
        force: opts.force || false,
        checklist: opts.checklist !== false,
        materials: opts.materials?.length ? opts.materials : null,
        modules: opts.modules ?? null,
        full_modules: opts.fullModules || false,
      }),
    }),
  // 流式生成：正文增量实时回调，结束返回完整运行结果
  runStream: (
    pid: string,
    chapter: number,
    stage: string,
    opts: { directive?: string; force?: boolean; checklist?: boolean; materials?: { source_id: string; family: string; content: string; title?: string }[]; modules?: string[]; fullModules?: boolean } = {},
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
        modules: opts.modules ?? null,
        full_modules: opts.fullModules || false,
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
  // 定点修复：只修审稿不稳项，其余一字不动；结果写回来源阶段（润色优先），不强制重审
  fixStream: (pid: string, chapter: number, h: SSEHandlers) =>
    fetch(`/api/projects/${pid}/desk/${chapter}/fix/stream`, { method: 'POST' })
      .then((r) => consumeSSE<{ y: string; stage: string; fixed: number }>(r, h)),
  // 三线打卡：记录本章推进了 主线/冲突/羁绊 哪些线（开章任务书与审稿报警都吃这份账）
  setStrands: (pid: string, chapter: number, strands: string[]) =>
    req<{ chapter: number; strands: string[]; strand_labels: string[] }>(
      `/api/projects/${pid}/desk/${chapter}/strands`,
      { method: 'PUT', body: JSON.stringify({ strands }) },
    ),
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

// ---- 工作台 adapt 阶段：PDF 上传 → 5 版并行改编 → 选版 ----

export interface AdaptStartResp {
  ok: boolean
  job_id: string
  job: Job
}

export const adaptApi = {
  /** 获取卷详情（走 workbench 路径） */
  getVolume: (pid: string, volNo: number) =>
    req<{ volume: any }>(`/api/projects/${pid}/workbench/volumes/${volNo}`),

  /** 创建新副本卷（dungeon），title 可空 */
  createVolume: (pid: string, title = '') =>
    req<{ ok: boolean; volume_no: number; volume: any }>(
      `/api/projects/${pid}/workbench/volumes`,
      { method: 'POST', body: JSON.stringify({ title }) },
    ),

  /** multipart 上传 PDF 并启动 n 版并行改编 */
  startPdf5: (pid: string, volNo: number, file: File, userHint: string, n: number) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('user_hint', userHint)
    fd.append('n', String(n))
    return fetch(`/api/projects/${pid}/workbench/volumes/${volNo}/adapt/pdf5`, {
      method: 'POST',
      body: fd,
    }).then(async (r) => {
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}))
        throw new Error(detail.detail ?? `${r.status} ${r.statusText}`)
      }
      return r.json() as Promise<AdaptStartResp>
    })
  },

  /** 列出该卷下所有 adapt jobs */
  listJobs: (pid: string, volNo: number) =>
    req<{ jobs: Job[] }>(`/api/projects/${pid}/workbench/volumes/${volNo}/adapt/jobs`),

  /** 订阅 adapt SSE 流，监听 job_update / job_update_batch / job_idle */
  subscribeJobs: (
    pid: string,
    volNo: number,
    onEvent: (ev: {
      type: 'job_update' | 'job_update_batch' | 'job_idle'
      job?: Job
      jobs?: Job[]
    }) => void,
  ) => {
    const url = `/api/projects/${pid}/workbench/volumes/${volNo}/adapt/stream`
    const es = new EventSource(url)
    const handle = (type: 'job_update' | 'job_update_batch' | 'job_idle') => (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (type === 'job_update') onEvent({ type, job: data as Job })
        else if (type === 'job_update_batch') onEvent({ type, jobs: Array.isArray(data) ? data : [] })
        else onEvent({ type })
      } catch { /* ignore parse errors */ }
    }
    es.addEventListener('job_update', handle('job_update'))
    es.addEventListener('job_update_batch', handle('job_update_batch'))
    es.addEventListener('job_idle', handle('job_idle'))
    es.onerror = () => { /* 浏览器自动重连，静默 */ }
    return {
      close: () => { es.close() },
    }
  },

  /** 选定一版，写入 volume.adapted_module 并标记 adapt 完成 */
  pickAdapt: (pid: string, volNo: number, jobId: string, pickIndex: number, extraEdits = '') =>
    req<{ ok: boolean; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/adapt/pick`,
      { method: 'POST', body: JSON.stringify({ job_id: jobId, pick_index: pickIndex, extra_edits: extraEdits }) },
    ),

  /** 直接保存自定义改编（不走 job 选版） */
  saveCustomAdapt: (pid: string, volNo: number, adaptation: Record<string, unknown>) =>
    req<{ ok: boolean; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/adapt/save-custom`,
      { method: 'POST', body: JSON.stringify({ adaptation }) },
    ),

  /** 通用阶段完成标记 */
  markStageComplete: (pid: string, volNo: number, stage: string) =>
    req<{ ok: boolean; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/stage-complete`,
      { method: 'POST', body: JSON.stringify({ stage }) },
    ),

  /** PATCH 卷（支持 simulated_run=null 清空重跑） */
  patchVolume: (pid: string, volNo: number, body: Record<string, unknown>) =>
    req<{ ok: boolean; volume: any }>(
      `/api/projects/${pid}/volumes/${volNo}`,
      { method: 'PATCH', body: JSON.stringify(body) },
    ),
}

// ---- 工作台 simulate 阶段：3 场并行 COC → 单选/拼贴 ----

export interface SimStartBody {
  user_directive?: string
  max_pairs_per_session?: number
  max_turns_per_session?: number
  tangchi_replace_idx?: number | null
}

export interface SimVariant {
  label?: string
  index?: number
  flavor?: string
  one_liner?: string
  full_log?: string
  [k: string]: unknown
}

export interface SimCollageSelection {
  section: string
  from_index: number
  note?: string
}

export const simulateApi = {
  /** 复用 adaptApi.getVolume */
  getVolume: adaptApi.getVolume,

  /** 清空 simulated_run 以重跑 */
  resetRun: (pid: string, volNo: number) =>
    adaptApi.patchVolume(pid, volNo, { simulated_run: null }),

  /** 启动 3 场并行 COC 仿真 */
  startSim: (pid: string, volNo: number, body: SimStartBody) =>
    req<{ ok: boolean; job_id: string; job: Job }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/simulate/start`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** 列出该卷下所有 simulate jobs */
  listJobs: (pid: string, volNo: number) =>
    req<{ jobs: Job[] }>(`/api/projects/${pid}/workbench/volumes/${volNo}/simulate/jobs`),

  /** 订阅 simulate SSE 流 */
  subscribeJobs: (
    pid: string,
    volNo: number,
    onEvent: (ev: {
      type: 'job_update' | 'job_update_batch' | 'job_idle'
      job?: Job
      jobs?: Job[]
    }) => void,
  ) => {
    const url = `/api/projects/${pid}/workbench/volumes/${volNo}/simulate/stream`
    const es = new EventSource(url)
    const handle = (type: 'job_update' | 'job_update_batch' | 'job_idle') => (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (type === 'job_update') onEvent({ type, job: data as Job })
        else if (type === 'job_update_batch') onEvent({ type, jobs: Array.isArray(data) ? data : [] })
        else onEvent({ type })
      } catch { /* ignore parse errors */ }
    }
    es.addEventListener('job_update', handle('job_update'))
    es.addEventListener('job_update_batch', handle('job_update_batch'))
    es.addEventListener('job_idle', handle('job_idle'))
    es.onerror = () => { /* 静默，浏览器自动重连 */ }
    return {
      close: () => { es.close() },
    }
  },

  /** 单选一场作为主线 */
  pickOne: (pid: string, volNo: number, jobId: string, pickIndex: number) =>
    req<{ ok: boolean; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/simulate/pick-one`,
      { method: 'POST', body: JSON.stringify({ job_id: jobId, pick_index: pickIndex }) },
    ),

  /** 拼贴合成 */
  collage: (
    pid: string,
    volNo: number,
    body: {
      job_id: string
      collage: {
        base_index: number
        selections: SimCollageSelection[]
        user_note?: string
      }
    },
  ) =>
    req<{ ok: boolean; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/simulate/collage`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** 直接保存自定义跑团 variant（不走 job 选版） */
  saveCustomVariant: (pid: string, volNo: number, variant: Record<string, unknown>) =>
    req<{ ok: boolean; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/simulate/save-custom`,
      { method: 'POST', body: JSON.stringify({ variant }) },
    ),
}

// ---- 工作台 embody 阶段（唐迟魂穿 4 步）----

export interface EmbodyCandidate {
  id: string
  name: string
  role?: string
  age?: number
  body_state?: string
  struggle_hook?: string
  san_loss?: string
  death_countdown?: string
  tag?: string
  [k: string]: unknown
}

export interface EmbodyEntryPoint {
  id: string
  name: string
  description?: string
  info_gap?: string
  time_anchor?: string
  [k: string]: unknown
}

export interface EmbodyStance {
  id: string
  name: string
  description?: string
  cost?: string
  tendency?: string
  [k: string]: unknown
}

export interface EmbodyOutline {
  index: number
  title: string
  one_liner?: string
  pacing?: string
  tangchi_entry?: string
  flavor?: string
  climax?: string
  blue_star_hook?: string
  foreshadow_planted?: string[]
  day_breakdown?: Array<{ day: number; summary: string }>
  outline_text?: string
  [k: string]: unknown
}

export const embodyApi = {
  /** 复用 adaptApi.getVolume / patchVolume */
  getVolume: adaptApi.getVolume,
  patchVolume: adaptApi.patchVolume,

  /** Step 1：列魂穿候选 */
  candidates: (pid: string, volNo: number) =>
    req<{ candidates: EmbodyCandidate[] }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/embody/candidates`,
    ),

  /** Step 2：根据候选生成 3 个切入点 */
  entryPoints: (pid: string, volNo: number, body: { candidate_id?: string; candidate?: EmbodyCandidate | null }) =>
    req<{ entry_points: EmbodyEntryPoint[] }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/embody/entry-points`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** Step 3：根据候选+切入点生成 3 个立场 */
  stances: (pid: string, volNo: number, body: { candidate?: EmbodyCandidate | null; entry_point?: EmbodyEntryPoint | null }) =>
    req<{ stances: EmbodyStance[] }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/embody/stances`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** Step 4：启动 5 版大纲并行生成 */
  generateOutline: (
    pid: string,
    volNo: number,
    body: {
      candidate?: EmbodyCandidate | null
      entry_point?: EmbodyEntryPoint | null
      stance?: EmbodyStance | null
      user_directive?: string
      n?: number
    },
  ) =>
    req<{ ok: boolean; job_id: string; job: Job }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/embody/generate-outline`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  /** 列出该卷 embody jobs */
  listJobs: (pid: string, volNo: number) =>
    req<{ jobs: Job[] }>(`/api/projects/${pid}/workbench/volumes/${volNo}/embody/jobs`),

  /** 订阅 embody SSE 流 */
  subscribeJobs: (
    pid: string,
    volNo: number,
    onEvent: (ev: {
      type: 'job_update' | 'job_update_batch' | 'job_idle'
      job?: Job
      jobs?: Job[]
    }) => void,
  ) => {
    const url = `/api/projects/${pid}/workbench/volumes/${volNo}/embody/stream`
    const es = new EventSource(url)
    const handle = (type: 'job_update' | 'job_update_batch' | 'job_idle') => (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (type === 'job_update') onEvent({ type, job: data as Job })
        else if (type === 'job_update_batch') onEvent({ type, jobs: Array.isArray(data) ? data : [] })
        else onEvent({ type })
      } catch { /* ignore */ }
    }
    es.addEventListener('job_update', handle('job_update'))
    es.addEventListener('job_update_batch', handle('job_update_batch'))
    es.addEventListener('job_idle', handle('job_idle'))
    es.onerror = () => { /* 静默 */ }
    return { close: () => { es.close() } }
  },

  /** 选版落盘，写入 host_character / entry_point / stance / tangchi_story_outline 并标记阶段完成 */
  pickOutline: (
    pid: string,
    volNo: number,
    body: {
      job_id?: string
      pick_index?: number
      candidate?: EmbodyCandidate | null
      entry_point?: EmbodyEntryPoint | null
      stance?: EmbodyStance | null
      custom_data?: Record<string, unknown> | null
    },
  ) =>
    req<{ ok: boolean; volume: any; picked: EmbodyOutline }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/embody/pick-outline`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
}

// ---- 副本详设 6 卡（Scaffold）----

export interface DeathNode {
  day: number
  who: string
  how: string
  tease?: string
  cost?: string
}
export interface ClimaxNode {
  day: number
  title: string
  beat: string
  weight: number
}
export interface ClueNode {
  day: number
  where: string
  what: string
  leads_to?: string
  is_red_herring?: boolean
}
export interface ScaffoldPayload {
  death_nodes?: DeathNode[][]
  climax_nodes?: ClimaxNode[][]
  clue_nodes?: ClueNode[][]
  core_twist?: string[]
  tangchi_start?: string[]
  cost_cap?: string[]
  is_mock?: boolean
}

export const scaffoldApi = {
  getVolume: adaptApi.getVolume,
  patchVolume: adaptApi.patchVolume,

  prefill: (pid: string, volNo: number, opts?: { force?: boolean; directive?: string }) =>
    req<{ ok: boolean; job_id: string; job: Job }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/scaffold/prefill`,
      { method: 'POST', body: JSON.stringify({ force: !!opts?.force, directive: opts?.directive || '' }) },
    ),

  /** 从魂穿产物一键生成 6 卡×3 版并默认锁 A 版；返回 {ok, payload, scaffold_cards, volume, all_locked_prefilled} */
  deriveFromEmbody: (pid: string, volNo: number, opts?: { force?: boolean; directive?: string }) =>
    req<{ ok: boolean; is_mock?: boolean; locked_default?: string; payload?: ScaffoldPrefillResult; scaffold_cards?: Record<string, any>; volume?: any; all_locked_prefilled?: boolean }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/scaffold/derive-from-embody`,
      { method: 'POST', body: JSON.stringify({ force: !!opts?.force, directive: opts?.directive || '' }) },
    ),

  /** 单卡🔄重抽：追加 2 个差异化变体到该卡 variants（该卡必须未锁定） */
  rerollCard: (pid: string, volNo: number, cardKey: string, opts?: { directive?: string }) =>
    req<{ ok: boolean; card_key: string; new_variants: any[]; variant_count: number; is_mock?: boolean; volume?: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/scaffold/reroll-card/${encodeURIComponent(cardKey)}`,
      { method: 'POST', body: JSON.stringify({ directive: opts?.directive || '' }) },
    ),

  listJobs: (pid: string, volNo: number) =>
    req<{ jobs: Job[] }>(`/api/projects/${pid}/workbench/volumes/${volNo}/scaffold/jobs`),

  /** SSE 订阅 prefill 任务：job_update / job_update_batch / job_idle */
  subscribeJobs: (
    pid: string,
    volNo: number,
    onEvent: (ev: {
      type: 'job_update' | 'job_update_batch' | 'job_idle'
      job?: Job
      jobs?: Job[]
    }) => void,
  ) => {
    const url = `/api/projects/${pid}/workbench/volumes/${volNo}/scaffold/stream`
    const es = new EventSource(url)
    const handle = (type: 'job_update' | 'job_update_batch' | 'job_idle') => (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        if (type === 'job_update') onEvent({ type, job: data as Job })
        else if (type === 'job_update_batch') onEvent({ type, jobs: Array.isArray(data) ? data : [] })
        else onEvent({ type })
      } catch { /* ignore */ }
    }
    es.addEventListener('job_update', handle('job_update'))
    es.addEventListener('job_update_batch', handle('job_update_batch'))
    es.addEventListener('job_idle', handle('job_idle'))
    es.onerror = () => { /* 静默 */ }
    return { close: () => { es.close() } }
  },

  lockCard: (pid: string, volNo: number, card_key: string, opts: { pick_index?: number; custom_value?: unknown[] | string | null }) =>
    req<{ ok: boolean; card_key: string; picked_idx: number; locked_at: string; all_locked?: boolean; volume?: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/scaffold/lock-card`,
      { method: 'POST', body: JSON.stringify({ card_key, pick_index: opts.pick_index ?? -1, custom_value: opts.custom_value ?? null }) },
    ),

  finalize: (pid: string, volNo: number) =>
    req<{ ok: boolean; stage_completed: Record<string, boolean> }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/scaffold/finalize`,
      { method: 'POST', body: JSON.stringify({}) },
    ),
}

// ============================================================
// 节奏骨架（rhythm）
// ============================================================
export interface RhythmBeat {
  day: number
  intensity: number
  label: string
}
export interface RhythmWarning {
  type: 'dearth' | 'cluster' | 'front_load' | 'back_load' | string
  day?: number
  msg: string
}
export interface RhythmChapterPlan {
  chapter: number
  day: number
  focus: string
  words: number
  beat_type: string
}
export interface RhythmBlueprint {
  pacing_ratio: Record<string, number>
  key_beats_progress: RhythmBeat[]
  warnings: RhythmWarning[]
  suggested_length: number
  suggested_chapter_range: [number, number]
  chapter_plan: RhythmChapterPlan[]
  notes: string
}
export interface DensityMap {
  // day(1~7) → { death, climax, clue, total }
  [day: number]: { death: number; climax: number; clue: number; total: number }
}

export const rhythmApi = {
  getVolume: adaptApi.getVolume,
  analyze: (pid: string, volNo: number, opts?: { rhythm_directive?: string }) =>
    req<{ ok: boolean; blueprint: RhythmBlueprint; density_map: DensityMap; is_mock?: boolean }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/rhythm/analyze`,
      { method: 'POST', body: JSON.stringify({ rhythm_directive: opts?.rhythm_directive || '' }) },
    ),
  get: (pid: string, volNo: number) =>
    req<{ volume: any; blueprint: RhythmBlueprint | null; density_map: DensityMap }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/rhythm`,
    ),
  finalize: (pid: string, volNo: number) =>
    req<{ ok: boolean; stage_completed: Record<string, boolean>; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/rhythm/finalize`,
      { method: 'POST', body: JSON.stringify({}) },
    ),
}

// ============================================================
// 章前 5 分钟方向卡（brief）
// ============================================================
export interface BriefOption {
  id: string
  title: string
  summary: string
  hooks?: string
  day?: number
  beat_type?: string
  foreshadow_advance?: string[]
  entities_on_stage?: string[]
  is_custom?: boolean
}

export const briefApi = {
  options: (
    pid: string,
    volNo: number,
    cno: number,
    body: { directive?: string | null; base_index?: number | null; custom_writing?: string | null; story_day_override?: number | null },
  ) =>
    req<{ options: BriefOption[]; day: number; chapter_words_estimate: number; is_mock?: boolean; cross_day?: boolean; story_day_override?: number | null }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/chapters/${cno}/brief/options`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  select: (
    pid: string,
    volNo: number,
    cno: number,
    body: { option_index: number; extra_edits?: string | null; custom_text?: string | null; story_day_override?: number | null },
  ) =>
    req<{ ok: boolean; brief: any; chapter_no: number }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/chapters/${cno}/brief/select`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  advance: (
    pid: string,
    volNo: number,
    cno: number,
    body?: { finalized?: boolean; force_volume_end?: boolean | null },
  ) =>
    req<{ next_chapter: number | null; phase: string; at_end?: boolean; auto_at_end?: boolean; force_state?: string }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/chapters/${cno}/brief/advance`,
      { method: 'POST', body: JSON.stringify({ finalized: true, ...(body || {}) }) },
    ),
  patch: (pid: string, volNo: number, cno: number, body: Record<string, unknown>) =>
    req<{ ok: boolean; brief: any }>(
      `/api/projects/${pid}/volumes/${volNo}/chapters/${cno}/brief`,
      { method: 'PATCH', body: JSON.stringify(body) },
    ),
  // 向后兼容旧接口
  nextChapterBrief: (pid: string, volNo: number, prev_chapter_no?: number) =>
    req<any>(
      `/api/projects/${pid}/volumes/${volNo}/next-chapter-brief`,
      { method: 'POST', body: JSON.stringify({ prev_chapter_no }) },
    ),
}

// ============================================================
// 杀青归账（settle）6 步面板
// ============================================================
export interface SettleForeshadow {
  id: string
  chapter_no: number
  planted_text: string
  status: 'paid' | 'dropped' | 'carried'
  resolution?: string
}

export interface SettleEntityCheck {
  entity_id: string
  name: string
  status_after: string
  san_delta: number
  note: string
}

export interface SettleCostSummary {
  cost_summary: string
  suggested_contamination_delta: number
  san_loss_note: string
  new_echo?: string
  kill_add?: string
}

export interface SettleBlueStarHook {
  id: string
  title: string
  detail: string
  summary?: string
}

export interface SettleNextVolumeHint {
  id: string
  title: string
  entry_point: string
  info_gap: string
  summary?: string
}

export interface SettleOverview {
  volume: any
  foreshadows: SettleForeshadow[]
  chapters_written: number
  total_words: number
  death_count: number
  current_contamination: number
  is_mock: boolean
}

export interface SettleEntityUpdate {
  id: string
  status?: string
  san_delta?: number
  note?: string
}

export interface SettleApplyBody {
  contamination_delta: number
  foreshadow_decisions: Record<string, { status: string; resolution?: string }>
  entity_updates?: SettleEntityUpdate[]
  cost_summary?: Partial<SettleCostSummary>
  blue_star_hook?: SettleBlueStarHook | null
  next_volume_hint?: SettleNextVolumeHint | null
}

export const settleApi = {
  overview: (pid: string, volNo: number) =>
    req<SettleOverview>(`/api/projects/${pid}/volumes/${volNo}/settle/overview`),

  detect: (pid: string, volNo: number) =>
    req<{ foreshadows: SettleForeshadow[] }>(`/api/projects/${pid}/volumes/${volNo}/settle/detect`),

  aiSuggest: (pid: string, volNo: number, step: string, context?: Record<string, unknown>) =>
    req<Record<string, any>>(
      `/api/projects/${pid}/volumes/${volNo}/settle/ai-suggest`,
      { method: 'POST', body: JSON.stringify({ step, context: context || {} }) },
    ),

  apply: (pid: string, volNo: number, body: SettleApplyBody) =>
    req<any>(
      `/api/projects/${pid}/volumes/${volNo}/settle/apply`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  finalize: (pid: string, volNo: number) =>
    req<{ ok: boolean; volume: any; next_action_options: string[] }>(
      `/api/projects/${pid}/volumes/${volNo}/settle/finalize`,
      { method: 'POST', body: JSON.stringify({}) },
    ),

  // 落锤后的后续动作
  createInterlude: (pid: string) =>
    req<{ ok: boolean; volume_no: number; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/interlude`,
      { method: 'POST', body: JSON.stringify({}) },
    ),

  createNextDungeon: (pid: string, title: string) =>
    req<{ ok: boolean; volume_no: number; volume: any }>(
      `/api/projects/${pid}/workbench/volumes`,
      { method: 'POST', body: JSON.stringify({ title }) },
    ),
}

// ============================================================
// 蓝星间章（Interlude）API
// ============================================================
export interface InterludeOption {
  id?: string
  title: string
  content: string
  [k: string]: any
}

export interface InterludeZhangshu {
  suggested_chapters: number
  words_per_chapter: number
  reason: string
}

export interface InterludePlan {
  weihedian?: InterludeOption
  houchiyizheng?: InterludeOption
  xiajuangouzi?: InterludeOption
  zhangshu?: InterludeZhangshu
  steps_done?: number
  updated_at?: string
  finalized_at?: string
}

export interface InterludeGetResp {
  volume: any
  plan: InterludePlan
  can_start_writing: boolean
}

export type InterludeStep = 'weihedian' | 'houchiyizheng' | 'xiajuangouzi' | 'zhangshu'

export const interludeApi = {
  getVolume: adaptApi.getVolume,

  get: (pid: string, volNo: number) =>
    req<InterludeGetResp>(`/api/projects/${pid}/workbench/volumes/${volNo}/interlude`),

  aiSuggest: (
    pid: string,
    volNo: number,
    step: InterludeStep,
    context?: Record<string, unknown>,
    selected?: Record<string, any> | null,
  ) =>
    req<{ options?: InterludeOption[]; zhangshu?: InterludeZhangshu; is_mock: boolean }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/interlude/ai-suggest`,
      { method: 'POST', body: JSON.stringify({ step, context: context || {}, selected: selected || null }) },
    ),

  savePlan: (pid: string, volNo: number, step: InterludeStep, picked: InterludeOption | InterludeZhangshu) =>
    req<{ ok: boolean; plan: InterludePlan }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/interlude/save-plan`,
      { method: 'POST', body: JSON.stringify({ step, picked }) },
    ),

  finalize: (pid: string, volNo: number) =>
    req<{ ok: boolean; volume: any; created_chapters: number[]; first_chapter_no: number }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/interlude/finalize`,
      { method: 'POST', body: JSON.stringify({}) },
    ),
}

// ---- volumesApi 快捷封装 ----
export const volumesApi = {
  list: (pid: string) => req<{ volumes: any[]; total: number }>(`/api/projects/${pid}/volumes`),
  latest: (pid: string) => req<{ volume: any | null }>(`/api/projects/${pid}/volumes/latest`),
  get: (pid: string, volNo: number) => adaptApi.getVolume(pid, volNo),
  createDungeon: (pid: string, title = '') => adaptApi.createVolume(pid, title),
}

