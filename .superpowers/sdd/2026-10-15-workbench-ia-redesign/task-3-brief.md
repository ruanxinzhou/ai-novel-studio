# Task 3 Brief：前端骨架组件 + App.vue 重构

## 项目路径
`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`

前端已在 `http://localhost:5173` 运行（Vite dev server，热重载），后端在 `http://127.0.0.1:8321`（uvicorn --reload）。不要重启 dev server。

## 目标
建立工作台新 UI 骨架：状态栏 + 书脊左栏 + 抽屉 + AI 浮动气泡 + HomeView 容器 + 最小 BookSetupView；App.vue 从 el-tabs 三栏切换改为"项目选择门 → HomeView 路由"。完成后浏览器访问 `http://localhost:5173/` 选项目后应能看到新布局，phase 由 `GET /api/projects/{pid}/phase` 驱动。

## 现状（必读）
- `frontend/src/main.ts`：**没有 vue-router**，直接 `createApp(App).use(ElementPlus, { locale: zhCn }).mount('#app')`。本任务保持无路由，App.vue 直接用 v-if 切换"项目选择门"和"HomeView"。
- `frontend/src/state.ts`：目前有 `currentProjectId`（localStorage 持久化）和 `activeTab`（旧 tab 状态）。需要保留 `currentProjectId`；**不删 `activeTab`**（可能有旧视图读它，Task 14 清理时再删）；新增全局阶段状态。
- `frontend/src/App.vue`：旧的 el-tabs 三栏布局 + 12 个旧 view import。全部替换为"项目门 + HomeView"结构。
- `frontend/src/api.ts`：有 `projectsApi.list()` 返回 `{projects: ProjectMeta[]}`；`ProjectMeta` 是 `{id, title, intro, created_at, updated_at}`。本任务**不新增 api.ts 方法**——HomeView 直接 fetch `/api/projects/{pid}/phase` 和 `/api/projects/{pid}/workbench/volumes`（创建卷端点是 POST `/api/projects/{pid}/workbench/volumes`，注意不是 `/volumes`，因为旧的 `/volumes` 是 create_next 语义）。
- 8 张书卡：`premise/tangchi/endgame/cost_debts/remaining_pages/finale_notes/seven_day_pulse/craft`，端点 `GET /api/projects/{pid}/book/{card}` 返回 `{card, data}`，data 为 dict（空卡为 {}）。

## 要改/新建的文件（共 8 个）

### 1. `frontend/src/state.ts` — 加全局阶段状态
在现有文件后面追加（不要删 currentProjectId/activeTab/watch 代码）：
```ts
// 工作台全局阶段状态（由 HomeView 在 /phase 请求后填充）
export const currentPhase = ref<string>('')
export const currentVolumeNo = ref<number>(0)
export const currentChapterNo = ref<number>(0)
export const currentVolumeKind = ref<string>('dungeon')
export const currentIsLastChapter = ref<boolean>(false)

// 抽屉开关
export const drawerOpen = ref(false)
// AI 气泡开关
export const aiChatOpen = ref(false)
```

### 2. 新文件 `frontend/src/components/StatusBar.vue`
照 `docs/superpowers/plans/2026-10-15-workbench-ia-redesign.md` L594-646 的代码创建，小调整：
- contamination prop 默认先写死 12（后续 Task 11 接 protagonist API）
- PHASE_LABELS 补全：`setup:'开书设定', adapt:'剧本改编', adapt_empty:'新卷', simulate:'模拟跑团', embody:'魂穿设计', scaffold:'卷内详设', rhythm:'节奏骨架', briefing:'章前规划', desk:'日更', settle:'杀青', interlude:'蓝星间章'`
- 齿轮按钮点击 → `drawerOpen.value = true`

### 3. 新文件 `frontend/src/components/SpineNav.vue`
照 plan L651-711 创建，小调整：
- props `volumes: any[]`（格式：`[{no, title, icon, contamination?, chapters:[{no,title,finalized,brief}]}]`）—— Task 3 里只传空数组 / 硬编码演示数据，书脊真实数据加载是 Task 10
- emit: `selectVolume(no)`, `selectChapter(vn, cn)`, `addVolume`, `goSetup`
- 样式：保持左栏 240px 宽，背景 #fafafa，active 态浅蓝
- 因为 spineVolumes 在 HomeView 里还没真实数据，所以书脊卷列表会空，只有"书设定"顶项 + "➕ 新副本"按钮——这是预期的。**不要**在 SpineNav 里自己 fetch 数据。

