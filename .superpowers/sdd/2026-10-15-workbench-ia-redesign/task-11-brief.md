# Task 11: SettleView 杀青 6 步面板

## 定位
副本 dungeon 卷所有章节定稿后进入"杀青"——清点伏笔、核算代价、埋蓝星钩子、预告下卷，落锤后选"进蓝星间章"或"直接开下一副本"。

## 后端：settle_routes.py 扩展

当前已有 detect/apply/finalize 三个端点，但都用 `/api/projects/{pid}/volumes/...` 旧前缀。为保持一致，新增 workbench 前缀版本（不删除旧的，兼容）。实际上，最简单的做法：**保持现有端点路径不变**（它们挂在 `/api/projects/{pid}/volumes/{vol_no}/settle/...`），前端新 settleApi 直接用这个路径即可，省得改动。

**但需要加 AI 辅助端点**，注入 `chore_provider`：

### 修改 register_settle_routes 签名
加入 `chore_provider: Callable` 参数，main.py 注册时补上 `_chore_provider`。

### 新增端点（都挂在同一个 router 下）

1. **GET /api/projects/{pid}/volumes/{vol_no}/settle/overview**
   - 返回杀青 6 步的初始数据聚合：
     ```json
     {
       "volume": {...as_dict},
       "foreshadows": detect_foreshadows(...)结果,
       "chapters_written": N,
       "total_words": N,
       "death_count": 本卷死亡数（从 vol.death_nodes 长度）,
       "current_contamination": N,  // 从 es.get(PROTAGONIST_ID) 取 contamination_level
       "is_mock": bool,
     }
     ```
   - mock 判断同其他模块（adapted_module "测试副本核文本" 开头 或 host_character.name 含"林薇"），非 mock 也能直接返回这些（都是纯本地数据，AI 不需要介入）。

2. **POST /api/projects/{pid}/volumes/{vol_no}/settle/ai-suggest**
   - 请求体：`{ step: "entity_check"|"cost"|"blue_star_hook"|"next_volume_hint", context?: any }`
   - 根据 step 返回 AI 建议。单次结构化返回（不需要 SSE，短任务）。
   - 组装上下文：vol 的 core_twist/death_nodes/climax_nodes/cost_cap、唐迟 outline、当前实体污染状态、本卷章末文字、用户已做的伏笔决定。
   - 返回：
     - step=entity_check：返回实体污染建议 `{ checks: [{entity_name, current_status, suggested_status, reason, san_delta?:number}] }`
     - step=cost：返回代价核算 `{ cost_summary: string, suggested_contamination_delta: number, san_loss_note: string, new_echo: string, kill_add: string }`
     - step=blue_star_hook：返回蓝星钩子 3 个候选（SushiCards 用）`{ hooks: [{ title, content, how_to_plant, weight }] }`（3 个差异版，克苏鲁基调，钩子是蓝星现实里残留的副本痕迹，例如唐迟手机里多了一个不存在联系人、梦里总听见海声）
     - step=next_volume_hint：返回下一副本预告 3 个候选 `{ hints: [{ title, tease, connected_clue, difficulty }] }`
   - **mock 短路**：返回硬编码克苏鲁风格 mock 数据（每个 step 都有）。
   - 注意：blue_star_hook 和 next_volume_hint 返回数组（3 个候选），其他两个 step 返回单条建议。

3. 现有 detect/apply/finalize 保留不动。apply 请求体新增可选字段：
   - `entity_updates: [{id, status, san_delta?:number, note}]`（实体污染状态修正）
   - `cost_summary: string`（代价核算结论）
   - `blue_star_hook: {title, content, how_to_plant}`（选中的蓝星钩子）
   - `next_volume_hint: {title, tease, connected_clue}`（选中的下卷预告）
   - 这些字段在 apply 时一起写入 v.settle：
     ```python
     v.settle = {
       "applied_at": _now(),
       "foreshadow_decisions": decisions_out,
       "entity_updates": body.entity_updates,
       "cost_summary": body.cost_summary,
       "blue_star_hook": body.blue_star_hook,
       "next_volume_hint": body.next_volume_hint,
       "diff_summary": summary,
       "final_chapter_no": last_chapter_no,
     }
     ```
   - entity_updates 里每条要应用到 es：更新 status、如果有 san_delta 应用到主角 contamination_level 上（通过 contamination_delta 累积到 protagonist）。简单做法：san_delta 累加到 body.contamination_delta 上即可。

### 修改 finalize
- finalize 成功后返回增加 `next_action_options: ["interlude", "next_dungeon"]`（供前端让用户选）
- **不**自动创建下一卷；前端选后再调 phase_routes 已有的 `/api/projects/{pid}/workbench/volumes` 或 `/workbench/volumes/interlude`

## 前端：api.ts

