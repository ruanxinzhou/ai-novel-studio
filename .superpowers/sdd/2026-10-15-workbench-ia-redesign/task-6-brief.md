# Task 6 Brief：SushiCards 通用化重构（diverge/ensemble 双模式 + 3/5 盘自适应）

## 项目路径
`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`

Vite/uvicorn 如果不在跑需要启动（uvicorn 用 `--reload`，前端 `npm run dev`）。

## 目标
现有 SushiCards 只支持"编辑流程"（出 N 盘→选一盘→进入 AI 改稿→锁定），而 ModuleAdaptView/CocSimView 用它展示后端批量产出（5 版改编 / 3 场跑团）时，是"只读 N 选 1"场景，不该走编辑态。Task 6 把 SushiCards 重构成支持两种模式的通用组件，覆盖未来所有"N 选 1"+"选后打磨"场景。

**不删除 VariantPicker**（它被 SessionView 引用，Task 14 随旧视图一起删），但在文件头加 `@deprecated 旧 kindle 深色 UI，新代码请用 SushiCards mode="ensemble"` 注释。

---

## 前端改动（本任务无后端改动）

### 1. 重构 `frontend/src/components/SushiCards.vue`

保留现有 diverge 模式（默认行为，向后兼容 SessionView 用法），新增 ensemble 模式。

#### 1a. Props 扩展

```ts
interface Props {
  // === 通用 ===
  cardKey?: string           // 可选；ensemble 模式不需要
  cardLabel?: string         // 顶部标签
  mode?: 'diverge' | 'ensemble'  // 默认 'diverge'
  plateCount?: number        // 默认 3（diverge）；ensemble 模式自动 = items.length
  isActive?: boolean         // 默认 true
  axisLabel?: string         // 顶部"◆ XXX ◆"轴标签

  // === diverge 模式专属（保持不变）===
  candidates?: Candidate[]
  editing?: EditingState | null
  messages?: SessionMessage[]
  lockedText?: string | null
  contextHint?: string

  // === ensemble 模式专属 ===
  items?: Record<string, any>[]   // 原始产出列表（pdf5/coc/outline5 等）
  pickedId?: string | null        // 已选定的 item id（用于高亮）
  itemTitleField?: string         // 默认 'label'；item 里哪个字段当卡片标题（可用点路径如 'meta.title'，但简单取顶层即可）
  itemSubtitleField?: string      // 默认 'one_liner'
  itemContentField?: string       // 默认 'content'；大段文本内容字段
  itemBadgeField?: string         // 默认 'flavor'；顶部彩色 tag 字段
  showIndex?: boolean             // 默认 true；是否显示 #1 #2 #3 角标
  allowRegenerate?: boolean       // 默认 true（ensemble 也允许"重新生成"按钮）
  regenerateLabel?: string        // 默认 "换一拨"
  emptyLabel?: string             // 默认 "暂无可选项"
}

const props = withDefaults(defineProps<Props>(), {
  mode: 'diverge',
  cardKey: '',
  cardLabel: '',
  isActive: true,
  axisLabel: '',
  plateCount: 3,
  candidates: () => [],
  editing: null,
  messages: () => [],
  lockedText: null,
  contextHint: '',
  items: () => [],
  pickedId: null,
  itemTitleField: 'label',
  itemSubtitleField: 'one_liner',
  itemContentField: 'content',
  itemBadgeField: 'flavor',
  showIndex: true,
  allowRegenerate: true,
  regenerateLabel: '换一拨',
  emptyLabel: '暂无可选项',
})
```

#### 1b. Emits 扩展

```ts
const emit = defineEmits<{
  // === 通用 ===
  (e: 'pick', id: string | null, index?: number): void   // diverge 传 candidateId，ensemble 传 itemId（用 index 作为兜底 id）
  (e: 'generate'): void

  // === diverge 专属（保持不变）===
  (e: 'updateText', text: string): void
  (e: 'aiSuggest', instruction: string, apply: boolean): void
  (e: 'lock'): void
  (e: 'endEditing'): void
}>()
```

注意：把原来 `(e: 'generate', hint?: string)` 改成无 hint（diverge 的 hint 通过 contextHint prop 或内部 input 获取；不影响 emit 签名，ModuleAdaptView 之前 emit 空函数所以无影响）。

#### 1c. ensemble 模式渲染

新增 `v-else-if="mode==='ensemble'"` 分支，结构和现有 plate 候选展示类似但更通用：