### 4. 新文件 `frontend/src/components/SettingsDrawer.vue`
照 plan L716-746 创建，小调整：
- 用 el-drawer，v-model 绑定 drawerOpen（computed get/set）
- 6 个分组：📖 书稿 / 🗂 实体库 / 📚 资料 / 📝 写作规范 / 🧪 高级 / 📂 项目
- 每个分组里放一段灰色占位文字（"内容迁移中"），不要引 EntitiesView/其他旧组件
- 默认展开"📖 书稿"

### 5. 新文件 `frontend/src/components/AIChatFloat.vue`
照 plan L751-806 创建，小调整：
- 右下角浮动圆形按钮，点击展开 380×480 对话面板
- 面板头部"💬 AI 聊一谈" + 关闭按钮
- 中部 context hint（按 currentPhase 显示中文提示）+ 快捷按钮（按 phase 显示不同建议词）
- 底部 textarea，回车暂不发送（send() 留空函数，加 TODO 注释接 sessionsApi）
- **不要用 aiChatOpen**，组件内部用 `const open = ref(false)` 自管，plan 里 state 里的 aiChatOpen 可 export 但 AIChatFloat 自己维护 open 状态即可（plan 里 aiChatOpen 给将来外部快捷键用，这里先 export 占位）

### 6. 新文件 `frontend/src/views/HomeView.vue`
照 plan L811-888 创建，重要修正：
- import 路径：`import BookSetupView from './BookSetupView.vue'`（同目录）
- `loadPhase()`：fetch GET `/api/projects/{pid}/phase`，填充 phase/currentPhase/currentVolumeNo/currentChapterNo/currentVolumeKind/currentIsLastChapter（来自返回的 `is_last_chapter` 字段）
- **创建新卷端点**：`POST /api/projects/{pid}/workbench/volumes`（不是 plan 里写的 `/volumes`），body `{"title":""}`，返回 `{ok, volume_no, volume}`
- 路由分支：
  - `phase === 'setup'` → `<BookSetupView @done="loadPhase" />`
  - `phase === 'adapt_empty'` → 空状态提示"开始新一卷"，按钮"创建新副本卷"点击调用 onAddVolume
  - 其他 phase → `<div class="placeholder-view">当前阶段：{{ phaseLabel }}（视图开发中）</div>`，显示中文阶段名
- spineVolumes：本任务**不 fetch 卷列表**，直接硬编码 `[]`，加 TODO 注释 `// TODO: Task 10 接入书脊真实数据`
- contamination：先写死 0（后续 Task 11 接 protagonist API），加 TODO
- expose 一个 `refresh()` 方法（defineExpose），方便外部刷新
- 注意 HomeView 里 onMounted 时如果 currentProjectId 变化（用户在 App.vue 切项目），要重新 loadPhase。建议 watch currentProjectId：
  ```ts
  watch(currentProjectId, () => { if (currentProjectId.value) loadPhase() })
  ```

### 7. 新文件 `frontend/src/views/BookSetupView.vue`
照 plan L895-940 创建，小调整：
- 8 个 el-collapse-item 对应 8 张卡，标题用中文：
  - premise: 核心设定（含讣告五铁律）
  - tangchi: 唐迟起始状态
  - endgame: 长线伏笔与终局
  - cost_debts: 代价与欠账
  - remaining_pages: 剩余页码（全书规模）
  - seven_day_pulse: 七日脉冲（节奏规律）
  - finale_notes: 终局注记
  - craft: 写作宪法