在 briefApi 之后加 settleApi：
```ts
export interface SettleForeshadow {
  id: string; name: string; status: string;
  planted_chapter?: number; last_mentioned?: number; overdue?: boolean;
  text?: string;
}
export interface SettleOverview {
  volume: any;
  foreshadows: SettleForeshadow[];
  chapters_written: number;
  total_words: number;
  death_count: number;
  current_contamination: number;
  is_mock?: boolean;
}
export interface SettleEntityCheck { name: string; current_status: string; suggested_status: string; reason: string; san_delta?: number }
export interface SettleCostSummary { cost_summary: string; suggested_contamination_delta: number; san_loss_note: string; new_echo: string; kill_add: string }
export interface SettleBlueStarHook { id?: string; title: string; content: string; how_to_plant: string; weight: number }
export interface SettleNextVolumeHint { id?: string; title: string; tease: string; connected_clue: string; difficulty: string }

export const settleApi = {
  overview: (pid: string, volNo: number) =>
    req<SettleOverview>(`/api/projects/${pid}/volumes/${volNo}/settle/overview`),
  aiSuggest: (pid: string, volNo: number, step: string, context?: any) =>
    req<any>(`/api/projects/${pid}/volumes/${volNo}/settle/ai-suggest`, {
      method: 'POST', body: JSON.stringify({ step, context: context ?? {} })
    }),
  detect: (pid: string, volNo: number) =>
    req<{ foreshadows: SettleForeshadow[] }>(`/api/projects/${pid}/volumes/${volNo}/settle/detect`),
  apply: (pid: string, volNo: number, body: any) =>
    req<{ ok: boolean; settle: any }>(`/api/projects/${pid}/volumes/${volNo}/settle/apply`, {
      method: 'POST', body: JSON.stringify(body)
    }),
  finalize: (pid: string, volNo: number) =>
    req<{ ok: boolean; volume: any; next_action_options: string[] }>(
      `/api/projects/${pid}/volumes/${volNo}/settle/finalize`, { method: 'POST' }
    ),
  createInterlude: (pid: string) =>
    req<any>(`/api/projects/${pid}/workbench/volumes/interlude`, { method: 'POST' }),
  createNextDungeon: (pid: string, title = '') =>
    req<any>(`/api/projects/${pid}/workbench/volumes`, { method: 'POST', body: JSON.stringify({ title }) }),
}
```

## 前端：views/SettleView.vue

props：`volNo: number`；emits：`done`。

**6 步 el-steps**（类似 EmbodyView），每步一个卡片，线性推进（"下一步"按钮）。步骤：

1. **Step 0：伏笔清点**
   - 调 settleApi.overview 拿到 foreshadows 列表
   - 表格展示：伏笔名 | 状态（planted/paid/dropped）| 埋在第几章 | 最近提及 | 操作（pay 回收✅ / drop 弃置🗑️ / carry 留到下卷⏭️）
   - 支持一键"让 AI 建议处理方式"按钮，调 ai-suggest（但伏笔主要是作者决定，AI 仅给建议文案，可以不做 AI 按钮，只让作者手动选 action——简单优先）。
2. **Step 1：实体污染检查**
   - 调 ai-suggest step=entity_check（如果非 mock），mock 路径直接显示 mock
   - 展示 AI 建议的实体状态修正列表（表格：实体名 | 当前状态 | 建议状态 | 原因 | 理智变化），每行一个"采纳"开关
   - 作者可手动编辑/加条目
3. **Step 2：代价核算**
   - 调 ai-suggest step=cost，拿到 cost_summary/suggested_contamination_delta/san_loss_note/new_echo/kill_add
   - 展示为一张"代价卡片"：文本描述 + el-input-number 调整 contamination_delta（默认 AI 建议值，0~5）
   - 作者可改写 cost_summary
4. **Step 3：蓝星钩子埋设**
   - 调 ai-suggest step=blue_star_hook，拿到 3 个候选
   - 用 SushiCards(mode=ensemble, plateCount=3, allowCustom=true) 展示（支持重抽/自编/微调，贯彻迭代设计原则）
   - 选中一版钩子作为本卷蓝星钩子
5. **Step 4：下卷预告**
   - 调 ai-suggest step=next_volume_hint，拿到 3 个候选
   - 用 SushiCards(mode=ensemble, plateCount=3, allowCustom=true) 展示，支持迭代
   - 选中一版作为下卷预告；不选也可以（允许不预告）
6. **Step 5：杀青落锤**
   - 汇总展示前 5 步结果（伏笔决定条数 / 实体更新数 / 污染+N / 蓝星钩子标题 / 下卷预告标题）
   - 按钮"归账落锤"：调 settleApi.apply 把所有决定送上去
   - apply 成功后显示 el-result success"副本杀青完成"
   - 下方两个按钮：
     - 「去蓝星间章 →」：调 settleApi.finalize → settleApi.createInterlude → emit('done')（HomeView 会 loadPhase 进 interlude）
     - 「直接开下一副本 →」：调 settleApi.finalize → settleApi.createNextDungeon → emit('done')

**每步**都有"上一步"/"下一步"按钮，最后一步显示"归账落锤"。进入视图时自动加载 overview。

**Mock 视觉**：如果 overview.is_mock=true，顶部显示一个小 el-tag type="info" "当前使用测试副本数据"。

**克苏鲁暗色基调**，和其他视图一致。

## HomeView 注册
在 DeskView 分支之后加：
```html
<SettleView
  v-else-if="phase === 'settle'"
  :vol-no="currentVolumeNo"
  @done="loadPhase"
/>
```
import SettleView。

## 验收标准
1. 后端：新 `/settle/overview` 和 `/settle/ai-suggest` 端点；register_settle_routes 接受 chore_provider 参数；apply 接受新增字段并写入 v.settle；finalize 返回 next_action_options；uvicorn import 成功。
2. 后端：mock 路径下 ai-suggest 四个 step 都返回合理硬编码克苏鲁数据。
3. 前端：SettleView 6 步向导能从 Step 0 走到 Step 5；伏笔表格 action 可切换；实体/代价 AI 建议展示正确；蓝星钩子和下卷预告使用 SushiCards ensemble 支持迭代；落锤后两按钮能调 finalize+createInterlude/createNextDungeon 并触发 @done。
4. 前端：settleApi 已导出，类型完整。
5. HomeView 正确注册 SettleView。
6. `cd backend; .\.venv\Scripts\python.exe -c "from app.api.main import app; print('OK', len(app.routes))"` 通过。

## Commit
`feat(fe+be): SettleView 杀青6步（伏笔清点/实体污染/代价/蓝星钩子/下卷预告/落锤）`

## 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-11-report.md`

不要 git push。
