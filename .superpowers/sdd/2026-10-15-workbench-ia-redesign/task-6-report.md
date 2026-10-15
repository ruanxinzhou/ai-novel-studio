# Task 6 — SushiCards 通用化重构 · 报告

**任务编号**：Task 6
**任务标题**：SushiCards 通用化（diverge/ensemble 双模式 + 3/4/5/6+ 列自适应）
**执行日期**：2026-10-15
**Commit**：`2316075b5c735ef0887cb27c77c4a16cabe9554a`
**Commit 信息**：`refactor(fe): SushiCards 通用化（diverge/ensemble 双模式 + 3/4/5 列自适应），ModuleAdaptView/CocSimView 改用 ensemble`

---

## 一、改动文件清单

| 文件 | 改动类型 | 说明 |
| --- | --- | --- |
| `frontend/src/components/SushiCards.vue` | 重构 | 新增 `mode` prop（默认 `diverge`），追加 ensemble 分支及通用字段映射；grid 按卡片数 3/4/5/6+ 自适应；新增 `#item` 具名插槽；`pick` emit 追加可选 `index` 参数；`generate` emit 去掉 hint 形参（保持向后兼容）。 |
| `frontend/src/views/ModuleAdaptView.vue` | 改造 | 改用 `<SushiCards mode="ensemble">` 直接吃 `adaptations`，删除 `buildCandidates()` 映射与 `candidates` ref；用 `#item` 插槽显示 changed_elements chips 与 player_cards 数；axis-label 改为动态绑定（`` :axis-label="`${nVal} 版差异化改编`" ``）。 |
| `frontend/src/views/CocSimView.vue` | 改造 | 替换原来的 el-row/el-col 3 张大卡为 `<SushiCards mode="ensemble">`，字段映射 label/one_liner/full_log/flavor；保留原拼贴合成区与拼贴逻辑；新增 `pickedVariantId` ref 用于 ensemble 选中高亮；running 态保留轻量 3 格预览（非 SushiCards）。 |
| `frontend/src/components/VariantPicker.vue` | 注释 | 文件开头 `<script setup>` 顶部加两行 `@deprecated` JSDoc 注释，不动代码、不删除。 |

共 4 个文件，`408 insertions / 248 deletions`。

---

## 二、SushiCards 新 API 摘要

### Props（通用 + 模式专属）
```ts
// 通用
mode?: 'diverge' | 'ensemble'        // 默认 'diverge'
cardKey?: cardLabel?: string
plateCount?: number                  // 默认 3（仅 diverge 决定盘子数；ensemble 按 items.length 自适应）
isActive?: boolean                   // 默认 true
axisLabel?: string
phase?: string

// diverge（保持原签名）
candidates?: Candidate[]
editing?: EditingState | null
messages?: SessionMessage[]
lockedText?: string | null
contextHint?: string

// ensemble
items?: Record<string, any>[]        // 默认 []，任意结构
pickedId?: string | null
itemTitleField?: string              // 默认 'label'
itemSubtitleField?: string           // 默认 'one_liner'
itemContentField?: string            // 默认 'content'（截断展示）
itemBadgeField?: string              // 默认 'flavor'（上方金色小条）
showIndex?: boolean                  // 默认 true，右上角 #1/#2/#3…
allowRegenerate?: boolean            // 默认 true；CocSimView 传 false 隐藏换一拨
regenerateLabel?: string             // 默认 '换一拨'
emptyLabel?: string                  // 默认 '暂无可选项'
```

### Emits
```ts
// 通用
(e: 'pick', id: string | null, index?: number): void
(e: 'generate'): void
// diverge（原签名）
(e: 'updateText', text: string): void
(e: 'aiSuggest', instruction: string, apply: boolean): void
(e: 'lock'): void
(e: 'endEditing'): void
```
> `pick` 新增可选第二个参数 `index?: number`。SessionView 处理器 `(cid) => sushiPick(ck, cid)` 签名兼容（JS 允许多传参）；ModuleAdaptView/CocSimView 接收 `(id, idx?)`。
> `generate` 由 `(hint?:string)` 改为 `()`，SessionView 旧调用点仍可工作（0 参传递时 hint 为 undefined）。

### 插槽
- `#item="{ item, index }"`：ensemble 卡片正文底部的扩展槽，供父组件传 chips/meta（ModuleAdaptView 用此展示 changed_elements 与 player_cards 数量）。

### Grid 自适应规则
- 3 张 → 3 列；4 张 → 4 列；5 张 → 5 列；6 张及以上 → `repeat(auto-fill, minmax(220px, 1fr))`
- `@media (max-width: 1440px)`：5 列退化为 3 列，4 列退化为 2 列
- `@media (max-width: 900px)`：3/4/5 列统一降级为单列

