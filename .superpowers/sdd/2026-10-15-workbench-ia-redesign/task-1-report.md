# Task 1 验证报告：后端 Volume 数据结构扩展 + 阶段判定 API

## 1. 文件改动清单

### 修改
- `backend/app/book/volume.py`
  - `Volume` dataclass 在 `updated_at` 之后追加 18 个新字段（工作台重构新增）：`volume_kind`, `adapted_module`, `simulated_run`, `death_nodes`, `climax_nodes`, `clue_nodes`, `pasted_nodes`, `host_character`, `entry_point`, `info_gap`, `stance`, `tangchi_story_outline`, `deviation_points`, `rhythm_blueprint`, `scaffold_cards`, `settle`, `interlude_plan`, `stage_completed`。全部带默认值，旧 JSON 反序列化不会报错。
  - `from_dict()` 更新：用 `.get(key, default)` 读取所有新字段。
  - `VolumeStore._flush()` 索引条目增加 `volume_kind` 字段，便于后续书脊显示。
  - 新增 `VolumeStore.create_new_volume(*, title="", volume_kind="dungeon")` 方法：取 max volume_no +1，field_no 同 volume_no（新场=新卷），不做幂等检查（允许连续创建多个卷）；interlude 时默认 title="蓝星间章"，dungeon 时默认 title="卷N"。未改动原有 `create_next(book_store, field_no)` 签名。

- `backend/app/api/main.py`
  - 文件末尾（`register_pdf5_route(app)` 之后）新增注册代码：
    ```python
    from .phase_routes import register_phase_routes
    from ..book.profile import BookStore as _BookStoreCls
    register_phase_routes(
        app,
        require_project=_require_project,
        project_dir=_project_dir,
        book_for=_book_for,
        volumes_for=_volumes_for,
        desk_for=_desk_for,
        book_cards=_BookStoreCls.CARDS,
    )
    ```

### 新建
- `backend/app/api/phase_routes.py`
  - 使用 `register_phase_routes(app, ...)` 函数式注册（与 `jobs_routes.py` 同样模式，避免循环 import）。
  - `GET /api/projects/{pid}/phase`：按 brief 的 7 步优先级判定阶段（setup → adapt_empty → settle → interlude/desk → desk → stage_completed 顺序 → briefing），返回 JSON 含 `phase/volume_no/volume_kind/chapter_no/is_last_chapter` 五个字段。
  - `POST /api/projects/{pid}/workbench/volumes`（body `{"title": "..."}`）：创建 dungeon 卷，调用 `vs.create_new_volume(title=title, volume_kind="dungeon")`。
  - `POST /api/projects/{pid}/workbench/volumes/interlude`：创建 interlude 卷，默认 title="蓝星间章"。
  - 路由前缀统一用 `/workbench/volumes`，完全不与现有 `POST /api/projects/{pid}/volumes`（`volumes_create_next`）冲突。
  - 通过依赖注入容器 `_Deps` 传入所有需要的 helper，无对 `main.py` 的反向 import。

## 2. Volume.from_dict 默认值验证命令与输出

命令（cwd = `backend/`）：
```
.venv\Scripts\python.exe -c "from app.book.volume import Volume; v=Volume.from_dict({'volume_no':1,'field_no':1}); print(v.volume_kind, v.stage_completed)"
```

实际输出：
```
dungeon {'setup': False, 'adapt': False, 'simulate': False, 'embody': False, 'scaffold': False, 'rhythm': False}
```

完全符合 brief 预期。

## 3. /phase 端点对 20261014-c6c432 项目的真实输出

使用 FastAPI TestClient 直接调用（不走 uvicorn，避免依赖运行中的 server）：

```python
from fastapi.testclient import TestClient
from app.api.main import app
client = TestClient(app)
resp = client.get("/api/projects/20261014-c6c432/phase")
print(resp.status_code, resp.json())
```

实际输出（Status=200）：
```json
{
  "phase": "setup",
  "volume_no": null,
  "volume_kind": null,
  "chapter_no": null,
  "is_last_chapter": false
}
```

