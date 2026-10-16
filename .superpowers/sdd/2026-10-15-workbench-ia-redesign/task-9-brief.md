# Task 9: RhythmView 节奏骨架

## 任务定位
在副本详设（scaffold 6 卡全部锁定）之后、进入章前 5 分钟（briefing）之前，新增一个"节奏骨架"视图：把 7 天内死亡/高潮/线索节点的分布做可视分析 + 给出建议章节分配，让作者在动笔前就能看到节奏问题（比如"第 4 天高潮真空"、"两天死 4 个人太密"）。

## 后端：rhythm_routes.py

### 路由前缀
`/api/projects/{pid}/workbench/volumes/{vol_no}/rhythm`

### 函数式注册
参考 scaffold_routes.py 模式：
```python
def register_rhythm_routes(app, *, require_project, volumes_for, chore_provider)
```
在 main.py 中放在 register_scaffold_routes 之后，用相同的 3 个注入参数（require_project, volumes_for, chore_provider）。

### 端点

1. **POST /analyze**（短任务，直接 await 返回，不用 SSE）
   - 前置校验：volume.scaffold_cards 6 张卡必须都 locked（即 stage_completed.scaffold=True 或每张卡都有 locked_at），否则 422
   - 组装输入：
     - 从 volume 读：death_nodes（已选定那一版，即 vol.death_nodes，不是 scaffold_cards.death_nodes.variants）、climax_nodes（vol.climax_nodes）、clue_nodes（vol.clue_nodes）、core_twist、tangchi_story_outline（简要）、stance
     - scaffold_cards 里 core_twist/tangchi_start/cost_cap 的 text 字段（已锁定文本）
   - 计算/LLM 调用：
     - 先**纯本地**算一个 density_map（7 天每天的节点计数：death/climax/clue 各多少）：这部分不依赖 LLM，直接从 nodes 的 day 字段聚合
     - 然后**单次** LLM complete_structured 调用，返回 rhythm_blueprint dict（不需要多版本、不需要选版，直接生成一个推荐方案）
   - LLM 返回 JSON schema（严格 JSON 不包裹 Markdown）：
     ```json
     {
       "pacing_ratio": {"setup": 0.18, "build": 0.32, "climax": 0.35, "cooldown": 0.15},
       "key_beats_progress": [
         {"day": 1, "intensity": 2, "label": "入本惊悚"},
         {"day": 2, "intensity": 3, "label": "第一滴血"},
         {"day": 3, "intensity": 4, "label": "规则显形"},
         {"day": 4, "intensity": 3, "label": "喘息/误导"},
         {"day": 5, "intensity": 5, "label": "中盘反转"},
         {"day": 6, "intensity": 6, "label": "同伴接连殒落"},
         {"day": 7, "intensity": 7, "label": "终极高潮"}
       ],
       "warnings": [
         {"type": "dearth", "day": 4, "msg": "第4天无高潮节点，节奏可能塌腰"},
         {"type": "cluster", "day": 5, "msg": "第5天同时有死亡+高潮+3条线索，信息过载"}
       ],
       "suggested_length": 95000,
       "suggested_chapter_range": [12, 16],
       "chapter_plan": [
         {"chapter": 1, "day": 1, "focus": "入本+第一异常", "words": 6500, "beat_type": "hook"},
         {"chapter": 2, "day": 1, "focus": "宿管之死+规则建立", "words": 7000, "beat_type": "sell"},
         ...第3~N章...
       ],
       "notes": "整体节奏建议：前3章黄金三章拉满恐怖密度，第4章给主角短暂喘息+误导红鲱鱼..."
     }
     ```
     - `pacing_ratio` 四段加起来=1.0
     - `key_beats_progress` 长度必须是 7，day=1~7，intensity 1~7
     - `warnings` 基于 density_map + AI 判断（deearth=真空, cluster=过载, front_load=前重后轻, back_load=前轻后重）
     - `suggested_length` 总字数（整数）
     - `suggested_chapter_range` [min, max]
     - `chapter_plan` 章节分配，长度必须在 [min, max] 之间，每章标注覆盖的 day、focus 一句话、预估字数（整数）、beat_type（hook/sell/engine/climax/cooldown 之一）
   - 写入 vol.rhythm_blueprint = 返回的 dict（不要改 death_nodes 等其他字段）
   - 返回：`{"ok": True, "blueprint": <dict>, "density_map": <本地计算的密度>, "is_mock": bool}`

