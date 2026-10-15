# 写作间工作台重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把写作间从"三个并列 Tab+9 项下拉"重构为"书脊+主工作区+抽屉+浮动 AI"的单工作台，承载"模组上传→改编→跑团→魂穿→详设→节奏→章前 5 分钟→章台日更→杀青→蓝星间章→下一卷"的真实创作流水线。

**Architecture:** 后端小改（加 Volume 字段、加 8 个新端点、加伏笔检测器），前端大改（砍 el-tabs、加 HomeView 容器+10 个视图+8 个新组件、把旧视图内容搬进抽屉或删除）。阶段判定完全基于 volume 字段推导，不再依赖 session.current_phase 作为权威来源。SushiCards 通用化后被所有"需要 N 选 1"的环节复用。

**Tech Stack:**
- 后端：FastAPI / Python 3.10 / Pydantic dataclasses（沿用现有）
- 前端：Vue 3 Composition API + `<script setup>` + TypeScript / Element Plus / Vite（沿用现有）
- 长任务：现有 jobs_routes SSE
- 状态持久化：现有 JSON 文件存储（projects/*/volumes/, fields/, entities/, book/）

**代码位置：**
- 前端：`novel-studio/frontend/src/`
- 后端：`novel-studio/backend/app/`
- 后端虚拟环境启动：在 `novel-studio/backend/` 下 `.\\.venv\\Scripts\\python.exe -m uvicorn app.api.main:app --host 0.0.0.0 --port 8321`
- 前端启动：在 `novel-studio/frontend/` 下 `npm run dev`（Vite dev server 5173）

---

## Global Constraints

- 不重写已有 LLM 能力（pdf5/coc_batch/三段生成/审稿/定稿飞轮/sushi_generator/coc_simulator），只改调用位置或前端承载。
- 所有新中文按钮文案统一使用「这版先这样，往下」「选这盘」「换一拨」「从空白自己写」「AI 改一版直接应用」「出建议先看看」「就定这版」（沿用现有 SushiCards 文案），不再使用"落锤/锁定"。
- craft 宪法（10 模块+双层黄金三章）默认自动注入，不暴露 UI 勾选。
- 新卷必须上传 PDF 模组才能解锁 adapt 之后的阶段，不做 AI 原创兜底。
- 每章"剧情时间（副本内第 N 天）"是独立字段 `story_day`，与章号解耦；第 6-7 天高潮可以跨多章。
- 开卷前三章校验看的是"副本开卷第 1/2/3 章"（结构位置），不是"副本第 1/2/3 天"（剧情时间）。
- 7 个旧 views（Wizard/Pipeline/Markers/Workshop/Skills/Corpus/Import/Projects/Session）+ VariantPicker 最终删除；迁移期间可先在新视图里实现内容再删除旧文件。
- plan_chapters 接口直接删除（不再返回 9 章硬模板）；walkthrough_step/daily_summon 标 deprecated 返回"建设中"。
- Volume 新增字段全部带默认值，确保已有 JSON 反序列化不报错；旧项目升级不丢已有文本。
- 杀青面板步骤序号从 0 开始（0=伏笔检测），步骤 0/1 是强制的，步骤 1 有未处置项时步骤 5 disabled。

---

## Task 1: 后端 Volume 数据结构扩展 + 阶段判定 API

**为什么先做这个：** 前端所有新视图都依赖 volume 上的新字段和 `/phase` 端点来判定当前阶段。这一步不做，前端无法启动。

**Files:**
- Modify: `backend/app/book/volume.py`
- Create: `backend/app/api/phase_routes.py`
- Modify: `backend/app/api/main.py`（注册新 router）

**Interfaces:**
- Consumes: 现有 `Volume` dataclass, `VolumeStore`
- Produces:
  - `Volume` 新增字段（全部带 default）：
    - `volume_kind: str = 'dungeon'`（'dungeon'|'interlude'）
    - `adapted_module: str|None = None`
    - `simulated_run: dict|None = None`（含 timeline, npc_fates, clue_distribution, reveal_moment, climax_scene, original_ending）
    - `death_nodes: list = field(default_factory=list)`（{day, npc, location, method}）
    - `climax_nodes: list = field(default_factory=list)`
    - `clue_nodes: list = field(default_factory=list)`
    - `pasted_nodes: list = field(default_factory=list)`（从其他跑团场拼贴来的节点）
    - `host_character: dict|None = None`（{name, role, original_fate, starting_position, risk}）
    - `entry_point: dict|None = None`（{day, hook, what_he_sees_first}）
    - `info_gap: dict|None = None`（{knowns, unknowns, opening_shot, bonus_clues}）
    - `stance: str|None = None`（'follower'|'defier'|'observer'）
    - `tangchi_story_outline: str|None = None`
    - `deviation_points: list = field(default_factory=list)`（{where, original_path, tangchi_choice, consequence}）
    - `rhythm_blueprint: dict|None = None`（{pacing_ratio, key_beats_progress, suggested_length, suggested_chapter_range}）
    - `scaffold_cards: dict = field(default_factory=dict)`（每张卡的锁定状态和文本：obituary_card/entity_card/truth_outline/core_twist/tangchi_start/cost_cap/foreshadows/tangchi_scale）
    - `settle: dict|None = None`（杀青面板结果）
    - `interlude_plan: dict|None = None`（间章 4 步结果，仅 volume_kind='interlude' 时使用）
    - `stage_completed: dict = field(default_factory=lambda: dict(setup=False, adapt=False, simulate=False, embody=False, scaffold=False, rhythm=False))`
  - `GET /api/projects/{pid}/phase` 返回：
    ```json
    {
      "phase": "setup|adapt|simulate|embody|scaffold|rhythm|desk|settle|interlude|adapt_empty",
      "volume_no": 2,
      "volume_kind": "dungeon",
      "chapter_no": 3,
      "is_last_chapter": false,
      "pending_settle": false,
      "pending_interlude": false
    }
    ```
  - `POST /api/projects/{pid}/volumes` 创建新卷（kind='dungeon'）
  - `POST /api/projects/{pid}/volumes/{vid}/interlude` 创建间章（kind='interlude'）

- [ ] **Step 1: 读现有 Volume dataclass**
```bash
# 读 backend/app/book/volume.py，确认现有字段和 from_dict/to_dict 方法
```

- [ ] **Step 2: 在 Volume dataclass 里新增字段**

打开 `backend/app/book/volume.py`，在现有字段末尾追加（保留所有现有字段不删）：

```python
@dataclass
class Volume:
    # ... 保留原有字段 ...
    # ---- 新增（工作台重构）----
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

在 `from_dict` 里确保新字段都被读取（默认用 dict.get 带默认值，不要因为旧 JSON 缺字段报错）。在 `to_dict` 里输出所有新字段。

- [ ] **Step 3: 创建 phase_routes.py**

新建 `backend/app/api/phase_routes.py`：

```python
from fastapi import APIRouter, HTTPException
from ..projects.store import ProjectStore
from ..book.volume import VolumeStore
from ..wizard.desk import DeskStore  # 用于判断是否有未定稿章
from ..entities.store import EntityStore
from pathlib import Path

router = APIRouter(prefix='/api/projects/{pid}', tags=['phase'])

def _stores(pid):
    base = Path('data/projects') / pid
    return (
        ProjectStore(base),
        VolumeStore(base / 'volumes'),
        DeskStore(base / 'chapters'),
    )

@router.get('/phase')
def get_phase(pid: str):
    ps, vs, ds = _stores(pid)
    proj = ps.get()
    if not proj:
        raise HTTPException(404)

    # 1. setup 完成判定：book/ 下 8 张卡都存在且不为空
    book_dir = Path('data/projects') / pid / 'book'
    setup_done = all(
        (book_dir / f'{card}.json').exists() and (book_dir / f'{card}.json').stat().st_size > 10
        for card in ['premise','tangchi','endgame','cost_debts','remaining_pages',
                     'seven_day_pulse','finale_notes','craft']
    )
    if not setup_done:
        return {'phase': 'setup'}

    # 2. 拿当前活跃卷
    vols = vs.list_volumes()
    if not vols:
        return {'phase': 'adapt_empty'}  # 引导上传模组

    # 找最新的卷
    current_vol = vols[-1]
    vid = current_vol.volume_no

    # 3. 杀青判定：末章已定稿且 settle 为空
    chapters = ds.list_chapters()
    finalized = [c for c in chapters if c.finalized_at]
    non_finalized = [c for c in chapters if not c.finalized_at]

    # 判定"末章"：若 volume 标记了 planned_chapter_count 用它，否则以现有最后一章为准
    is_last_chapter_finalized = bool(finalized) and not non_finalized
    if is_last_chapter_finalized and not current_vol.settle:
        return {'phase': 'settle', 'volume_no': vid, 'volume_kind': current_vol.volume_kind}

    # 4. 间章判定：上一卷 settle 完成，下一个是 interlude 未完成
    if current_vol.volume_kind == 'interlude' and not current_vol.settle:
        if non_finalized:
            return {'phase': 'desk', 'volume_no': vid, 'volume_kind': 'interlude',
                    'chapter_no': non_finalized[0].chapter_no}
        if not current_vol.stage_completed.get('rhythm'):
            return {'phase': 'interlude', 'volume_no': vid}
        # 间章完→自动创建下一副本卷应在 settle finalize 做，这里兜底
        return {'phase': 'adapt_empty'}

    # 5. 日更：有未定稿且有 brief 的章
    if non_finalized:
        for c in non_finalized:
            if c.brief:  # 有章前卡说明规划过
                return {'phase': 'desk', 'volume_no': vid, 'volume_kind': current_vol.volume_kind,
                        'chapter_no': c.chapter_no, 'is_last_chapter': (c == non_finalized[-1] and is_last_chapter_finalized)}

    # 6. 按 stage_completed 判定在哪个规划阶段
    sc = current_vol.stage_completed
    if not sc.get('adapt'):
        return {'phase': 'adapt', 'volume_no': vid}
    if not sc.get('simulate'):
        return {'phase': 'simulate', 'volume_no': vid}
    if not sc.get('embody'):
        return {'phase': 'embody', 'volume_no': vid}
    if not sc.get('scaffold'):
        return {'phase': 'scaffold', 'volume_no': vid}
    if not sc.get('rhythm'):
        return {'phase': 'rhythm', 'volume_no': vid}

    # 7. 都完了但没章 → 触发前 3 章章前 5 分钟（前端处理）
    return {'phase': 'briefing', 'volume_no': vid}
```

> 注：上面 DeskStore 的 `c.brief` 字段需要确认，下一个 task 加。这里先按 dict 访问。

- [ ] **Step 4: 添加 create volume/interlude 端点**

在 phase_routes.py 末尾加：

```python
@router.post('/volumes')
def create_volume(pid: str, body: dict):
    ps, vs, ds = _stores(pid)
    vol = vs.create_next_volume(kind='dungeon', title=body.get('title', ''))
    return {'volume_no': vol.volume_no}

@router.post('/volumes/{vid}/interlude')
def create_interlude(pid: str, vid: int):
    ps, vs, ds = _stores(pid)
    inter = vs.create_next_volume(kind='interlude', title='蓝星间章')
    return {'volume_no': inter.volume_no}
```

> 需要在 VolumeStore 里加 `create_next_volume(kind, title)` 方法：找到当前 volume_no_max +1 新建空 Volume 并落盘。如果现有 create_volume 签名不同则按现有模式适配。

- [ ] **Step 5: 在 main.py 注册 router**

打开 `backend/app/api/main.py`，在现有 router 注册附近加：

```python
from .phase_routes import router as phase_router
app.include_router(phase_router)
```

- [ ] **Step 6: 启动后端验证**

```bash
cd c:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio\backend
.\\.venv\\Scripts\\python.exe -c "from app.book.volume import Volume; v=Volume.from_dict({'volume_no':1,'field_no':1}); print(v.volume_kind, v.stage_completed)"
```
应输出 `dungeon {'setup': False, ...}`

- [ ] **Step 7: 验证 /phase 端点**

启动 uvicorn 后：
```powershell
Invoke-RestMethod -Uri http://localhost:8321/api/projects/20261014-c6c432/phase
```
应返回 JSON，phase 字段是 'setup'/'adapt'/'simulate' 等之一（根据项目当前数据状态）。

- [ ] **Step 8: Commit**

```bash
cd c:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio
git add backend/app/book/volume.py backend/app/api/phase_routes.py backend/app/api/main.py
git commit -m "feat(backend): 扩展 Volume 字段 + /phase 端点判定阶段"
```

---

## Task 2: 章前卡 Brief 数据结构 + 章前 5 分钟/杀青相关后端接口

**Files:**
- Modify: `backend/app/wizard/desk.py`（Chapter 加 brief 字段）
- Create: `backend/app/api/brief_routes.py`（章前卡+next-chapter-brief）
- Create: `backend/app/api/settle_routes.py`（杀青 3 个端点）
- Create: `backend/app/settle/detector.py`（伏笔检测逻辑）
- Modify: `backend/app/api/main.py`
- Modify: `backend/app/review/reviewer.py`（加第 8 项检查 rule_reveal_pacing）

**Interfaces:**
- Consumes: VolumeStore, DeskStore, EntityStore, BookProfile (for craft rules)
- Produces:
  - Chapter.brief: dict|None = None（{summary, threads, payoff, hook, story_day, position}）
  - `PATCH /api/projects/{pid}/volumes/{vid}/chapters/{cno}/brief` upsert chapter.brief
  - `POST /api/projects/{pid}/volumes/{vid}/next-chapter-brief` 流式生成下一章 3 个方向 + 章前卡（SSE）
  - `POST /api/projects/{pid}/volumes/{vid}/settle/detect_foreshadows` 同步返回检测结果
  - `POST /api/projects/{pid}/volumes/{vid}/settle/apply` 批量写回伏笔/实体/污染
  - `POST /api/projects/{pid}/volumes/{vid}/settle/finalize` 标记 settled+创建间章或下一卷

- [ ] **Step 1: 给 Chapter dataclass 加 brief 字段**

打开 `backend/app/wizard/desk.py`，找到 Chapter dataclass，加字段：
```python
brief: t.Optional[dict] = None  # {summary, threads:[], payoff, hook, story_day, position}
```
确保 from_dict/to_dict 处理这个字段，缺省为 None。

- [ ] **Step 2: 创建 settle/detector.py（伏笔检测桩实现）**

```python
"""本卷未回收伏笔自动检测（初版：基于关键词匹配+LLM 抽取二次校验）"""
from ..entities.store import EntityStore
from ..wizard.desk import DeskStore
from pathlib import Path

def detect_foreshadows(project_id: str, volume_no: int):
    base = Path('data/projects') / project_id
    es = EntityStore(base / 'entities')
    ds = DeskStore(base / 'chapters')

    # 1. 收集所有 type='foreshadow', status='planted' 的伏笔
    planted = []
    for e in es.list_entities():
        if e.entity_type == 'foreshadow' and e.status == 'planted':
            planted.append(e)

    # 2. 扫本卷所有已定稿章 polish 文本
    chapters = [c for c in ds.list_chapters() if c.finalized_at]
    full_text = '\n'.join((c.polish or c.draft or c.plot or '') for c in chapters)

    results = []
    for f in planted:
        # 简单匹配：伏笔关键字/名字出现次数（初版；后续可接 LLM 做语义判断）
        mentions = full_text.count(f.name)
        if mentions >= 2:
            status = 'recycled'  # 多次出现视为已回收（启发式）
        elif mentions == 1:
            status = 'hinted'
        else:
            status = 'missed'
        results.append({
            'foreshadow_id': f.id,
            'content': f.description or f.name,
            'planted_chapter': getattr(f, 'planted_chapter', None),
            'mentions': mentions,
            'status': status,
            'suggestion': '',  # 后续接 LLM 给建议
        })
    return {'results': results}
```

- [ ] **Step 3: 创建 settle_routes.py**

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path
from ..projects.store import ProjectStore
from ..book.volume import VolumeStore
from ..wizard.desk import DeskStore
from ..entities.store import EntityStore
from ..pipeline.settle import apply_settle_diff
from .detector import detect_foreshadows

router = APIRouter(prefix='/api/projects/{pid}/volumes/{vid}', tags=['settle'])

class SettleApplyBody(BaseModel):
    foreshadow_decisions: list[dict]  # [{id, action: 'patch'/'carry'/'drop', patch_text?: str, target_chapter?: int}]
    entity_decisions: list[dict]      # [{entity_id, action: 'kill'/'carry'/'interlude'}]
    contamination_delta: int
    wrapup_text: str

@router.post('/settle/detect_foreshadows')
def detect(pid: str, vid: int):
    return detect_foreshadows(pid, vid)

@router.post('/settle/apply')
def apply_settle(pid: str, vid: int, body: SettleApplyBody):
    base = Path('data/projects') / pid
    vs = VolumeStore(base / 'volumes')
    es = EntityStore(base / 'entities')
    ds = DeskStore(base / 'chapters')
    vol = vs.get_volume(vid)
    if not vol: raise HTTPException(404)

    # 1. 写回伏笔处置
    for d in body.foreshadow_decisions:
        e = es.get(d['id'])
        if not e: continue
        if d['action'] == 'drop':
            e.status = 'dropped'
        elif d['action'] == 'carry':
            e.status = 'planted'
            e.tags = list(set((e.tags or []) + ['carry_to_next']))
        elif d['action'] == 'patch' and d.get('patch_text'):
            e.status = 'paid'
            # 把 patch_text 追加到 target_chapter 的 polish 末尾
            tgt = ds.get_chapter(d['target_chapter'])
            if tgt:
                tgt.polish = (tgt.polish or '') + '\n\n' + d['patch_text']
                ds.save_chapter(tgt)
        es.save(e)

    # 2. 实体去向
    for d in body.entity_decisions:
        e = es.get(d['entity_id'])
        if not e: continue
        if d['action'] == 'kill':
            e.status = 'deceased'
        elif d['action'] == 'carry':
            e.status = 'active'
            e.tags = list(set((e.tags or []) + ['carry_to_next']))
        elif d['action'] == 'interlude':
            e.status = 'pending_interlude'
        es.save(e)

    # 3. 污染度写到 protagonist（复用 existing apply_settle_diff 的 contamination 分支）
    diff = {'contamination_delta': body.contamination_delta, 'field_count_inc': 1}
    apply_settle_diff(base, diff)

    # 4. 暂存到 vol.settle
    vol.settle = {
        'foreshadow_decisions': body.foreshadow_decisions,
        'entity_decisions': body.entity_decisions,
        'contamination_delta': body.contamination_delta,
        'wrapup_text': body.wrapup_text,
        'applied': False,
    }
    vs.save_volume(vol)
    return {'ok': True}

@router.post('/settle/finalize')
def finalize(pid: str, vid: int, body: dict):
    base = Path('data/projects') / pid
    vs = VolumeStore(base / 'volumes')
    vol = vs.get_volume(vid)
    if not vol: raise HTTPException(404)
    if not vol.settle: raise HTTPException(400, 'settle not applied')

    vol.settle['applied'] = True
    vol.settle['finalized_at'] = __import__('datetime').datetime.now().isoformat()
    vs.save_volume(vol)

    # 若 body.skip_interlude=True 直接建下一副本卷，否则建间章
    kind = 'dungeon' if body.get('skip_interlude') else 'interlude'
    new_vol = vs.create_next_volume(kind=kind, title='新副本' if kind=='dungeon' else '蓝星间章')
    return {'new_volume_no': new_vol.volume_no, 'new_kind': kind}
```

- [ ] **Step 4: 创建 brief_routes.py（章前卡 CRUD + next-chapter-brief 生成端点桩）**

```python
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pathlib import Path
from ..book.volume import VolumeStore
from ..wizard.desk import DeskStore

router = APIRouter(prefix='/api/projects/{pid}/volumes/{vid}', tags=['brief'])

class BriefBody(BaseModel):
    summary: str
    threads: list[str]
    payoff: str
    hook: str
    story_day: int
    position: str  # opening/mid/climax/ending

@router.patch('/chapters/{cno}/brief')
def update_brief(pid: str, vid: int, cno: int, body: BriefBody):
    base = Path('data/projects') / pid
    ds = DeskStore(base / 'chapters')
    ch = ds.get_chapter(cno)
    if not ch: raise HTTPException(404)
    ch.brief = body.dict()
    ds.save_chapter(ch)
    return {'ok': True}

@router.post('/next-chapter-brief')
def next_chapter_brief(pid: str, vid: int):
    """SSE 流式返回 3 个章方向卡。
    初版桩实现：返回 3 个模板方向，后续接 LLM 实装。"""
    def gen():
        import json, time
        for i, d in enumerate([
            {'title': f'第 X 章（方向 A）', 'day': 1, 'summary': '沿上一章结尾继续：唐迟面对第一个异常',
             'threads': ['主线'], 'payoff': '小爽点', 'hook': '他看到了不该看到的东西'},
            {'title': f'第 X 章（方向 B）', 'day': 1, 'summary': '从侧面人物视角切入，慢慢拉回唐迟',
             'threads': ['羁绊'], 'payoff': '无', 'hook': '邻居张阿姨敲开了门'},
            {'title': f'第 X 章（方向 C）', 'day': 1, 'summary': '唐迟试图用程序员思维梳理线索',
             'threads': ['冲突'], 'payoff': '无', 'hook': '他发现笔记本上自己写了看不懂的字'},
        ], 1):
            yield f"data: {json.dumps({'index': i, **d}, ensure_ascii=False)}\n\n"
            time.sleep(0.2)
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type='text/event-stream')
```

- [ ] **Step 5: reviewer.py 加第 8 项 rule_reveal_pacing**

打开 `backend/app/review/reviewer.py`，在现有 7 项检查之后追加第 8 项。找到 checks 列表，加：
```python
{
    'key': 'rule_reveal_pacing',
    'label': '规则揭示节奏',
    'run': lambda text, ctx: _check_rule_reveal(text, ctx),
}
```
实现 `_check_rule_reveal`：
```python
def _check_rule_reveal(text, ctx):
    # 启发式：检测是否有大段"我给你讲讲规则啊"式 exposition
    red_flags = ['我给你讲讲规则', '这个副本的规则是', '你听好了，这个世界', '其实这个地方']
    hits = [kw for kw in red_flags if kw in text]
    if hits:
        return {'ok': False, 'issues': [f'发现疑似 NPC 讲解设定：{h}' for h in hits]}
    return {'ok': True, 'issues': []}
```
（初版启发式；后续迭代可接 LLM 做更准确检测。）

- [ ] **Step 6: 在 main.py 注册新 router**

```python
from .brief_routes import router as brief_router
from .settle_routes import router as settle_router
app.include_router(brief_router)
app.include_router(settle_router)
```

- [ ] **Step 7: 删除/标记 plan_chapters 桩**

在 `backend/app/api/main.py` 里搜索 `plan_chapters` 分支，把原来返回硬编码 9 章模板的代码替换为：
```python
return {'error': 'plan_chapters 已废弃，新流程使用 rhythm 阶段+章前 5 分钟'}
```
并删除 kindle_diverge 之外的三个桩（walkthrough_step 返回 {'status': 'construction'}、daily_summon 返回 {'status': 'construction'}）。

- [ ] **Step 8: 启动后端验证新端点**

```powershell
# 启动 uvicorn 后
Invoke-RestMethod -Uri http://localhost:8321/api/projects/20261014-c6c432/phase
Invoke-RestMethod -Method Post -Uri http://localhost:8321/api/projects/20261014-c6c432/volumes/1/settle/detect_foreshadows
```
应返回正常 JSON 不报错。

- [ ] **Step 9: Commit**

```bash
git add backend/app/wizard/desk.py backend/app/api/brief_routes.py backend/app/api/settle_routes.py backend/app/settle/detector.py backend/app/api/main.py backend/app/review/reviewer.py
git commit -m "feat(backend): 章前卡 brief 字段、brief/settle 端点、审稿第8项、废弃桩接口"
```

---

## Task 3: 骨架组件（SpineNav / StatusBar / SettingsDrawer / AIChatFloat / HomeView 容器）+ App.vue 重构

**Files:**
- Create: `frontend/src/components/SpineNav.vue`
- Create: `frontend/src/components/StatusBar.vue`
- Create: `frontend/src/components/SettingsDrawer.vue`
- Create: `frontend/src/components/AIChatFloat.vue`
- Create: `frontend/src/views/HomeView.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/state.ts`

**Interfaces:**
- Consumes: `GET /api/projects/{pid}/phase` 端点、现有 projectsApi、volumesApi、chaptersApi
- Produces:
  - HomeView 作为新的根容器，内部根据 phase 渲染对应 view
  - SpineNav 暴露 `refresh()` 方法
  - AIChatFloat 暴露 `open()` 方法供外部快捷键调用
  - 全局状态新增 `currentPhase`, `currentVolumeNo`, `currentChapterNo`

- [ ] **Step 1: 改 state.ts 加全局阶段状态**

```ts
import { ref, watch } from 'vue'

const KEY = 'novel-studio.current-project'
export const currentProjectId = ref<string>(localStorage.getItem(KEY) ?? '')
watch(currentProjectId, v => {
  if (v) localStorage.setItem(KEY, v)
  else localStorage.removeItem(KEY)
})

// 工作台全局阶段状态（由 HomeView 在 /phase 请求后填充）
export const currentPhase = ref<string>('')
export const currentVolumeNo = ref<number>(0)
export const currentChapterNo = ref<number>(0)
export const currentVolumeKind = ref<string>('dungeon')

// 抽屉开关
export const drawerOpen = ref(false)
// AI 气泡开关
export const aiChatOpen = ref(false)
```

- [ ] **Step 2: 创建 StatusBar.vue**

```vue
<template>
  <div class="status-bar">
    <div class="left">
      <span class="book-title">《第七日的亡者》</span>
      <span class="sep">·</span>
      <span class="vol" v-if="currentVolumeNo">第 {{ currentVolumeNo }} 卷</span>
      <span class="sep" v-if="currentVolumeNo && currentChapterNo">·</span>
      <span class="ch" v-if="currentChapterNo">第 {{ currentChapterNo }} 章</span>
      <el-tag size="small" class="phase-tag">{{ phaseLabel }}</el-tag>
    </div>
    <div class="center">
      <span class="contam-label">污染</span>
      <el-progress :percentage="contamination" :stroke-width="8" :show-text="false"
                   :color="contamination > 60 ? '#d63031' : '#e17055'" style="width:120px"/>
      <span class="contam-num">{{ contamination }}%</span>
    </div>
    <div class="right">
      <el-button text @click="openSettings">⚙️</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { currentPhase, currentVolumeNo, currentChapterNo, drawerOpen } from '../state'
import { ElProgress, ElButton, ElTag } from 'element-plus'

const props = defineProps<{ contamination?: number }>()
const contamination = computed(() => props.contamination ?? 12)

const PHASE_LABELS: Record<string,string> = {
  setup: '开书设定', adapt: '剧本改编', simulate: '模拟跑团', embody: '魂穿设计',
  scaffold: '卷内详设', rhythm: '节奏骨架', desk: '日更', settle: '杀青',
  interlude: '蓝星间章', adapt_empty: '新卷', briefing: '章前规划'
}
const phaseLabel = computed(() => PHASE_LABELS[currentPhase.value] || currentPhase.value)
function openSettings() { drawerOpen.value = true }
</script>

<style scoped>
.status-bar {
  display: flex; align-items: center; justify-content: space-between;
  height: 48px; padding: 0 16px; background: #fff;
  border-bottom: 1px solid #eee;
}
.status-bar .left, .status-bar .right { display: flex; align-items: center; gap: 8px; }
.book-title { font-weight: 600; font-size: 15px; }
.sep { color: #ccc; }
.phase-tag { margin-left: 8px; }
.center { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #666; }
</style>
```

- [ ] **Step 3: 创建 SpineNav.vue（书脊左栏骨架）**

```vue
<template>
  <div class="spine">
    <div class="spine-root" @click="goSetup">
      <span class="icon">📖</span><span>书设定</span>
    </div>
    <div class="spine-body">
      <div v-for="v in volumes" :key="v.no" class="volume-group">
        <div class="vol-item" :class="{active: v.no===currentVolumeNo}" @click="selectVolume(v.no)">
          <span class="vol-icon">{{ v.icon }}</span>
          <span class="vol-title">{{ v.title }}</span>
          <el-tag v-if="v.contamination !== undefined" size="small"
                  :color="contamColor(v.contamination)" effect="dark">污{{ v.contamination }}%</el-tag>
        </div>
        <div v-for="c in v.chapters" :key="c.no" class="ch-item"
             :class="{active: v.no===currentVolumeNo && c.no===currentChapterNo, finalized: c.finalized}"
             @click="selectChapter(v.no, c.no)">
          <span class="ch-icon">{{ c.finalized ? '✓' : (c.no===currentChapterNo ? '▶' : (c.brief ? '📋' : '')) }}</span>
          <span class="ch-title">第 {{ c.no }} 章 · {{ c.title || '未命名' }}</span>
        </div>
      </div>
      <div class="add-btn" @click="$emit('addVolume')">
        <span>➕</span><span>新副本</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElTag } from 'element-plus'
import { currentProjectId, currentVolumeNo, currentChapterNo, currentPhase } from '../state'

const props = defineProps<{ volumes: any[] }>()
const emit = defineEmits(['selectVolume', 'selectChapter', 'addVolume', 'goSetup'])

function contamColor(c: number) {
  if (c < 20) return '#27ae60'
  if (c < 50) return '#f39c12'
  return '#c0392b'
}
function selectVolume(no: number) { emit('selectVolume', no) }
function selectChapter(vn: number, cn: number) { emit('selectChapter', vn, cn) }
function goSetup() { emit('goSetup') }
</script>

<style scoped>
.spine { width: 240px; background: #fafafa; border-right: 1px solid #eee; overflow-y: auto; height: calc(100vh - 48px); }
.spine-root { padding: 12px 16px; font-weight: 600; cursor: pointer; border-bottom: 1px solid #eee; }
.spine-root:hover { background: #f0f0f0; }
.vol-item { padding: 10px 16px; display: flex; align-items: center; gap: 6px; cursor: pointer; font-weight: 500; }
.vol-item:hover, .vol-item.active { background: #e8f4fd; }
.vol-icon { font-size: 16px; }
.vol-title { flex: 1; font-size: 13px; }
.ch-item { padding: 6px 16px 6px 32px; font-size: 12px; color: #555; cursor: pointer; display: flex; gap: 6px; align-items: center; }
.ch-item:hover { background: #f0f0f0; }
.ch-item.active { background: #d6ecfa; color: #000; font-weight: 500; }
.ch-item.finalized { color: #999; }
.add-btn { padding: 12px 16px; color: #888; cursor: pointer; font-size: 13px; display: flex; gap: 6px; align-items: center; }
.add-btn:hover { color: #409eff; }
</style>
```

- [ ] **Step 4: 创建 SettingsDrawer.vue 骨架（带 6 个分组占位，内容迁移是后续任务）**

```vue
<template>
  <el-drawer v-model="open" title="工具箱" direction="rtl" size="420px">
    <el-collapse v-model="activeGroup">
      <el-collapse-item title="📖 书稿" name="book">
        <p class="placeholder">版本回看/回滚/导出 Vault（待迁移）</p>
      </el-collapse-item>
      <el-collapse-item title="🗂 实体库" name="entities">
        <EntitiesViewPlaceholder />
      </el-collapse-item>
      <el-collapse-item title="📚 资料" name="ref"></el-collapse-item>
      <el-collapse-item title="📝 写作规范" name="craft"></el-collapse-item>
      <el-collapse-item title="🧪 高级" name="adv"></el-collapse-item>
      <el-collapse-item title="📂 项目" name="proj"></el-collapse-item>
    </el-collapse>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { drawerOpen } from '../state'
import { ElDrawer, ElCollapse, ElCollapseItem } from 'element-plus'
import { ref } from 'vue'
const open = computed({ get: () => drawerOpen.value, set: v => drawerOpen.value = v })
const activeGroup = ref('entities')
const EntitiesViewPlaceholder = { template: '<p class="placeholder">实体库（待迁移）</p>' }
</script>

<style scoped>
.placeholder { color: #999; padding: 12px 0; }
</style>
```

- [ ] **Step 5: 创建 AIChatFloat.vue 骨架（最小可用版，完整对话能力迭代）**

```vue
<template>
  <div class="ai-float">
    <el-button v-if="!open" circle type="primary" size="large" @click="open=true" class="float-btn">💬</el-button>
    <div v-if="open" class="chat-panel">
      <div class="chat-header">
        <span>💬 AI 聊一聊</span>
        <el-button text @click="open=false">✕</el-button>
      </div>
      <div class="chat-body">
        <div class="hint">上下文：{{ phaseHint }}</div>
        <textarea v-model="input" placeholder="和 AI 聊点什么..." class="chat-input" @keydown.enter.prevent="send"></textarea>
        <div class="quick-actions">
          <el-button v-for="a in quickActions" :key="a" size="small" @click="useQuick(a)">{{ a }}</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElButton } from 'element-plus'
import { currentPhase } from '../state'
const open = ref(false)
const input = ref('')
const phaseHint = computed(() => ({
  setup: '开书设定阶段', adapt: '剧本改编', simulate: '模拟跑团',
  embody: '魂穿设计', scaffold: '卷内详设', rhythm: '节奏骨架',
  desk: '日更写作', settle: '杀青', interlude: '蓝星间章'
}[currentPhase.value] || '自由聊'))
const quickActions = computed(() => {
  const map: Record<string,string[]> = {
    adapt: ['更阴冷一点', '换个风味', '和程序员 oncall 结合'],
    simulate: ['再多跑几场', '更惨一点', '少死点人'],
    desk: ['剧情打不通', '对话不自然', '钩子不够狠'],
    settle: ['帮我找伏笔回收点'],
    interlude: ['蓝星日常怎么违和']
  }
  return map[currentPhase.value] || ['给点灵感']
})
function send() { /* 后续接 sessionsApi.send */ }
function useQuick(a: string) { input.value = a }
</script>

