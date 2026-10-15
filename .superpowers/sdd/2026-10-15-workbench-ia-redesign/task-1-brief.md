# Task 1 Brief: 后端 Volume 数据结构扩展 + 阶段判定 API

## Context
这是 novel-studio（《第七日的亡者》写作间）工作台重构的第一个任务。当前后端有 Volume dataclass 但没有承载新创作流水线（模组→改编→跑团→魂穿→详设→节奏→日更→杀青→间章）所需的字段，也没有一个统一的"当前阶段"端点给前端判断该渲染哪个视图。本次任务是纯后端数据+API，不涉及前端改动。

## Working Directory
- 仓库根：`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`
- 后端在 `backend/`，用 `.venv\Scripts\python.exe` 运行
- 数据存 `backend/data/projects/{pid}/`（相对 backend/ 目录；运行 uvicorn 时 cwd=backend/）

## Files to Modify/Create
1. **Modify** `backend/app/book/volume.py` — 给 Volume dataclass 加字段
2. **Create** `backend/app/api/phase_routes.py` — 新路由文件
3. **Modify** `backend/app/api/main.py` — 注册新 router

## Requirements (exact)

### 1. Volume 新增字段（全部带默认值，确保旧 JSON 反序列化不报错）

在现有 Volume dataclass 字段列表末尾追加（**保留所有现有字段不要删**）：

```python
# ---- 工作台重构新增 ----
volume_kind: str = 'dungeon'  # 'dungeon' | 'interlude'
adapted_module: t.Optional[str] = None
simulated_run: t.Optional[dict] = None
death_nodes: list = field(default_factory=list)
climax_nodes: list = field(default_factory=list)
clue_nodes: list = field(default_factory=list)
pasted_nodes: list = field(default_factory=list)
host_character: t.Optional[dict] = None
entry_point: t.Optional[dict] = None
info_gap: t.Optional[dict] = None
stance: t.Optional[str] = None
tangchi_story_outline: t.Optional[str] = None
deviation_points: list = field(default_factory=list)
rhythm_blueprint: t.Optional[dict] = None
scaffold_cards: dict = field(default_factory=dict)
settle: t.Optional[dict] = None
interlude_plan: t.Optional[dict] = None
stage_completed: dict = field(default_factory=lambda: {
    'setup': False, 'adapt': False, 'simulate': False,
    'embody': False, 'scaffold': False, 'rhythm': False,
})
```

**重要**：
- `from_dict()` 必须用 `.get(key, default)` 读取这些字段，不能因旧 JSON 缺字段报错
- `to_dict()` 必须输出所有新字段
- 不要改动现有字段的类型/顺序/默认值
- `t` 是 `typing as t` 的别名，按文件现有导入方式使用

### 2. GET /api/projects/{pid}/phase 端点

返回 JSON，字段：
```json
{
  "phase": "setup|adapt|adapt_empty|simulate|embody|scaffold|rhythm|briefing|desk|settle|interlude",
  "volume_no": 2,
  "volume_kind": "dungeon",
  "chapter_no": 3,
  "is_last_chapter": false
}
```

阶段判定优先级（从高到低）：

1. **setup**：`data/projects/{pid}/book/` 下 8 张卡 json 文件（premise, tangchi, endgame, cost_debts, remaining_pages, seven_day_pulse, finale_notes, craft）不全存在或文件 < 10 字节 → `phase='setup'`
2. **没有任何 volume** → `phase='adapt_empty'`
3. **杀青判定**：取最后一个 volume（max volume_no），若存在 chapters 且所有章都 finalized 且 `vol.settle` 为空 → `phase='settle'`
4. **间章判定**：最后一个 volume 的 `volume_kind=='interlude'` 且未 settle → 若有未定稿章则 `phase='desk'`；若 rhythm 未完则 `phase='interlude'`
5. **日更 desk**：存在未定稿章，且章有 brief 属性（用 `getattr(ch, 'brief', None)` 防御，Task 2 才会加这个字段）→ `phase='desk'` 且 `chapter_no` 为最早未定稿章
6. **按 stage_completed 判定**：按顺序 adapt→simulate→embody→scaffold→rhythm，第一个 False 的键就是 phase
7. **都完成但没触发章前** → `phase='briefing'`