```html
<div v-else-if="mode==='ensemble'" class="sushi-plate">
  <div v-if="axisLabel" class="sushi-axis">
    <span class="sushi-axis-sep">◆</span>{{ axisLabel }}<span class="sushi-axis-sep">◆</span>
  </div>

  <!-- 空态 -->
  <div v-if="!ensembleItems.length" class="sushi-empty">
    <div class="sushi-axis-simple">{{ emptyLabel }}</div>
    <el-button v-if="allowRegenerate && isActive" type="primary" size="small" @click="emit('generate')">
      {{ regenerateLabel }}
    </el-button>
  </div>

  <!-- N 张卡 -->
  <div v-else class="sushi-cards" :class="sushiCardsGridClass">
    <div
      v-for="(it, idx) in ensembleItems"
      :key="itemId(it, idx)"
      class="sushi-cand"
      :class="{ 'sushi-cand--picked': pickedId === itemId(it, idx) }"
    >
      <div v-if="showIndex" class="sushi-cand-idx">#{{ idx + 1 }}</div>
      <div v-if="getField(it, itemBadgeField)" class="sushi-cand-axis">{{ getField(it, itemBadgeField) }}</div>
      <div v-if="getField(it, itemTitleField)" class="sushi-cand-title">{{ getField(it, itemTitleField) }}</div>
      <div v-if="getField(it, itemSubtitleField)" class="sushi-cand-subtitle">{{ getField(it, itemSubtitleField) }}</div>
      <div v-if="getField(it, itemContentField)" class="sushi-cand-text sushi-cand-text--truncated">
        {{ truncate(getField(it, itemContentField), 600) }}
      </div>
      <!-- 默认插槽：允许父组件传自定义详情（例如 Day 列表、meta chip）-->
      <div v-if="$slots.item" class="sushi-cand-extra">
        <slot name="item" :item="it" :index="idx" />
      </div>
      <div class="sushi-cand-pick">
        <el-button
          size="small"
          :type="pickedId === itemId(it, idx) ? 'success' : 'primary'"
          :disabled="!isActive"
          @click="emit('pick', itemId(it, idx), idx)"
        >
          {{ pickedId === itemId(it, idx) ? '已选定' : '选这盘' }}
        </el-button>
      </div>
    </div>
  </div>

  <div v-if="ensembleItems.length && allowRegenerate" class="sushi-plate-actions">
    <el-button size="small" :disabled="!isActive" @click="emit('generate')">{{ regenerateLabel }}</el-button>
  </div>
</div>
```

新增几个 helper：
```ts
const ensembleItems = computed(() => props.items || [])
function getField(it: any, field: string): string {
  if (!it || !field) return ''
  const v = it[field]
  if (v == null) return ''
  return String(v)
}
function itemId(it: any, idx: number): string {
  return it?.id ?? it?.vid ?? String(idx)
}
function truncate(s: string, n: number) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}
const sushiCardsGridClass = computed(() => {
  const n = props.mode === 'ensemble' ? ensembleItems.value.length : props.plateCount
  return {
    'sushi-cards-3': n <= 3,
    'sushi-cards-5': n === 5,
    'sushi-cards-4': n === 4,
    'sushi-cards-6plus': n >= 6,
  }
})
```

#### 1d. 样式增强

把现有 `.sushi-cards` 默认三列保留，加新类：
```css
.sushi-cards-3 { grid-template-columns: repeat(3, 1fr); }
.sushi-cards-4 { grid-template-columns: repeat(2, 1fr); }
.sushi-cards-5 { grid-template-columns: repeat(5, 1fr); }
.sushi-cards-6plus { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 1280px) {
  .sushi-cards-5 { grid-template-columns: repeat(3, 1fr); }
}
.sushi-cand--picked {
  border-color: #67c23a;
  box-shadow: 0 0 0 2px rgba(103,194,58,0.18);
}
.sushi-cand-idx {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 11px;
  color: #c0c4cc;
  font-weight: 600;
}
.sushi-cand { position: relative; } /* 给 idx 定位 */
.sushi-cand-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 4px;
}
.sushi-cand-subtitle {
  font-size: 12px;
  color: #e6a23c;
  margin-bottom: 6px;
  line-height: 1.5;
}
.sushi-cand-text--truncated {
  max-height: 160px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 7;
  -webkit-box-orient: vertical;
}
.sushi-cand-extra {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #ebeef5;
}
```

#### 1e. diverge 模式现有行为保留
- 原 plateCandidates / editingThisCard / suggestionHistory / 锁定态 / 编辑态 / 候选展示态 / 空态逻辑保持不变
- 唯一改动：原来的 `.sushi-cards-5` 类名改为配合 gridClass 的 class object；把原来硬编码 `:class="{ 'sushi-cards-5': plateCount >= 5 }"` 换成 `:class="sushiCardsGridClass"`（diverge 模式下 gridClass 也按 plateCount 生成）

### 2. 更新 ModuleAdaptView 使用 ensemble 模式

**文件**：`frontend/src/views/ModuleAdaptView.vue`