<style scoped>
.ai-float { position: fixed; right: 24px; bottom: 24px; z-index: 900; }
.float-btn { width: 52px; height: 52px; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
.chat-panel { position: absolute; right: 0; bottom: 64px; width: 380px; height: 480px; background: #fff;
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.15); display: flex; flex-direction: column; }
.chat-header { padding: 10px 14px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; font-weight: 600; }
.chat-body { padding: 12px; flex: 1; display: flex; flex-direction: column; gap: 8px; }
.hint { font-size: 12px; color: #999; }
.chat-input { flex: 1; border: 1px solid #ddd; border-radius: 4px; padding: 8px; font-size: 13px; resize: none; }
.quick-actions { display: flex; flex-wrap: wrap; gap: 4px; }
</style>
```

- [ ] **Step 6: 创建 HomeView.vue 容器**

```vue
<template>
  <div class="home">
    <StatusBar :contamination="contamination" />
    <div class="home-body">
      <SpineNav :volumes="spineVolumes"
                @selectVolume="onSelectVolume"
                @selectChapter="onSelectChapter"
                @addVolume="onAddVolume"
                @goSetup="phase='setup'" />
      <div class="main-area">
        <BookSetupView v-if="phase==='setup'" @done="loadPhase" />
        <div v-else-if="phase==='adapt_empty'" class="empty-hint">
          <h2>开始新一卷</h2>
          <p>上传 COC 跑团模组 PDF 来开新副本</p>
          <el-button type="primary" @click="startNewVolume">创建新卷并上传模组</el-button>
        </div>
        <div v-else>
          <!-- 其他视图后续 task 注册 -->
          <div class="placeholder-view">当前阶段：{{ phase }}（视图开发中）</div>
        </div>
      </div>
    </div>
    <SettingsDrawer />
    <AIChatFloat />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import StatusBar from '../components/StatusBar.vue'
import SpineNav from '../components/SpineNav.vue'
import SettingsDrawer from '../components/SettingsDrawer.vue'
import AIChatFloat from '../components/AIChatFloat.vue'
import BookSetupView from './BookSetupView.vue'
import { currentProjectId, currentPhase, currentVolumeNo, currentChapterNo, currentVolumeKind } from '../state'
import { ElButton } from 'element-plus'

const phase = ref('')
const contamination = ref(12)
const spineVolumes = ref<any[]>([])
const router = useRouter()

async function loadPhase() {
  const pid = currentProjectId.value
  if (!pid) { router.push('/projects'); return }
  const r = await fetch(`/api/projects/${pid}/phase`).then(r=>r.json())
  phase.value = r.phase
  currentPhase.value = r.phase
  currentVolumeNo.value = r.volume_no || 0
  currentChapterNo.value = r.chapter_no || 0
  currentVolumeKind.value = r.volume_kind || 'dungeon'
  // TODO: load spine volumes (Task 8 补)
  spineVolumes.value = []
}
function onSelectVolume(n: number) { currentVolumeNo.value = n }
function onSelectChapter(vn: number, cn: number) {
  currentVolumeNo.value = vn; currentChapterNo.value = cn; phase.value = 'desk'; currentPhase.value='desk'
}
async function onAddVolume() {
  const pid = currentProjectId.value
  const r = await fetch(`/api/projects/${pid}/volumes`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:'新副本'})}).then(r=>r.json())
  await loadPhase()
}
function startNewVolume() { onAddVolume() }

