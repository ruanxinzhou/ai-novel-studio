# Task 13 完成报告：SettingsDrawer 抽屉填内容

## 概述
将 `frontend/src/components/SettingsDrawer.vue` 从 6 个占位 `el-empty`（"内容迁移中"）改造为可用的全局工具面板，覆盖书稿 / 实体 / 资料 / 写作规范 / 高级 / 项目六个分组。未新增后端端点，未删除旧视图。

## 改动文件
- `frontend/src/components/SettingsDrawer.vue`（唯一改动）

## 分组实现对照

### 📖 书稿（manuscript）
- 顶部卡片展示当前项目 title / intro / id（来自 `projectsApi.detail`）
- "📖 书卡设定"按钮：写 `currentPhase.value = 'setup'` 后关闭抽屉，App.vue 自动切到 BookSetupView
- "📂 项目列表"按钮：调 `projectsApi.list` 拉列表，`ElMessageBox.prompt` 让用户输序号或 id 选择，直接写 `currentProjectId.value` 触发 App.vue 切换
- "➕ 新建项目"按钮：两步 prompt（title → intro），调 `projectsApi.create`，写入 `currentProjectId` 并关抽屉

### 🗂 实体（entities）
- 打开抽屉时 `entitiesApi.list(pid, {})` 拉数据
- `el-table` size="small" + stripe + max-height="240" 展示 name/type/status 三列
- type/status 用 `ENTITY_TYPE_LABELS` / `ENTITY_STATUS_LABELS` 翻译并以 el-tag 显示
- "+ 快速添加"三步 prompt（name → type → summary），调 `entitiesApi.add`，成功后刷新
- "完整实体库"按钮 disabled 占位并标注 "Task 14 后开放"

### 📚 资料（materials）
- `Promise.all` 并行调 `corpusApi.types()` / `corpusApi.tags()`
- 类型/标签分别以 el-tag 展示（"type × count"），标签区 max-height 120px 滚动
- "📥 导入拆书"两步 prompt（dir → title），调 `corpusApi.importChaishu`，成功 ElMessage 提示新增条数并刷新统计

### 📝 写作规范（craft）
- `skillsApi.list()` 拉模块列表
- 模块以 `el-tag effect="plain"` 展示 title，容器 max-height 240px 滚动
- "查看/编辑模块"按钮 disabled 占位并 tooltip 说明 "Task 14 从旧 WorkshopView 迁移"

### 🧪 高级（advanced）
- 当前模型硬编码显示 `MiniMax-M3.1-Flash-Preview`（el-tag type="success"）
- "🔧 Prompt 工坊"按钮点击 ElMessage.info 占位提示
- "📤 导出 Vault"按钮调 `vaultApi.export(pid)`，成功显示返回的 `out_dir`（绿色 code 块）

### 📂 项目（project）
- `projectsApi.detail(pid)` 返回的 created_at / updated_at / id 以 kv-row 列表展示
- 已完成层数：遍历 LAYERS（intro/outline/plot/draft/polish），按 `layers[layer].current` 是否存在判断完成，用 el-tag 绿色+✓ / 灰色显示
- "🏠 返回首页"危险按钮：`ElMessageBox.confirm` 确认后清空 `currentProjectId` 并关抽屉、清空 currentPhase，App.vue 自动回到项目选择页

## 加载/状态管理
- `loading` / `loaded` 两个 ref<Record>，分组独立 loading 标志
- `watch(drawerOpen, immediate 行为通过 onMounted + watch 组合实现)`：首次 open=true 时 `Promise.allSettled` 并行拉四个分组数据，仅拉一次（loaded.all flag）
- 所有 API 调用 try/catch 包裹，错误统一 ElMessage.error 提示
- 所有主操作按钮加 el-tooltip 提示
- 危险操作（回首页）走 ElMessageBox.confirm

## 样式
- 保持简洁工具面板风格，浅灰卡片+虚线分隔+小 tag，不用克苏鲁暗色
- 所有操作按钮 `size="small"`，gap 6px flex-wrap 排布
- 列表 max-height 240px overflow-y:auto（实体表格、技能/标签列表）
- 新增 `.current-project-card` / `.kv-row` / `.vault-out` 等局部样式类

## 类型校验
```
cd frontend; npx vue-tsc --noEmit
→ exit code 0，无类型错误
```

## Commit
- 消息：`feat(fe): SettingsDrawer 填充内容（书稿/实体/资料/规范/高级/项目六组）`
- 范围：仅 `frontend/src/components/SettingsDrawer.vue`
- 未执行 git push

## 验证要点（对应 brief 验收项）
1. ✅ 6 个 collapse-item 全部替换 el-empty 为真实内容
2. ✅ 书稿组：当前项目展示、setup 跳转、项目列表/切换、新建项目
3. ✅ 实体组：表格列表 + 快速添加
4. ✅ 资料组：类型/标签 el-tag 统计 + 导入拆书按钮（成功/失败 ElMessage）
5. ✅ 规范组：skillsApi.list 模块 el-tag 列表
6. ✅ 高级组：Vault 导出展示 out_dir、模型名硬编码、Prompt 工坊占位
7. ✅ 项目组：详情 + 层数完成度 + 返回首页（清 currentProjectId）
8. ✅ 未新增后端端点，未改后端，未删除旧视图
9. ✅ vue-tsc --noEmit 通过