- 现在 ModuleAdaptView 把 adaptations 转成 Candidate[] 传给 SushiCards（diverge 模式）。改为 ensemble 模式更语义化：
  - 传 `mode="ensemble"`
  - 传 `:items="adaptations"`
  - field 映射：`item-title-field="flavor"`, `item-subtitle-field="one_liner"`, `item-content-field="adapted_premise"`, `item-badge-field="flavor"`
  - `:picked-id="pickedAdaptId"`（state 里存 picked id）
  - `:allow-regenerate="false"`（pdf5 重跑代价大，用户想重跑点页面上的"重新上传"，不支持再上一拨）
  - 自定义 item 插槽展示 changed_elements（3 条）和 player_cards 数量：
    ```html
    <template #item="{ item }">
      <div v-if="item.changed_elements?.length" class="meta-line">
        <span v-for="(c,i) in item.changed_elements.slice(0,3)" :key="i" class="meta-chip">{{ c }}</span>
      </div>
      <div v-if="item.player_cards?.length" class="meta-hint">角色卡 {{ item.player_cards.length }} 位</div>
    </template>
    ```
  - `@pick` 回调改成 `(id, idx) => onPickAdapt(idx)`（id 是 itemId 兜底 idx 字符串，直接用 idx 更稳）
- 删除原来把 adaptations 映射成 Candidate[] 的 `candidates` computed；保留 CSS 里 `.sushi-generate-bar` 的隐藏（现在 ensemble 有 allowRegenerate=false 自然不显示换一拨按钮，可以把那段 CSS 删了）。
- 在 state 里加 `pickedAdaptId: string | null = null`，pick 成功后设置。

### 3. 更新 CocSimView 使用 ensemble 模式

**文件**：`frontend/src/views/CocSimView.vue`

- 现在 CocSimView 自己写了 3 张大卡的 HTML。改为用 SushiCards ensemble 模式渲染：
  - 传 `mode="ensemble"`, `:items="variants"`, `plate-count=3`
  - field 映射：`item-title-field="label"`, `item-subtitle-field="one_liner"`, `item-content-field="full_log"`, `item-badge-field="flavor"`
  - `:picked-id="pickedVariantId"`
  - `:allow-regenerate="false"`
  - 自定义 item 插槽：现在 CocSimView 已经有 JSON 预览，保持简单：显示 full_log 前 300 字即可（ensemble 默认 content 会截到 600 字，够了）
  - `@pick="(id, idx) => onPickOne(idx)"`
- 删除 CocSimView 里自己写的 `.vp-card` 3 张大卡模板（el-row/el-col 那一整块），换成单个 `<SushiCards>` 即可
- 拼贴区域（"或：拼贴合成"那块 el-card）保留在 SushiCards 下方不变
- 调整 CSS：原来给 3 张大卡写的样式可以清理

### 4. VariantPicker 加 @deprecated 注释（不删）

**文件**：`frontend/src/components/VariantPicker.vue`

在文件顶部 `<script setup>` 里第一行加注释：
```ts
// @deprecated 旧 kindle 深色 UI（仅被 SessionView 使用），新代码请用 <SushiCards mode="ensemble">。
//             Task 14 删除 SessionView 时一并删除本文件。
```
不改任何代码。

### 5. 验证
- 启动 dev server（如果没在跑）：
  - 后端：`cd backend; .\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload --port 8321`
  - 前端：`cd frontend; npm run dev`
- `cd frontend; npx vue-tsc --noEmit` 零错误
- 浏览器：
  - 用之前的测试脚本造一条 phase=simulate 的数据（和 Task 5 一样：填满 book 8 卡 + 建卷 1 + adapted_module 文本 + stage_completed.adapt=true），访问 http://localhost:5173/ 选项目
  - 验证点 1：进入 CocSimView 准备态，开跑（max_turns=1 mock），完成后 3 张 SushiCards 大卡正确显示 label/one_liner/full_log 截断，选定按钮高亮
  - 手动改 volume.stage_completed.simulate=true（或通过 pick-one 端点），造 adapted_module 存在但 simulate 未选的数据看 ModuleAdaptView 的"已选定"态；如果不好验证 ModuleAdaptView 就只验证 CocSimView 端
  - 清掉测试数据
- 验证完成后 dev server 保持运行（留给后续任务）

### 6. Commit
单一 commit：
```
refactor(fe): SushiCards 通用化（diverge/ensemble 双模式 + 3/4/5 列自适应），ModuleAdaptView/CocSimView 改用 ensemble
```

### 7. 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-6-report.md`：
- commit hash
- 改动文件清单
- vue-tsc 输出
- 浏览器验证描述
- 截图（可选）：CocSimView 用 SushiCards ensemble 的效果图
- 已知遗留：SessionView 仍用旧 SushiCards(diverge) 签名（要保证不破坏）+ VariantPicker（Task 14 一起删）

---

## 重要提醒
- diverge 模式必须保持向后兼容：SessionView 现有用法（传 candidates/editing/messages/lockedText/@pick/@generate/@updateText/@aiSuggest/@lock/@endEditing）必须还能正常工作。**一定要打开 SessionView.vue 看一下它怎么调用 SushiCards，确认所有 emit 名字和 prop 名字都没改（可以加新但不能改旧签名）**。
- ensemble 模式不依赖 Candidate/EditingState 类型（items 是通用 Record<string, any>[]），让它独立可用。
- 不删任何旧文件（VariantPicker 只是加注释）。
- ElementPlus 直接 `<el-xxx>`。
- 样式 scoped。
- 中文字案自然。
- 遇到不确定就 Read 文件确认。
