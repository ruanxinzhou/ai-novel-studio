# Task 3 报告：前端骨架组件 + App.vue 重构

## Commit
- hash: `07f9e7215e2941fed5cdc2c42d2baf4d88313752`
- message: `feat(fe): HomeView 容器+状态条+书脊+抽屉+AI气泡骨架，App.vue 砍 tabs`
- 统计：8 files changed, 1085 insertions(+), 61 deletions(-)

## 改动清单

### 修改（2 个文件）
1. `frontend/src/state.ts`：保留 `currentProjectId` / `activeTab`，追加工作台全局阶段状态 `currentPhase/currentVolumeNo/currentChapterNo/currentVolumeKind/currentIsLastChapter/drawerOpen/aiChatOpen`。
2. `frontend/src/App.vue`：完全重写为"项目选择门 + HomeView"结构。删除了 11 个旧 view import 与 el-tabs 三栏布局；保留标题与 el-select 项目选择器；添加 `html,body,#app { height:100% }` 全局样式，使 HomeView 100vh 生效；通过 `fetch('/api/projects')` 获取项目列表，兼容 `{projects}` 与直接数组两种返回。

### 新建（6 个文件）
1. `frontend/src/components/StatusBar.vue`：顶部状态栏，含书名、卷章位置、阶段标签（11 个阶段中文映射）、污染进度条（默认写死 12）、齿轮按钮（点击打开抽屉）。
2. `frontend/src/components/SpineNav.vue`：240px 左侧书脊栏，props `volumes/activeVolumeNo/activeChapterNo`，emit `selectVolume/selectChapter/addVolume/goSetup`；顶项"📖 书设定"、卷章折叠列表、底部"➕ 新副本"按钮。Task 3 由父组件传 `[]`。
3. `frontend/src/components/SettingsDrawer.vue`：右侧 el-drawer，通过 computed 绑定全局 `drawerOpen`；6 个分组（📖 书稿 / 🗂 实体库 / 📚 资料 / 📝 写作规范 / 🧪 高级 / 📂 项目），默认展开"📖 书稿"，每个分组内显示灰色占位"内容迁移中"。
4. `frontend/src/components/AIChatFloat.vue`：右下角 52px 圆形 FAB，点击展开 380×480 面板；头部"💬 AI 聊一谈"+关闭按钮；中部 context hint 与快捷按钮按 `currentPhase` 动态切换；底部 textarea + 发送按钮；`send()` 留空加 TODO 注释；组件内部自管 `open` 状态。
5. `frontend/src/views/HomeView.vue`：根容器（StatusBar + SpineNav + 主区 + SettingsDrawer + AIChatFloat）。`loadPhase()` GET `/api/projects/{pid}/phase` 填充全局 phase/volume/chapter 状态；`onAddVolume()` POST `/api/projects/{pid}/workbench/volumes`（body `{title:""}`）；路由分支：`setup → BookSetupView`，`adapt_empty → "开始新一卷"空状态+创建按钮`，其他 → 占位符"当前阶段：xxx（视图开发中）"；`watch(currentProjectId)` 在切项目时重新 loadPhase；spineVolumes/contamination 硬编码并加 TODO；`defineExpose({refresh})`。
6. `frontend/src/views/BookSetupView.vue`：开书设定 8 卡折叠面板，标题用中文（核心设定/唐迟起始状态/长线伏笔与终局/代价与欠账/剩余页码/七日脉冲/终局注记/写作宪法）；onMounted 循环 GET `/api/projects/{pid}/book/{card}`，以 `Object.keys(d.data||{}).length>0` 判断已填状态；绿色"✓ 已填入" / 红色"未填入"徽标；顶部显示"x/8 已填"统计；底部按钮"完成开书设定，开始第一卷"emit('done')，未填完也允许点击（后端自动判断）。

## 未改动
- `main.ts`、`api.ts`、`vite.config.ts` 等配置文件
- `components/JobProgressBar.vue`、`components/SushiCards.vue`、`components/VariantPicker.vue`（Task 14 再清理）
- `views/` 下旧视图文件（DeskView/SessionView/…）均保留

## TypeScript 类型检查
命令：
```
cd frontend
npx vue-tsc --noEmit
```
结果：**exit code 0，无任何类型错误输出**。

开发过程中遇到一个 vite 编译错误：`??` 和 `||` 混用需要加括号（`PHASE_LABELS[x] ?? x || '—'`），已修正为 `(PHASE_LABELS[x] ?? x) || '—'`，修复后 HomeView/StatusBar 均返回 200。

## 浏览器验证结果
访问 `http://localhost:5173/`（agent-browser headless 验证）：

1. **项目选择门**：页面显示居中卡片"《第七日的亡者》· 写作间"+副标题+el-select 下拉框，样式正常（渐变背景、圆角、阴影）。
2. **选择项目 `20261014-c6c432`**：通过 localStorage 注入后页面自动跳转到 HomeView。
3. **主布局**：
   - 顶部 StatusBar 渲染正常（书名"未命名作品"、卷章位置"—"、阶段标签"开书设定"蓝色 el-tag、污染条 0%、右上角齿轮按钮）。
   - 左侧 SpineNav 240px 宽度渲染，显示"书脊"标题 + "📖 书设定"项 + 底部"➕ 新副本"按钮（符合预期——volumes 传空数组，暂无真实卷）。
   - 主区 BookSetupView 显示"开书设定"标题、"完成下面 8 张书卡即可进入第一卷"+"5/8 已填"黄色徽标、8 张折叠项（核心设定/唐迟起始状态/长线伏笔与终局/七日脉冲/写作宪法 5 张显示"✓ 已填入"，代价与欠账/剩余页码/终局注记 3 张显示"未填入"），与后端该项目实际 book 卡状态一致。
   - 右下角蓝色圆形 FAB（💬图标）正常显示。
4. **抽屉**：点击齿轮按钮，右侧 el-drawer 从右侧滑出，标题"设置"，6 个折叠分组正确列出，"📖 书稿"默认展开并显示灰色"内容迁移中"空态。
5. **AI 气泡**：关闭抽屉后点击蓝色 FAB，右下角 380×480 面板展开，头部"💬 AI 聊一谈"+关闭按钮，context hint 显示"开书设定阶段：先和 AI 聊聊核心设定、主角起点与终局吧"，3 个 phase 相关快捷按钮（"帮我想一个讣告铁律"/"唐迟初始状态该怎么写"/"七日后终局怎么收"），欢迎语"你好！有什么想聊的，随时告诉我。"，底部 textarea 与发送按钮。
6. **F12 控制台**：浏览器渲染过程中无红色错误（组件编译 500 错误在修复 `??`/`||` 括号后已解决，此后所有模块均返回 200）。

## 已知遗留（按 brief 留待后续任务）
- `spineVolumes`：硬编码 `[]`，Task 10 接入书脊真实数据（GET volumes）。
- `contamination`：硬编码 0（StatusBar 默认 12，HomeView 传 0 覆盖），Task 11 接 protagonist API 的 `contamination_level`。
- `bookTitle`：HomeView 从 `/phase` 返回的 `book_title` 字段读取，当前后端 phase 接口未返回该字段，显示"未命名作品"；后续 phase 接口补充字段后自动展示。
- `AIChatFloat.send()`：空函数 + TODO 注释，等 sessions API 接入后实现真实发送。
- BookSetupView：仅展示卡状态，不提供编辑表单（编辑 Task 13）。
- `activeTab`：未删除（旧视图仍可能引用），Task 14 统一清理旧视图时删除。