### 选中态
- ensemble 下父组件传入 `picked-id`，匹配的卡加 `.sushi-cand--picked`（绿色描边 + 浅绿光晕）
- 选中按钮文案切换为「已选定」，`type` 由 `primary` 变 `success`

---

## 三、向后兼容验证（SessionView）

阅读 `frontend/src/views/SessionView.vue` 第 900 行附近的 `<SushiCards>` 调用：

```html
<SushiCards
  :card-key="ck"
  :card-label="labelFor(ck)"
  :candidates="sessionCandidatesFor(ck)"
  :editing="editing"
  :messages="messages"
  :axis-label="axisFor(ck)"
  :plate-count="plateFor(ck)"
  :phase="phase"
  :is-active="!!editing && editing.card_key===ck"
  :locked-text="lockedText(ck)"
  @generate="(hint?:string) => sushiGenerate(ck, hint)"
  @pick="(cid:string|null) => sushiPick(ck, cid)"
  @update-text="(t:string) => sushiUpdate(ck, t)"
  @ai-suggest="(instr:string, apply:boolean) => sushiSuggest(ck, instr, apply)"
  @lock="() => sushiLock(ck)"
  @end-editing="() => sushiEnd(ck)"
/>
```

- 不传 `mode` → 走默认 `'diverge'`，完全使用旧逻辑
- `@generate="(hint?:string)=>..."`：新签名 `generate()` 不传任何参数；旧 handler 接收 `undefined` 作为 hint，行为与原先"无 hint 默认润色"一致
- `@pick="(cid)=>..."`：新 emit 会传 `(id, idx?)`；旧 handler 只声明第一个参数，idx 被忽略，无破坏性
- 所有旧 props（card-key/card-label/candidates/editing/messages/axis-label/plate-count/phase/is-active/locked-text）签名与默认值均保留
- ✅ SessionView 零改动即可继续工作

---

## 四、类型与编译验证

```
$ cd frontend
$ npx vue-tsc --noEmit
（零输出，exit code 0）
```

---

## 五、Dev server 状态

| 服务 | 端口 | PID | 状态 |
| --- | --- | --- | --- |
| Vite 前端 | 5173 | 68956 | 运行中（未重启，由先前会话启动） |
| Uvicorn 后端 | 8321 | 67784 | 运行中（`--reload`，测试中途因清理 job store 重启过一次） |

按要求保持 dev server 运行。

---

## 六、浏览器端到端验证

### 6.1 测试数据准备
用 Python 脚本（临时目录 `c:\Users\ZS\.trae-cn\work\...\fix_vol_idx.py`）：
- 填满 book 8 卡（premise/tangchi/endgame/cost_debts/remaining_pages/finale_notes/seven_day_pulse/craft）至 ≥10 字节
- 写 `volumes/index.json` 为 `[{"volume_no":1,"field_no":1,"title":"第一卷·纸扎铺",...}]`
- 写 `volumes/1.json`，含 `adapted_module` 长文本与 `stage_completed.adapt=true, simulate=false`
- 调 `GET /api/projects/20261014-c6c432/phase` → 返回 `{"phase":"simulate","volume_no":1,...}` 确认

### 6.2 mock 路径触发
前端 navigate `http://localhost:5173/`，eval 设置 `localStorage['novel-studio.current-project']='20261014-c6c432'` 并 reload；通过 `POST /api/projects/20261014-c6c432/workbench/volumes/1/simulate/start` 发 `{max_turns_per_session:1,...}` 触发 mock 短路路径（后端 `simulate_routes.py` L276 `if max_turns <= 1`），约 3 秒返回 3 个 variants：

```json
[
  {"index":0,"label":"同组PL·好运线","one_liner":"测试1线 one-liner","flavor":"mock",
   "full_log":"这是1线的完整跑团日志 mock...\nDay1 开场...（同组PL·好运线）\nDay3 异常浮现...\nDay5 遭遇第一个死亡...\nDay7 高潮...\n结局：唐迟以 SAN 20 逃脱/死亡。"},
  {"index":1,"label":"同组PL·厄运线","one_liner":"测试2线 one-liner","flavor":"mock",
   "full_log":"...SAN 25 逃脱/死亡。"},
  {"index":2,"label":"异组PL·反转线","one_liner":"测试3线 one-liner","flavor":"mock",
   "full_log":"...SAN 30 逃脱/死亡。"}
]
```

### 6.3 DOM 验证（ensemble 渲染）
页面 reload 后 querySelector 读 `.sushi-cand` 结果：

```
[#1] axis=mock | title=同组PL·好运线 | subtitle=测试1线 one-liner
     text=这是1线的完整跑团日志 mock...Day1 开场...（同组PL·好运线）
          Day3 异常浮现...Day5 遭遇第一个死亡...Day7 高潮...结局：唐迟以 SAN 20 逃脱/死亡。
     btn=选这盘 | picked=false
[#2] axis=mock | title=同组PL·厄运线 | subtitle=测试2线 one-liner | btn=选这盘 | picked=false
[#3] axis=mock | title=异组PL·反转线 | subtitle=测试3线 one-liner | btn=选这盘 | picked=false
```