原因：`data/projects/20261014-c6c432/book/` 下 8 张卡中：
- `cost_debts.json` = 2 字节（`[]`）
- `finale_notes.json` = 2 字节（`[]`）
- `remaining_pages.json` = 2 字节（`[]`）
均 < 10 字节，按规则 1 判定 phase=`setup`。这与预期一致——当前项目还在填卡阶段，还没走完 setup。

404 验证（请求不存在的 pid）返回 Status=404，HTTPException 正常抛出。

已有 volume 1 反序列化验证：
```
Volume count: 1
  vol 1: kind=dungeon, title='卷1', stage={'setup': False, 'adapt': False, 'simulate': False, 'embody': False, 'scaffold': False, 'rhythm': False}
```
旧 JSON（不含新字段）被正确读取，新字段全部获得默认值。

## 4. Commit

- Hash：`4dc9bbd`
- Message：`feat(backend): 扩展 Volume 字段 + /phase 端点判定阶段`
- 变更：3 files changed, 3054 insertions(+), 21 deletions(-)
  - `backend/app/book/volume.py`（新建，整个文件首次提交）
  - `backend/app/api/phase_routes.py`（新建）
  - `backend/app/api/main.py`（追加注册代码）

## 5. Concerns / 注意事项

1. **书卡"填入"判定粒度**：brief 要求用"文件大小 > 10 字节"判定某张卡是否已填。空列表 `[]`（2 字节）判定为未填。但 `premise.json`（12383 字节）和 `craft.json`（15591 字节）都是首次读取由 `BookStore._default()` 自动生成的默认值，严格说作者并没有真正"填过"它们。本实现严格按 brief 的"文件大小 > 10 字节"规则，不解析 JSON 内容判空；如果后续需要区分"默认值 vs 作者手填"，需另加标记。
2. **杀青/desk 阶段的章绑定问题**：判定 settle（规则3）和未定稿章（规则4、5）时，目前检查的是 `desk.all_nos()` 返回的全部章，而非严格绑定到 `last_vol` 的章。当前工作台流程是"一卷写完才开下一卷"，且每卷所有章都在同一 desk.json 里，这个近似在新工作流下是正确的；如果未来支持多卷并行写作，需要用 `ChapterWork.field_no == last_vol.field_no` 过滤。
3. **新端点路径与 brief 原文略有差异**：brief 原文写的是 `POST /api/projects/{pid}/volumes` 和 `POST /api/projects/{pid}/volumes/{vid}/interlude`，但前者已被现有 `volumes_create_next` 占用（基于 field_no 自动 +1、幂等）。为避免破坏现有端点（前端可能正在调用），新端点统一放在 `/workbench/volumes` 前缀下：
   - `POST /api/projects/{pid}/workbench/volumes`（接受 `{"title": "..."}`，创建 dungeon）
   - `POST /api/projects/{pid}/workbench/volumes/interlude`（无 body，创建 interlude，title 默认"蓝星间章"）
   - interlude 端点不需要 vid 参数（+1 创建即可），与 create_new_volume 语义一致。
   后续前端工作台统一调用 workbench 前缀即可；原有 `POST /volumes` 端点保持原样不受影响。
4. **brief 写"17 个新字段"但代码片段列出 18 个**：`volume_kind` + `adapted_module` + `simulated_run` + `death_nodes` + `climax_nodes` + `clue_nodes` + `pasted_nodes` + `host_character` + `entry_point` + `info_gap` + `stance` + `tangchi_story_outline` + `deviation_points` + `rhythm_blueprint` + `scaffold_cards` + `settle` + `interlude_plan` + `stage_completed` = 18 个。已全部添加；以代码片段为准。
5. **Chapter 暂无 `brief` 字段**：按 brief 说明 Task 2 才加 `brief` 字段，本任务的规则5用 `getattr(w, 'brief', None)` 防御性访问，目前不会触发"有 brief 才进 desk"的分支，所有未定稿章直接 phase=`desk`。
6. **desk.json 不存在时**：`DeskStore._load()` 在文件不存在时返回空 dict，`all_nos()` 返回空列表 `[]`，规则 3/4/5 正确跳过（不会崩溃）。
7. **volume.py 此前未被 git track**：本次 commit 把整个 `backend/app/book/volume.py` 作为新文件提交（3054 行里的大部分是该文件原有内容，因它从未被 git add 过）。`backend/app/book/__init__.py` 等其他 book 模块文件未动。