> 注意：
> - 路径都相对于 backend 运行时 cwd（即 `data/projects/{pid}/`）
> - book 卡片路径是 `data/projects/{pid}/book/{card}.json`（确认现有 book profile 的存储路径）
> - 判定"某 card 是否填入"用文件大小 > 10 字节而不是 JSON 解析（空 dict/空文件算未填）
> - 如果 VolumeStore 的 `list_volumes()` 返回按 volume_no 排序的列表，则最后一个就是最新卷；若不是请按 volume_no 排序取 max
> - Chapter 对象访问 `.finalized_at` 判断已定稿（检查现有字段名：有的用 finalized_at，若不确定先读 desk.py）
> - DeskStore 初始化路径是 `data/projects/{pid}/chapters`（先读 desk.py/VolumeStore 等现有代码确认路径模式，保持一致）

### 3. POST /api/projects/{pid}/volumes

接受 body `{"title": "新卷名"}`，调用 VolumeStore 创建下一副本卷（kind='dungeon'），返回 `{"volume_no": N}`。

> 若现有 VolumeStore 没有 create_next_volume 方法，请添加：找到当前最大 volume_no，+1 创建新 Volume(volume_no=n, field_no=n, title=title, volume_kind='dungeon', ...)，落盘到 `volumes/{n}.json`，并更新 volumes/index.json（看现有 volumes/index.json 结构按同格式追加）。

### 4. POST /api/projects/{pid}/volumes/{vid}/interlude

创建间章：同样 create_next_volume，但 `volume_kind='interlude'`，`title='蓝星间章'`，返回 `{"volume_no": N}`。

### 5. 在 main.py 注册 router

```python
from .phase_routes import router as phase_router
app.include_router(phase_router)
```

放在现有 router 注册附近即可。

## Report File
写完后写报告到：`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-1-report.md`

报告内容必须包含：
1. 改了哪些文件、每个文件加了什么
2. 启动验证的命令和输出（`python -c "from app.book.volume import Volume; v=Volume.from_dict({'volume_no':1,'field_no':1}); print(v.volume_kind, v.stage_completed)"` 必须贴真实输出）
3. `/api/projects/{pid}/phase` 对 20261014-c6c432 项目 curl 的真实输出
4. 如有任何不确定的地方（比如路径、字段名），列在 concerns 里
5. 提交的 commit hash（至少一个 commit，message: `feat(backend): 扩展 Volume 字段 + /phase 端点判定阶段`）

## Constraints
- 不要改动前端文件
- 不要改动其他后端逻辑（sessions/pipeline/generation/review）
- 不要删除现有字段或方法
- 所有新加的路由要正确处理 404（项目不存在、卷不存在）
- 用 fastapi 的 HTTPException 抛错
- Python 代码用 snake_case，类型注解完整
- 启动 uvicorn 验证时 cwd 必须是 `backend/`（否则会 ModuleNotFoundError）

## Self-Test
1. 从 backend/ 目录运行 `.venv\Scripts\python.exe -c "from app.book.volume import Volume; v=Volume.from_dict({'volume_no':1,'field_no':1}); print(v.volume_kind, v.stage_completed)"` 应输出 `dungeon {'setup': False, 'adapt': False, 'simulate': False, 'embody': False, 'scaffold': False, 'rhythm': False}`
2. 启动 uvicorn 后 `Invoke-RestMethod http://localhost:8321/api/projects/20261014-c6c432/phase` 能返回 JSON，phase 值根据现有数据可能是 'setup' 或 'adapt' 或其他
3. `POST /api/projects/20261014-c6c432/volumes` 创建卷不报错（注意：测试后可删除创建出的 json 文件避免污染数据，或在测试完成后回滚）
