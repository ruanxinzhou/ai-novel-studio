# Task 12: InterludeView 蓝星间章

## 定位
杀青落锤后，如果作者选"进蓝星间章"，就创建 volume_kind='interlude' 的特殊卷。间章是唐迟回到蓝星现实后的短篇过渡（1-3 章），不走 adapt/simulate/embody/scaffold/rhythm 流程，直接通过 4 步向导生成间章规划，然后写 1-3 章（复用 DeskView），杀青后自动创建下一 dungeon 卷。

## 后端：interlude_routes.py（新建）

### 函数式注册
```python
def register_interlude_routes(app, *, require_project, volumes_for, desk_for, chore_provider)
```
在 main.py 的 register_settle_routes 之后注册。

### 路由前缀
`/api/projects/{pid}/workbench/volumes/{vol_no}/interlude`（走 workbench 前缀）

### 端点

1. **GET /** （获取间章规划）
   - 校验：vol.volume_kind 必须是 'interlude'，否则 422（"不是间章卷"）
   - 返回：`{ volume: vol.as_dict(), plan: vol.interlude_plan, can_start_writing: bool }`
   - can_start_writing = interlude_plan 非空且 interlude_plan.steps_done === 4

2. **POST /ai-suggest**
   - 请求体：`{ step: "weihedian"|"houchiyizheng"|"xiajuangouzi"|"zhangshu", context?: any, selected?: any }`
   - step 对应 4 步：
     - **weihedian（违和点）**：回到蓝星后，唐迟发现哪些从副本带回来的异常（3 个候选，SushiCards 用）。克苏鲁基调：不是大张旗鼓的恐怖，而是微妙的不对劲（手机里多了一个不认识的联系人、浴室镜子里自己慢半拍、梦中听见海声、手心长出藤壶状的小疤、总觉得背后有人叫他名字但回头没有人）。
     - **houchiyizheng（后遗症）**：副本留下的心理/生理/能力后遗症（3 个候选）——轻度妄想、对特定声音恐惧、新获得某种"感知"但伴随代价（能听见不该听的声音但越听越疯狂）、身上的印记。
     - **xiajuangouzi（下卷钩子）**：下一副本的召唤信号如何出现（3 个候选）——快递收到一个不属于自己的包裹、新闻里某案件和副本里死亡方式一模一样、联系人列表里那个陌生号码主动发来定位、唐迟身边某个人开始说梦话叫出副本里死者的名字。
     - **zhangshu（章数）**：建议间章长度（AI 返回 `{suggested_chapters: 1~3, words_per_chapter: 2500~5000, reason: string}`，单条）
   - 上下文来源：上一 dungeon 卷（max(volumes) 且 volume_kind=='dungeon'）的 settle.blue_star_hook、settle.cost_summary、tangchi_story_outline、host_character、contamination 现状。
   - 单次结构化返回（不用 SSE）；mock 短路（和其他模块一致——这里可以简化：只要 volume.title 含"蓝星间章"或 volume_kind=='interlude' 且 pid 任意 就走 mock；因为间章本身没有 adapted_module，用"蓝星"或"间章"关键词即可）。
   - 前 3 个 step 返回数组（3 个候选），zhangshu 返回单对象。

3. **POST /save-plan**
   - 请求体：`{ step: string, picked: any }`
   - 把每一步选中的结果写入 vol.interlude_plan.{step} = picked；维护 steps_done 计数。
   - 返回：`{ok: true, plan: vol.interlude_plan}`
   - vol.interlude_plan 结构：
     ```json
     {
       "weihedian": {...选中的违和点...},
       "houchiyizheng": {...},
       "xiajuangouzi": {...},
       "zhangshu": {"suggested_chapters": N, "words_per_chapter": N, "reason": ""},
       "steps_done": 0~4,
       "updated_at": "..."
     }
     ```

4. **POST /finalize**
   - 校验：steps_done===4
   - 写 vol.interlude_plan.finalized_at = _now()
   - 根据 zhangshu.suggested_chapters 自动创建 N 章（desk.ensure(1)..desk.ensure(N)，每章 field_no = vol.field_no，is_interlude=True）。
   - 给第 1 章生成一个 brief（章前 5 分钟卡）：
     ```python
     work = desk.ensure(1)
     work.field_no = vol.field_no
     work.is_interlude = True
     work.brief = {
       "selected": {
         "title": "回家：第一件不对劲的事",
         "summary": interlude_plan.weihedian.content,
         "is_interlude": True,
       },
       "selected_at": _now(),
     }
     desk._flush()
     ```
     剩下的 N-1 章 brief 留空，写完一章再弹下一章（类似 dungeon 卷章前卡流程，但 interlude 的章前卡可以简化或直接进入 desk）。
   - 设置 stage_completed？间章不走 stage_completed 那一套（phase 判定逻辑里 interlude 是特殊分支）。finalize 后下一次 GET /phase 会看到有未定稿章（desk.all_nos() 里的未 finalized），返回 phase='desk'。
   - 返回：`{ok: true, chapters_created: [1,...N], volume: vol.as_dict()}`

### Volume / Desk 支持
- volume.py 里 interlude_plan 字段已存在（dict|None），无需新增。
- 需要确认 ChapterWork 有 is_interlude 字段。读代码：wizard/store.py 里 ChapterWork 已经有 is_interlude。如果没有，加一个 `is_interlude: bool = False`。
- volumes_for(pid).create_new_volume(title="蓝星间章", volume_kind="interlude") 已经在 T2 里存在（phase_routes._create_interlude_volume）。

## 前端：api.ts

在 settleApi 之后加 interludeApi：
```ts
export interface InterludeOption { id?: string; title: string; content: string; [k:string]: any }
export interface InterludeZhangshu { suggested_chapters: number; words_per_chapter: number; reason: string }
export const interludeApi = {
  getVolume: adaptApi.getVolume,
  get: (pid: string, volNo: number) =>
    req<{ volume: any; plan: any; can_start_writing: boolean }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/interlude`,
    ),
  aiSuggest: (pid: string, volNo: number, step: string, context?: any, selected?: any) =>
    req<any>(`/api/projects/${pid}/workbench/volumes/${volNo}/interlude/ai-suggest`, {
      method: 'POST', body: JSON.stringify({ step, context: context ?? {}, selected: selected ?? null })
    }),
  savePlan: (pid: string, volNo: number, step: string, picked: any) =>
    req<{ ok: boolean; plan: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/interlude/save-plan`,
      { method: 'POST', body: JSON.stringify({ step, picked }) }
    ),
  finalize: (pid: string, volNo: number) =>
    req<{ ok: boolean; chapters_created: number[]; volume: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/interlude/finalize`,
      { method: 'POST', body: JSON.stringify({}) }
    ),
}
```

## 前端：views/InterludeView.vue

props：`volNo: number`；emits：`done`。

**4 步 el-steps**（和 SettleView 一致的节奏），4 步分别对应：
1. **违和点**：AI 3 版候选，SushiCards ensemble 3 盘+迭代（重抽/自编/微调）
2. **后遗症**：同上
3. **下卷钩子**：同上
4. **间章长度**：AI 建议章数+每章字数，用 el-slider 调整（1-3 章，每章 2000-6000 字），显示 reason 文本
- 顶部标题"蓝星间章 · 短暂的喘息"，副文："唐迟回到了蓝星现实，但副本不会轻易放过他。这里是下一次挣扎前的片刻宁静——亦是新的恐惧的开始。"
- 每步"上一步"/"保存并继续"
- 第 4 步底部按钮"落笔，写间章" → 调 finalize → emit('done')，HomeView loadPhase 后会进 desk 写第一章。

**克苏鲁暗色基调**，但可以比副本阶段稍微"偏冷/偏灰"（蓝星现实的色调，少一点血红多一点冷蓝），体现"看似日常却有什么不对"的氛围。

## HomeView 注册
在 SettleView 分支之后加：
```html
<InterludeView
  v-else-if="phase === 'interlude'"
  :vol-no="currentVolumeNo"
  @done="loadPhase"
/>
```
import InterludeView。

注意：phase_routes._determine_phase 里间章判定逻辑已经写过了（step 4），确认它在 interlude_plan 为空时返回 phase='interlude'，在 finalize 后 interlude_plan.finalized_at 存在且有未定稿章时返回 phase='desk'，在所有章 finalized 且 settle 为 None 时返回 phase='settle'（间章杀青）。读代码确认：现有逻辑是
```
if last_vol.volume_kind == "interlude" and last_vol.settle is None:
    unfinished = [...]
    if unfinished: return phase='desk' ...
    if not last_vol.interlude_plan: return phase='interlude'
```
——这里有个问题：finalize 后 interlude_plan 已经有 finalized_at，但未设置 settle，且 unfinished 章还没写时会返回 desk（正确）。但在 interlude_plan 有但没 finalized 时会 fall through 到后面的 stage_order 判定（里面没有 interlude 阶段），会错误地进入 adapt。

修复 phase_routes：在"4. 间章判定"里，finalized_at 不存在但 interlude_plan 非空时也应该算 interlude 阶段（因为用户可能填了几步回来继续），或者更简单——把判断改成：
```python
if last_vol.volume_kind == "interlude" and last_vol.settle is None:
    unfinished = [...]
    if unfinished: return phase='desk' ...
    if not last_vol.interlude_plan or not last_vol.interlude_plan.get("finalized_at"):
        return phase='interlude'
```
这样只要间章还没 finalize，就停在 interlude 视图；finalize 后有章节就进 desk。

**请把这个 phase_routes 的修复也做在本任务里。**

## 验收
1. 后端 interlude_routes 4 端点，register 注入，main.py 注册；mock 路径返回克苏鲁风格硬编码数据；finalize 自动创建 N 章+给第 1 章写 brief。
2. phase_routes 间章判定修复。
3. 前端 interludeApi + InterludeView 4 步 SushiCards + 章数 slider。
4. HomeView 注册。
5. 后端 import 验证通过。
6. 如果 ChapterWork 缺 is_interlude 字段，补上默认 False。

## Commit
`feat(fe+be): InterludeView 蓝星间章4步（违和点/后遗症/下卷钩子/章数）+间章phase判定修复`

## 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-12-report.md`

不要 git push。