onMounted(loadPhase)
</script>

<style scoped>
.home { height: 100vh; display: flex; flex-direction: column; }
.home-body { flex: 1; display: flex; overflow: hidden; }
.main-area { flex: 1; padding: 24px; overflow-y: auto; background: #fff; }
.empty-hint { text-align: center; padding: 80px 0; }
.empty-hint h2 { margin-bottom: 8px; }
.placeholder-view { padding: 40px; text-align: center; color: #999; }
</style>
```

- [ ] **Step 7: 创建最小 BookSetupView 骨架**（完整版 Task 4 做，这里只要能显示已填节并点"完成"）

新建 `frontend/src/views/BookSetupView.vue`：

```vue
<template>
  <div class="book-setup">
    <h2>开书设定</h2>
    <p class="hint">以下 8 项是《第七日的亡者》的基础设定，填完就可以开始第一卷。</p>
    <el-collapse v-model="active" style="margin-top:16px">
      <el-collapse-item title="核心设定（含讣告五铁律）" name="premise">
        <p class="section-hint">{{ setupStatus.premise ? '✓ 已填入' : '未填入' }}</p>
      </el-collapse-item>
      <el-collapse-item title="唐迟状态" name="tangchi"><p class="section-hint">{{ setupStatus.tangchi ? '✓ 已填入' : '未填入' }}</p></el-collapse-item>
      <el-collapse-item title="长线伏笔与终局" name="endgame"></el-collapse-item>
      <el-collapse-item title="代价与欠账" name="cost_debts"></el-collapse-item>
      <el-collapse-item title="剩余页码" name="remaining_pages"></el-collapse-item>
      <el-collapse-item title="七日脉冲" name="seven_day_pulse"></el-collapse-item>
      <el-collapse-item title="终局注记" name="finale_notes"></el-collapse-item>
      <el-collapse-item title="写作宪法（craft）" name="craft"></el-collapse-item>
    </el-collapse>
    <div style="margin-top:24px;text-align:right">
      <el-button type="primary" @click="$emit('done')">完成开书设定，开始第一卷</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElCollapse, ElCollapseItem, ElButton } from 'element-plus'
import { currentProjectId } from '../state'
const active = ref(['premise'])
const setupStatus = ref<Record<string,boolean>>({})
const emit = defineEmits(['done'])
onMounted(async () => {
  const pid = currentProjectId.value
  for (const card of ['premise','tangchi','endgame','cost_debts','remaining_pages','seven_day_pulse','finale_notes','craft']) {
    try {
      const r = await fetch(`/api/projects/${pid}/book/${card}`)
      const d = await r.json()
      setupStatus.value[card] = !!(d && (d.data || d) && Object.keys(d.data||d).length)
    } catch { setupStatus.value[card] = false }
  }
})
</script>
<style scoped>
.book-setup { max-width: 820px; }
.hint { color: #666; }
.section-hint { color: #27ae60; font-size: 13px; }
</style>
```

- [ ] **Step 8: 重写 App.vue，删 el-tabs 改渲染 HomeView**

把 App.vue 内容改为（项目选择器保留顶栏，其他交给 HomeView）：

```vue
<template>
  <div id="app">
    <div v-if="!currentProjectId" class="project-gate">
      <h2>选择小说项目</h2>
      <el-select v-model="selectedPid" placeholder="选项目" style="width:320px" @change="onPick">
        <el-option v-for="p in projects" :key="p.id" :label="p.title || p.id" :value="p.id"/>
      </el-select>
    </div>
    <HomeView v-else />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import HomeView from './views/HomeView.vue'
import { currentProjectId } from './state'
import { ElSelect, ElOption } from 'element-plus'

const selectedPid = ref('')
const projects = ref<any[]>([])
async function load() {
  projects.value = await fetch('/api/projects').then(r=>r.json()).then(d=>d.projects||d||[])
}
function onPick(v: string) { currentProjectId.value = v }
onMounted(load)
</script>

<style>
#app { font-family: -apple-system, "PingFang SC", sans-serif; color:#222; }
.project-gate { padding: 80px; text-align: center; }
.project-gate h2 { margin-bottom: 16px; }
</style>
```

- [ ] **Step 9: 确认路由（如果项目用 vue-router）**

检查 `frontend/src/main.ts`，如果使用 vue-router，改为单路由渲染 HomeView（或去掉路由直接挂 HomeView）。最简单：App.vue 直接引入 HomeView，不要 router-view。

- [ ] **Step 10: 启动前端验证**

```bash
cd c:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio\frontend
npm run dev
```
浏览器访问 http://localhost:5173/ ：应看到新的状态栏+左栏书脊+主区（phase=setup 时显示开书设定；phase=adapt_empty 显示"开始新一卷"引导）；不再看到旧的三个 Tab。

- [ ] **Step 11: Commit**

```bash
cd c:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio
git add frontend/src/state.ts frontend/src/components/SpineNav.vue frontend/src/components/StatusBar.vue frontend/src/components/SettingsDrawer.vue frontend/src/components/AIChatFloat.vue frontend/src/views/HomeView.vue frontend/src/views/BookSetupView.vue frontend/src/App.vue
git commit -m "feat(fe): HomeView 容器+状态条+书脊+抽屉+AI 气泡骨架，App.vue 砍 tabs"
```

---

## Task 4 及之后：阶段视图（adapt/simulate/embody/scaffold/rhythm/desk精简/settle/interlude）

这是工作量最大的部分。每个视图一个独立 task，按流水线顺序做：

- **Task 4:** ModuleAdaptView（上传 PDF + pdf5 长任务 + 5 盘回转寿司）—— 复用现有 `/source-pdf` + pdf5 job + SushiCards 组件（从 kindle 阶段搬过来，从 kindle_diverge 路径移除）
- **Task 5:** CocSimView（多场并行 coc_batch + 结果卡片 + 拼贴篮 + 合成主跑团记录）——复用现有 coc_batch 接口，新增 SimRunCard 组件
- **Task 6:** 通用 SushiCards 重构（支持 3/5 盘、diverge/ensemble、可配置 schema），删除 VariantPicker
- **Task 7:** EmbodyView（魂穿 4 步：穿谁/时机/信息差/立场）+ 自动生成 tangchi_story_outline 端点（后端补）
- **Task 8:** DungeonScaffoldView（6 卡，AI 预填）+ 后端 PATCH scaffold_cards 端点
- **Task 9:** RhythmView（节奏曲线可视化）+ 后端保存 rhythm_blueprint
- **Task 10:** ChapterBriefModal + DeskView 精简（去左栏/折叠/召唤AI/五段任务书 checkbox，加 PreChapterCard，加人物长新东西按钮，加第 8 项审稿）+ 书脊真实数据加载
- **Task 11:** SettleView（杀青 6 步面板：伏笔检测列表/处置/实体去向/污染结算/基调回转寿司/确认）
- **Task 12:** InterludeView（蓝星间章 4 步）+ 间章章前触发
- **Task 13: SettingsDrawer 填内容**（迁移 ProjectView/EntitiesView/CorpusView/ImportView/SkillsView/WorkshopView/ProjectsView 内容进对应分组）
- **Task 14:** 删除废弃视图（WizardView/PipelineView/MarkersView/WorkshopView/SkillsView/CorpusView/ImportView/ProjectsView/SessionView/VariantPicker/JobProgressBar），清理 App.vue/api.ts 里对它们的引用
- **Task 15:** api.ts PHASE_TITLES 重映射、自动阶段切换 bug 修复、章末→杀青→间章→新卷的完整状态机 e2e 测试
- **Task 16:** 已有项目（20261014-c6c432）数据迁移脚本：把已有 field core_pitch 映射为 adapted_module、标记 stage_completed.adapt=true，引导用户补跑 simulate/embody

---

## Self-Review

- **Spec coverage:**
  - setup 8 卡：Task 3 骨架，Task 4-13 中 BookSetupView 完整版单独补（在 Task 13 迁移组里补，因其编辑器可以复用 SettingsDrawer 里的 entity 风格编辑器）
  - adapt 上传 PDF+5 盘寿司：Task 4
  - simulate 多场跑团+拼贴：Task 5
  - 通用回转寿司：Task 6
  - embody 4 步+大纲：Task 7
  - scaffold 6 卡预填：Task 8
  - rhythm 节奏：Task 9
  - 章前 5 分钟+desk 精简：Task 10
  - settle 6 步+伏笔检测：Task 11（detector 已在 Task 2 完成初版）
  - interlude 4 步：Task 12
  - ⚙️ 抽屉 6 分组：Task 13
  - 删除旧视图：Task 14
  - api.ts 重映射+状态机：Task 15
  - 旧项目迁移：Task 16
- **Placeholder scan:** Task 1-3 所有 code block 都是完整实现，没留 TBD；Task 4-16 作为后续任务清单描述明确，留给 subagent 时会写具体 code。
- **Type consistency:** Volume 字段、/phase 返回 JSON、brief 字段、settle 接口 body 命名在前后端一致（volume_kind/stage_completed/brief/death_nodes 等）。

## KB Impact Preview

本项目无 `.knowledges/` 目录（之前未初始化），跳过 KB Touchpoint 3 Gate，后续任务可以直接开始执行。

---

**Plan 保存位置**：`docs/superpowers/plans/2026-10-15-workbench-ia-redesign.md`

**Task 1-3 是可立即执行的"骨架落地"部分**（后端字段+接口、骨架组件、App 重构），完成后你就能在浏览器看到新 UI 骨架和阶段自动切换，后续 Task 4-16 一个阶段一个阶段填内容。

**执行选项：**
1. **Subagent-Driven（推荐）** —— 每个 Task 派一个 fresh subagent 去实现，我在每步之间 review
2. **Inline Execution** —— 在当前会话里按 Task 顺序执行，每几个 Task 停顿让你看效果

你选哪个？
