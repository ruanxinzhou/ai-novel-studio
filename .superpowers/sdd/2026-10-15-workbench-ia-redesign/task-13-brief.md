# Task 13: SettingsDrawer 填内容（迁移）

## 定位
把占位抽屉变成可用的全局设置/工具入口。**不**在抽屉里完整复刻旧视图（ProjectView/PipelineView/Workshop 等），而是放"高频入口+关键编辑"，把完整旧视图通过菜单链接打开（Task 14 再统一删除旧路由时，我们会判断哪些功能被抽屉完全覆盖了、哪些还要留着）。

## 范围（6 个分组，每组放实际可用内容）

所有功能都通过已存在的 api.ts 方法实现，**不新增后端端点**。

### 1. 📖 书稿（manuscript）
- 当前项目名/书名展示
- "📖 书卡设定"按钮：点击后设置 `currentPhase.value='setup'`，跳转到 BookSetupView，关闭抽屉（书卡 8 张已有视图）
- "📂 项目列表"按钮：弹一个小对话框列出 projectsApi.list() 的项目，点切换（调用现有 projectsApi；怎么切换看 App.vue 现有 currentProjectId 逻辑——如果有 selectProject 之类方法最好，没有就 localStorage 存 id 后刷新 window.location 简单处理）
- "➕ 新建项目"按钮：弹 prompt 输入 title+intro，调 projectsApi.create 后切换

### 2. 🗂 实体库（entities）
- 简单的实体快速查看列表：拉 entitiesApi.list(currentProjectId, {})，展示 name/type/status 三列的小表格（max-height 240px 滚动）
- "+ 快速添加"按钮：弹一个小 form（name/type/summary）调 entitiesApi.add
- "打开完整实体库"链接（先留空 href，提示"Task 14 前旧 EntitiesView 继续在标签页打开"——不做跳转，因为标签页已经被砍了。简单做法：不加跳转，只有快速列表+添加）

### 3. 📚 资料（materials）
- 语料统计：corpusApi.types() 和 tags()，展示类型/标签数量 el-tag
- "导入拆书"按钮：弹一个 el-input 输入目录路径，调 corpusApi.importChaishu(dir, title)——保持简单，弹一个 prompt 就行。
- 不需要做搜索/浏览（低频，Task 14 再决定）

### 4. 📝 写作规范（craft）
- 技能模块列表：skillsApi.list()，展示模块 title 列表（小 tag 可关）
- "查看/编辑模块"——留个按钮，提示"技能编辑器在旧 WorkshopView 中，Task 14 前保留原标签"——实际不做跳转
- 简单占位即可，不要做完整编辑器

### 5. 🧪 高级（advanced）
- "🔧 Prompt 工坊"按钮：提示"Prompt 工坊将在后续版本独立入口"（占位）
- "📤 导出 Vault"按钮：调 vaultApi.export(pid)，显示结果 out_dir
- 模型状态显示：如果能拿到 config 就显示当前模型名，否则显示"当前模型: MiniMax-M3.1"（硬编码也可以）

### 6. 📂 项目（project）
- 项目详情：projectsApi.detail(pid) 显示 created_at/updated_at/层数（intro/outline/plot/draft/polish）
- "重置当前阶段"危险按钮：弹 ElMessageBox.confirm，确认后调 PATCH volume 把 stage_completed 对应阶段清 false？不做——危险操作暂不做。
- "返回首页"按钮：清 currentProjectId 回 HomeView 项目选择页

## 全局设置
- 抽屉右上角齿轮附近加一个"关闭" X（el-drawer 默认有，不用加）
- 每个分组的 el-empty 换成真实内容
- 数据在 drawer 打开时 onMounted/immediate watch 加载一次；如果已经加载过不重复拉（用 loaded flag）
- 所有按钮都有 el-tooltip 提示；危险操作（删除/重置）要有 confirm
- 不引入新依赖，只用 ElementPlus

## 实现要点
- 用 `currentProjectId` 从 state.ts 取当前项目
- 数据加载用 async 函数 + try/catch + ElMessage.error 提示
- 分组还是用 el-collapse v-model activeNames，每组内容替换 el-empty 为实际 UI
- 风格保持暗色（现在抽屉默认是白色，可以改成深色 themed，或者保持默认简洁样式；不必克苏鲁暗色，抽屉是工具面板克制一点）

## 不做的事
- 不新建任何后端端点
- 不删除旧视图（Task 14 做）
- 不做完整 corpus 搜索/技能编辑器/prompt 工坊（Task 13 只是"填内容"，不是迁移所有功能）
- 不上 router 跳转（现有 HomeView 没有 router，都是 currentPhase 切换；回首页/跳 setup 用改 currentPhase 即可）

## 验收
1. SettingsDrawer 打开时 6 个分组都有真实内容（不再显示"内容迁移中"）
2. 书稿组：能看当前项目、跳转 setup、列出/切换/新建项目
3. 实体组：能列实体+快速添加
4. 资料组：能看语料统计、点导入（成功/失败 ElMessage 即可，不需要真跑导入）
5. 规范组：列出技能模块
6. 高级组：能触发 Vault 导出（成功时显示 out_dir）
7. 项目组：显示项目详情、能清 currentProjectId 返回首页
8. 不引入新后端依赖；不改后端
9. `npx vue-tsc --noEmit` 通过（若超时则不强求）

## Commit
`feat(fe): SettingsDrawer 填充内容（书稿/实体/资料/规范/高级/项目六组）`

## 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-13-report.md`

不要 git push。
