# Task 12 报告：InterludeView 蓝星间章 4 步

## 提交信息
- **Commit**: `3565015`
- **Message**: `feat(fe+be): InterludeView 蓝星间章4步（违和点/后遗症/下卷钩子/章数）+间章phase判定修复`
- **变更规模**: 6 files changed, 1404 insertions(+), 2 deletions(-)

## 变更清单

### 1. 新增后端路由 `backend/app/api/interlude_routes.py`（~479 行）
函数式注册 `register_interlude_routes(app, *, require_project, volumes_for, desk_for, chore_provider)`。
路由前缀 `/api/projects/{pid}/workbench/volumes/{vol_no}/interlude`，4 个端点：

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/` | 返回 `{volume, plan, can_start_writing}` |
| POST | `/ai-suggest` | 4 步 AI 建议：weihedian/houchiyizheng/xiajuangouzi 各返回 3 候选；zhangshu 返回单对象 `{suggested_chapters, words_per_chapter, reason}` |
| POST | `/save-plan` | 写入 `vol.interlude_plan[step]`，自动维护 `steps_done`、`updated_at` |
| POST | `/finalize` | 校验 steps_done==4，设 finalized_at，desk.ensure 创建 N 章，第 1 章写 brief.selected（is_interlude=True，标题「回家：第一件不对劲的事」） |

- **Mock 短路**：`volume_kind=='interlude'` 即走 mock（硬编码克苏鲁风格 3×3+1 条，含镜子慢半拍/通讯录多号/手心藤壶疤、海声/幻听/影子慢半拍、快递/新闻/室友异常等）。
- **LLM 路径**：从上一卷 dungeon 读取 `settle.blue_star_hook/cost_summary`、`tangchi_story_outline`、`host_character` 组装上下文，LLM 失败回退 mock。
- **章号分配**：`max(desk.all_nos())+1` 起，设 `field_no=v.field_no`、`is_interlude=True`，第 1 章写 brief，其他章 brief=None。

### 2. 修复 `backend/app/api/phase_routes.py` 间章判定 bug
原逻辑 `if not last_vol.interlude_plan:` 在 interlude_plan 存在但 `finalized_at` 为空（即 4 步做了部分但未 finalize）时会 fall through 到 stage_order（adapt/simulate/embody/scaffold/rhythm），错误地 phase='adapt'。
改为：
```python
if not last_vol.interlude_plan or not last_vol.interlude_plan.get("finalized_at"):
    return {"phase": "interlude", ...}
```
与 DeskView 已有的 `ch.is_interlude → desk` 判定配合，间章全链路（规划 → finalize → desk 写章 → settle）现在完整闭合。

### 3. 修改 `backend/app/api/main.py`
在 `register_brief_routes(...)` 之后 import 并注册：
```python
from .interlude_routes import register_interlude_routes
register_interlude_routes(app, require_project=_require_project, volumes_for=_volumes_for,
                          desk_for=_desk_for, chore_provider=_chore_provider)
```

### 4. `backend/app/wizard/store.py`
ChapterWork 已有 `is_interlude: bool = False` 字段，`from_dict` 正确处理，`assign_field(is_interlude=True)` 支持，asdict 自动序列化。**无需修改**。

### 5. `frontend/src/api.ts`（+65 行）
新增 TS 接口：`InterludeOption`、`InterludeZhangshu`、`InterludePlan`、`InterludeGetResp`、`InterludeStep`。
新增 `interludeApi`，4 方法：`get`、`aiSuggest`、`savePlan`、`finalize`，外加 `getVolume: adaptApi.getVolume` 复用。

### 6. 新增 `frontend/src/views/InterludeView.vue`（~520 行）
- 顶部标题「蓝星间章 · 短暂的喘息」，副标题克苏鲁文案。
- `el-steps` 4 步：违和点 → 后遗症 → 下卷钩子 → 章数。
- 前 3 步使用 `SushiCards mode="ensemble" plateCount=3 allowCustom=true allow-regenerate=true`，支持点选/重抽（rerun 迭代）/自拟/微调，每步 pick 自动进入下一步并调用 `save-plan`。
- 第 4 步：双 `el-slider`（章数 1-3、字数 2500-5000 step 500）+ 总篇幅数字展示 + `el-input textarea` 节奏理由（可重新生成/手动编辑）。
- 点「❆ 定稿 · 进入间章」先 save-plan 保存 zhangshu，再 finalize，finalize 成功后 `emit('done')`。
- 冷蓝灰色调（`#1c2430 → #0a0e14` 径向渐变 + `#7aa2c1` 冷蓝强调色）区别于 SettleView 的血红古铜。
- 加载已保存 plan 时自动恢复选中项并跳到第一个未完成步骤；已 finalize 显示成功结果页。

### 7. 修改 `frontend/src/views/HomeView.vue`
- 新增 `import InterludeView from './InterludeView.vue'`。
- 在 SettleView 分支后增加：
  ```html
  <InterludeView v-else-if="phase === 'interlude'" :vol-no="currentVolumeNo" @done="loadPhase" />
  ```
- `PHASE_LABELS` 中已有 `interlude: '蓝星间章'`，无需补加。

## 验证结果
- 后端导入：`python -c "from app.api.main import app; print('OK', len(app.routes))"` → **OK 147**（新增 4 条路由已挂载）。
- 前端类型检查：`npx vue-tsc --noEmit` → **exit 0，无类型错误**。

## 已知限制/说明
1. Mock 触发条件使用 `volume_kind=='interlude'`，与 brief 描述一致，简单且无歧义。
2. LLM 路径为占位式调用（`chore_provider("interlude")` + `complete_structured`），schema 与 mock 数据字段对齐；当 LLM 抛错会自动回退到 mock 并附 `llm_error`。
3. finalize 时第 1 章 brief 的 summary 使用 weihedian.content（违和点内容）作为钩子概要，标题硬编码「回家：第一件不对劲的事」；后续章节未预填 brief（is_interlude=True 直接进 desk 不走 briefing）。
4. 未执行 `git push`。

## 文件清单
- `backend/app/api/interlude_routes.py`（新）
- `backend/app/api/main.py`（改）
- `backend/app/api/phase_routes.py`（改）
- `frontend/src/api.ts`（改）
- `frontend/src/views/InterludeView.vue`（新）
- `frontend/src/views/HomeView.vue`（改）