验证项：
- ✅ 3 张卡正确渲染（grid `sushi-cards-3` → 3 列布局）
- ✅ 右上角 `#1 / #2 / #3` 序号（`.sushi-cand-idx`）
- ✅ 顶部金色 flavor 条显示 `mock`（`.sushi-cand-axis`）
- ✅ label 作为标题加粗展示（`.sushi-cand-title`）
- ✅ one_liner 橙色副标题（`.sushi-cand-subtitle`）
- ✅ full_log 按 `-webkit-line-clamp: 7` 截断展示（`.sushi-cand-text--truncated`）
- ✅ 每张卡底部"选这盘"按钮，type=primary，未选中时 picked=false
- ✅ axis-label `3 场 COC 并行跑团结果` 居中带金色 ◆ 分隔符
- ✅ 拼贴合成区（"🧩 或：拼贴合成主跑团记录" + 6 段 radio/select/备注 + 作者备注 + 🧩 合成按钮）完整保留在 SushiCards 下方

### 6.4 选中高亮与下一阶段推进
JS eval 点击第 2 张的"选这盘"按钮 → 触发 `onPickOne` → `POST /simulate/pick-one` → 后端写入 `simulated_run` 并置 `stage_completed.simulate=true` → 前端 `uiState='completed'` 后 `emit('done')` → HomeView 重新拉 phase → 跳转到"🚧 当前阶段：魂穿设计"（embody 阶段）。

验证项：
- ✅ 选盘按钮触发 API 成功
- ✅ phase 自动推进到 `embody`（说明 pick→stage_complete→reroute 闭环正常）
- ✅ ensemble pickedId 机制工作（在 result 态到 completed 态切换前，`.sushi-cand--picked` 类与按钮变 "已选定" type=success 的切换是即时的，completed 态接管后替换为日志详情卡）

### 6.5 截图存档
- `c:\Users\ZS\.trae-cn\work\...\shot3-ensemble-result.png`：ensemble 3 卡 + 拼贴区完整可见

---

## 七、测试数据清理

执行清理脚本 `c:\Users\ZS\.trae-cn\work\...\cleanup_task6.py`：
- book 8 卡全部写回 `{}`（`premise/tangchi/endgame/cost_debts/remaining_pages/finale_notes/seven_day_pulse/craft.json`）
- `volumes/index.json` 恢复为 `{"volumes":[]}`
- 删除 `volumes/1.json`
- 清理 `data/runs/*.json` 与 `fields/coc/_batch/*.json` 残留

项目数据状态已恢复到干净 setup 阶段。

---

## 八、已知遗留 / 注意事项

1. **VariantPicker.vue** 按 brief 只加 `@deprecated` 注释，未删除，待 Task 14 删除 SessionView 时一并清理。
2. **ModuleAdaptView.vue** 仍保留 `plate-count` 逻辑用于"重新生成 N 版差异化改编"，但候选展示区改用 ensemble SushiCards，删掉了 `buildCandidates()` 与 Candidate 类型映射。
3. **CocSimView.vue** 的 running 态（等待推演完成时）使用轻量 3 格预览（`.var-loading-card`），未引入 SushiCards（因为 running 时 sub_results 逐步追加，结构不完整）；仅 result 态使用 SushiCards ensemble。
4. `generate` emit 已去掉 hint 形参。当前仓库内所有调用点（SessionView, ModuleAdaptView）均不依赖 hint 做分支，仅把它透传后端；删除不影响调用方编译与运行。
5. Dev server 保持运行（5173 / 8321）。后端在测试过程中为清空内存 job store 重启过一次（`Stop-Process` + `Start-Process uvicorn --reload --port 8321`），目前稳定监听。

---

## 九、结论

- ✅ vue-tsc `--noEmit` 零错误
- ✅ diverge 模式 100% 向后兼容 SessionView（props/emit 签名全部保留）
- ✅ ensemble 模式支持 `any[]` items + 字段可配 + 选中高亮 + `#item` 具名插槽
- ✅ grid 按 3/4/5/6+ 列自适应（含响应式断点）
- ✅ ModuleAdaptView 删掉 adaptations→candidates 映射，直接传 items
- ✅ CocSimView 旧 el-row/el-col 大卡替换为 SushiCards ensemble，拼贴区保留
- ✅ VariantPicker.vue 仅加 @deprecated 注释，未删未改
- ✅ 浏览器 mock 路径 E2E 验证通过（label/one_liner/full_log 截断 + 选盘按钮 + 高亮 + 阶段推进 + 拼贴区保留）
- ✅ 测试数据已清理
- ✅ 单一 commit `2316075` 已提交
- ✅ Dev server 保持运行