- onMounted 里循环 fetch `/api/projects/{pid}/book/{card}` 检查每张卡是否已填（返回的 data 是 dict，Object.keys(d.data||{}).length>0 算已填）
- 未填的显示红色"未填入"，已填显示绿色"✓ 已填入"
- 底部按钮"完成开书设定，开始第一卷" → emit('done')（实际只是刷新 phase，用户即使卡片没填完也可以点——后端 phase 判定会自动判断 8 卡是否填完，前端按钮只是触发 loadPhase）
- **重要**：BookSetupView 目前只是状态展示，不提供编辑表单（编辑留到 Task 13）。如果用户没有填卡，需要引导他们"请先通过 API 或后续编辑界面填入 8 张卡"；不过为了测试，按钮点击后直接 emit('done')，后端会根据真实卡状态返回 setup 或 adapt_empty，前端自动重新路由，不需要额外拦截。

### 8. 重写 `frontend/src/App.vue`
完全替换为 plan L947-979 代码，但有几个修正：
- **保留项目选择器**（plan 里的项目门太简陋，用现有 el-select 样式）：
  - `<div v-if="!currentProjectId" class="project-gate">` 里放标题"《第七日的亡者》· 写作间" + el-select 选项目
  - 项目列表通过 `fetch('/api/projects').then(r=>r.json())` 拿（后端返回 `{projects:[...]}` 或直接数组？验证：先读现有 api.ts，projectsApi.list() 返回 `{projects}`，所以取 `.projects || []` 兼容）
  - 用现有 currentProjectId 来自 state.ts
- `<HomeView v-else />` 渲染主界面
- 删光所有旧 view 的 import（DeskView/SessionView/... 共 11 个）
- 保留全局样式 `#app { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; color:#222; margin:0; }`，加 `html,body,#app { height:100%; margin:0; padding:0; }` 让 HomeView 的 height:100vh 生效
- 删 el-container/el-header/el-main/el-tabs 等旧结构，全删

main.ts 不需要改（App.vue 仍然是默认导出组件）。

## 验证步骤

1. **TypeScript 类型检查**（如果项目有 vue-tsc）：
   ```powershell
   cd C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio\frontend
   npx vue-tsc --noEmit 2>&1 | Select-Object -First 50
   ```
   如果 vue-tsc 不通过，修复所有类型错误。

2. **浏览器验证**：
   - 访问 `http://localhost:5173/`
   - 应看到项目选择门（下拉框选项目）
   - 选 `20261014-c6c432`（旧项目，book cards 未满）→ 应看到新布局（状态栏 + 左栏书脊 + 主区 BookSetupView 显示"开书设定"8 个折叠项，应都显示"未填入"或部分已填）
   - 访问后 F12 控制台无红色错误
   - 点右上角齿轮 → 抽屉滑出显示 6 个分组
   - 点右下角💬 → AI 浮动面板展开
   - 点"完成开书设定"按钮 → 触发 loadPhase，若卡片未满应该仍停在 BookSetupView；若卡片已齐会跳到对应阶段

3. **验证旧代码不报错**：
   - 不要删除/修改其他旧 views 文件（Task 14 再删）
   - 不要改 api.ts、main.ts
   - 不要删 components/ 下的 JobProgressBar.vue/SushiCards.vue/VariantPicker.vue

## 报告
在 `.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-3-report.md` 写：
- commit hash
- 改动清单（新建的 6 个文件 + 修改的 2 个文件）
- vue-tsc 输出（或 vite 热重载无错误的截图/描述）
- 浏览器验证结果
- 已知遗留（spineVolumes/contamination 硬编码，待后续任务接入）

## Commit
单一 commit：
```
feat(fe): HomeView 容器+状态条+书脊+抽屉+AI气泡骨架，App.vue 砍 tabs
```

## 注意
- 所有中文文案保持自然
- 不要重启 vite 或 uvicorn dev server（让热重载自动生效）
- 用 Element Plus 组件（ElButton/ElTag/ElProgress/ElDrawer/ElCollapse/ElSelect/ElOption/ElIcon 等），按需 import，不要全量注册（现有项目是按需 import 的吗？看 App.vue 里是直接用 `<el-xxx>` 标签，靠 main.ts 的 `app.use(ElementPlus)` 全量注册，所以模板里直接用 `<el-button>` 即可，不必手动 import）
- 样式 `<style scoped>`，不要写全局样式污染
- 不要改/删其他不相关文件
- 遇到不确定的地方（比如 projects API 返回结构），写一个临时 `console.log` 看实际返回再决定；不要猜。