2. **POST /finalize**
   - 校验：rhythm_blueprint 非空
   - 设置 stage_completed.rhythm = True
   - 返回：`{"ok": True, "stage_completed": vol.stage_completed, "volume": vol.as_dict()}`

3. **GET /** （获取当前节奏蓝图）
   - 返回：`{"volume": vol.as_dict(), "blueprint": vol.rhythm_blueprint, "density_map": <重新算一遍>}`（volume 里其实已经带 blueprint，这里额外计算 density_map 供前端画图）

### Mock 短路
与现有约定一致：当 `vol.adapted_module` 以 "测试副本核文本" 开头 或 `vol.host_character?.name` 含 "林薇" 时，不调 LLM，直接返回硬编码 mock 数据（要包含合理的 7 天 intensity 曲线 + 12-16 章 chapter_plan + 至少 2 条 warnings，克苏鲁基调）。

### Volume.update_planning 白名单
在 VolumeStore.update_planning 的 planning_fields 集合里加上 `"rhythm_blueprint"`，在 _allow_falsey 里也加上。

### stage_completed 默认值
在 Volume 类里 stage_completed 默认值已经包含 "rhythm": False，无需改。但要确认 from_dict 补齐时没漏（读 volume.py 确认过已有）。

### 路由注册
在 `backend/app/api/main.py` 的 register_scaffold_routes 调用**之后**添加：
```python
from .rhythm_routes import register_rhythm_routes
register_rhythm_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    chore_provider=_chore_provider,
)
```

## 前端：api.ts

在 scaffoldApi 块之后追加 `rhythmApi`，参照 scaffoldApi 风格（简单对象，无 SSE，无 Job）：

```ts
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
  analyze: (pid: string, volNo: number) =>
    req<{ ok: boolean; blueprint: RhythmBlueprint; density_map: DensityMap; is_mock?: boolean }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/rhythm/analyze`,
      { method: 'POST', body: JSON.stringify({}) },
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
```

不要 subscribeJobs / listJobs（没长任务）。

## 前端：views/RhythmView.vue

新建一个视图组件，props：`volNo: number`；emits：`done`。

### 布局（单页，不分步）

**顶部标题区：**
- 标题：`节奏骨架 · 第七日`
- 副标题：基于死亡/高潮/线索分布推演整卷节奏，给出章节分配建议
- 右侧"AI 推演节奏"按钮（el-button type="primary"），点击调 rhythmApi.analyze；loading 期间显示"AI 正在推演节奏…"

**核心可视化区（用 Element Plus 原生组件 + 纯 CSS，不引入 echarts）：**

1. **7 天节奏强度曲线**：一条横向 7 格 bar，每格高度按 intensity(1~7) 比例，柱顶显示 day 数字+label。可用 el-progress（不，不好看）——建议用纯 div flex 布局：
   - 外层 `.rhythm-curve` 横向 flex
   - 7 个 `.rhythm-day-col`，内部
     - 顶部 `.rhythm-label`（day 数字 + label，小字号）
     - 中部 `.rhythm-bar-wrap` 固定高度（比如 180px），内部 `.rhythm-bar` 从底部往上长，背景线性渐变 `linear-gradient(to top, #409EFF, #F56C6C)`（蓝→红，强度越高越红）
     - 底部 `.rhythm-day-num`（D1~D7）
   - 每个 day-col 内用小图标/圆点标记节点（死亡=💀，高潮=★，线索=●，红鲱鱼=○），位置按密度分布垂直排列
2. **7 天节点密度条**：另起一行，横向 7 列，每列叠三条 el-progress（death 红、climax 橙、clue 蓝），max 按当天最大值。或纯 CSS 水平堆叠 bar。
3. **节奏四段占比**：4 个 el-tag / 小卡片显示 setup/build/climax/cooldown 占比百分比。

**节奏告警区：**
- 用 el-alert type="warning" 展示 warnings 列表（按 dearth/cluster/front_load/back_load 给不同 icon 或 tag 颜色），如果 warnings 为空显示 el-result type="success" "节奏分布均衡"。
- 如果未 analyze，显示 el-empty "点击右上角「AI 推演节奏」生成蓝图"。

**章节分配建议区：**
- 标题"建议章节分配（{{suggested_chapter_range[0]}}~{{suggested_chapter_range[1]}}章 / 约{{suggested_length}}字）"
- el-table 展示 chapter_plan：chapter | day | focus | words | beat_type
  - beat_type 用 el-tag 着色：hook=蓝, sell=绿, engine=青, climax=红, cooldown=灰
- 表格底部显示 notes（一段建议文本）。

**底部按钮：**
- 「采纳此节奏方案，进入章前规划」el-button type="primary" size="large"，点击调 rhythmApi.finalize，成功后 $emit('done')
- 未 analyze 时按钮 disabled

### 状态 & 生命周期
- onMounted + watch(volNo)：先调 rhythmApi.get()，如果已有 blueprint（非 null）直接展示，无需重新 analyze；如果没有显示空态+按钮。
- analyze 返回后把 blueprint/density_map 存到 ref，渲染可视化。
- 错误用 ElMessage.error 提示。

### 样式
- scoped CSS，克苏鲁暗色基调，和 EmbodyView/DungeonScaffoldView 风格一致（主色可参考 #1f1d2b 深色背景配金色/血色强调）。
- 节奏柱要做出"心电图"那种紧张感——bar 边缘可加点阴影。
- 宽度自适应：rhythm-curve 在小屏下 flex-wrap（或横向滚动）。

## 在 HomeView.vue 中注册

import RhythmView，在 `<DungeonScaffoldView v-else-if="phase==='scaffold'" />` 之后加：
```html
<RhythmView
  v-else-if="phase === 'rhythm'"
  :vol-no="currentVolumeNo"
  @done="loadPhase"
/>
```

## 验收标准
1. 后端：`POST /api/projects/{pid}/workbench/volumes/{n}/rhythm/analyze` 在 6 卡已锁定时返回 blueprint + density_map，写入 volume.rhythm_blueprint；mock 路径（host_character.name 含"林薇"）能正常返回硬编码数据。
2. 后端：volume.update_planning 支持 rhythm_blueprint 字段。
3. 后端：finalize 正确设置 stage_completed.rhythm=true，GET /phase 从此阶段流转到 briefing（章前规划）。
4. 后端：main.py 正确注册 rhythm_routes（不引入循环导入，启动 uvicorn 不报 import 错）。
5. 前端：RhythmView 在 phase==='rhythm' 时能渲染；进入页面若已有 blueprint 直接展示；点击"AI 推演节奏"能拿到数据并渲染 7 天柱状曲线+密度条+warnings+章节表；点击"采纳"能 finalize 并触发 @done 切阶段。
6. 前端：api.ts 已导出 rhythmApi 及其 TypeScript 类型。
7. 风格：视觉一致，无 console.log 调试输出，TypeScript 无显式 any（局部 any 可以接受但不要大面积）。

## 提交
完成后一个 commit：
`feat(fe+be): RhythmView 节奏骨架（7天强度曲线+密度分析+章节分配建议+告警）`

把改动文件（backend/app/api/rhythm_routes.py、backend/app/book/volume.py、backend/app/api/main.py、frontend/src/api.ts、frontend/src/views/RhythmView.vue、frontend/src/views/HomeView.vue）一并提交。

## 报告
写到 `.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-9-report.md`，内容包括：
- 完成状态（DONE）
- 提交的 commit hash
- 文件清单
- 跑过的基本验证（例如启动 uvicorn 能 import 成功、curl 或浏览器调用 analyze 接口能返回 JSON）
- 已知遗留问题（如果有）
