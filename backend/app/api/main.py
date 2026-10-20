"""Novel Studio API 骨架（M0）：健康检查 + 通道状态 + 路由生成测试。

M1-T5：Prompt 工坊（查看/编辑/重置/干跑）。
"""
from __future__ import annotations
import hashlib
import json
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any, Iterator, Literal

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config as _config
from ..context import Material, MaterialFamily
from ..context.ledger import (
    build_state_materials,
    rhythm_hunger,
    strand_warnings,
)
from ..corpus import add_excerpts as corpus_add_excerpts
from ..corpus import article_exists as corpus_article_exists
from ..corpus import count as corpus_count
from ..corpus import list_tags, list_types, search as corpus_search
from ..corpus import replace_article as corpus_replace_article
from ..entities import (
    ENTITY_TYPES, TYPE_LABELS, EntityStore, PROTAGONIST_ID,
    StateChange, StateDiff, ProtagonistState, Relation,
    extract_entities,
)
from ..entities.vault import export_vault
from ..generation import PROSE_TOKENS_PER_CHAR, complete_structured
from ..importer import ImportJournal, analyze_chapter, split_import
from ..pipeline import (
    SPECS,
    PipelineRunner,
    PromptStore,
    RunJournal,
    apply_epilogue_to_book,
    apply_entities,
    apply_obsession_to_book,
    apply_split_to_field,
    apply_world_rules,
    compute_run_key,
    render_generation_messages,
    run_field_node,
)
from ..pipeline.prompts import OUTPUT_CONTRACT, SYSTEM_CONTRACT
from ..projects import ProjectStore
from ..providers import Router, get_router
from ..review import STATUS_PENDING, MarkerStore, book_review_checks, review_chapter
from ..review.markers import markers_material
from ..skills import MODULES, available_modules, module_rules, skill_materials
from ..wizard import (
    PAYOFF_LABELS,
    STATIONS,
    STRANDS,
    STRAND_LABELS,
    DeskStore,
    Scene,
    WizardStore,
    chapter_title_of,
    finalize_chapter,
    generate_direction_cards,
    outline_map,
    parse_outline,
    previous_tail,
    run_chapter_stage,
    work_text,
)
from ..fields import DayWork
from ..fields import FIELD_PIPELINE_NODES
from ..fields import (
    ALL_CARD_KEYS, SCAFFOLD_CARD_KEYS, SCHEDULE_CARD_KEY,
    WALKTHROUGH_CARD_KEY, CardLock,
)

app = FastAPI(title="Novel Studio API", version="0.4.0")

_prompt_store = PromptStore(persist_path=_config.REPO / "backend" / "data" / "prompt_store.json")
_marker_store = MarkerStore(_config.REPO / "backend" / "data" / "markers.json")
_project_store = ProjectStore(_config.REPO / "backend" / "data" / "projects")

_project_marker_stores: dict[str, MarkerStore] = {}
_project_entity_stores: dict[str, EntityStore] = {}
_project_book_stores: dict = {}     # pid -> BookStore (lazy)
_project_field_stores: dict = {}    # pid -> FieldStore (lazy)
_project_field_run_stores: dict = {}  # pid -> FieldRunStore (lazy)
_project_coc_run_stores: dict = {}    # pid -> CocRunStore (lazy)
_project_session_stores: dict = {}    # pid -> SessionStore (lazy)
_project_volume_stores: dict = {}     # pid -> VolumeStore (lazy)


def _markers_for(project: str | None) -> MarkerStore:
    """项目维度标记：project=None 用全局默认（兼容单书流程）。"""
    if project is None:
        return _marker_store
    if project not in _project_marker_stores:
        _project_marker_stores[project] = MarkerStore(
            _config.REPO / "backend" / "data" / "projects" / project / "markers.json"
        )
    return _project_marker_stores[project]


def _require_project(pid: str):
    try:
        return _project_store.get(pid)
    except (FileNotFoundError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail=f"项目不存在：{pid}")


def _entities_for(pid: str) -> EntityStore:
    """实体库按项目隔离；缓存实例避免重复读盘。"""
    if pid not in _project_entity_stores:
        _project_entity_stores[pid] = EntityStore(
            _config.REPO / "backend" / "data" / "projects" / pid / "entities.json"
        )
    return _project_entity_stores[pid]


def _project_dir(pid: str) -> Path:
    return _config.REPO / "backend" / "data" / "projects" / pid


def _book_for(pid: str):
    """书级硬卡存储（惰性创建；book/ 目录自动生成默认卡）。"""
    if pid not in _project_book_stores:
        from ..book import BookStore
        _project_book_stores[pid] = BookStore(_project_dir(pid) / "book")
    return _project_book_stores[pid]


def _fields_for(pid: str):
    """场存储（惰性创建；fields/ 目录在首次写入时创建）。"""
    if pid not in _project_field_stores:
        from ..fields import FieldStore
        _project_field_stores[pid] = FieldStore(_project_dir(pid) / "fields")
    return _project_field_stores[pid]


def _field_run_for(pid: str):
    """场流水线节点中间产物存储（惰性创建）。"""
    if pid not in _project_field_run_stores:
        from ..fields import FieldRunStore
        _project_field_run_stores[pid] = FieldRunStore(_project_dir(pid) / "fields" / "nodes")
    return _project_field_run_stores[pid]


def _coc_run_for(pid: str):
    """COC 多轮跑团会话存储（惰性创建）。"""
    if pid not in _project_coc_run_stores:
        from ..fields import CocRunStore
        _project_coc_run_stores[pid] = CocRunStore(_project_dir(pid) / "fields" / "coc")
    return _project_coc_run_stores[pid]


def _sessions_for(pid: str):
    if pid not in _project_session_stores:
        from ..sessions import SessionStore
        _project_session_stores[pid] = SessionStore(_project_dir(pid) / "sessions")
    return _project_session_stores[pid]


def _volumes_for(pid: str):
    """卷存储（惰性创建；volumes/ 目录自动建）。"""
    if pid not in _project_volume_stores:
        from ..book import VolumeStore
        _project_volume_stores[pid] = VolumeStore(_project_dir(pid) / "volumes")
    return _project_volume_stores[pid]


def _volume_for_chapter(pid: str, chapter: int):
    """按章号定位其所属卷（优先 field_no 匹配），找不到回退到最新卷。"""
    vs = _volumes_for(pid)
    desk = _desk_for(pid)
    work = desk.get(chapter)
    target_vn = None
    if work is not None and getattr(work, "field_no", None):
        target_vn = int(work.field_no)
    if target_vn is None:
        target_vn = len(vs.all_list()) or 1
    try:
        return vs.get(target_vn)
    except Exception:
        vols = vs.all_list()
        return vols[-1] if vols else None


class SessionCreateIn(BaseModel):
    scope: str = "field"                # field | chapter
    field_no: int | None = None
    chapter: int | None = None
    title: str = ""
    start_phase: str | None = None


class SessionSendIn(BaseModel):
    text: str
    kind: str = "chat"                 # chat | card_edit | answer
    as_ai: bool = False                # true=AI 消息（测试/预置），false=作者
    output_key: str | None = None      # 如果填了，把这段文本同时写入 outputs[key]
    meta: dict | None = None


class SessionGateIn(BaseModel):
    key: str                           # 必填产物键
    value: Any                         # 值（bool/str/dict/list）


class SessionRollbackIn(BaseModel):
    to_phase: str


class SessionActionIn(BaseModel):
    action: str                        # kindle_diverge | daily_summon | walkthrough_step | ...
    args: dict | None = None           # 动作参数
    n_variants: int = 3                # kindle_diverge 默认 3 个雏形


class GenerateIn(BaseModel):
    step: str = "draft"                      # outline/chapter/plot/draft/polish/review/debug/chore
    prompt: str
    system: str | None = None
    model: str | None = None                 # 覆盖通道默认模型
    max_tokens: int | None = None


def _messages(body: GenerateIn) -> list[dict]:
    msgs = []
    if body.system:
        msgs.append({"role": "system", "content": body.system})
    msgs.append({"role": "user", "content": body.prompt})
    return msgs


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/providers")
def providers_status() -> dict:
    router: Router = get_router()
    return {name: p.health() for name, p in router.channels.items()}


@app.post("/generate")
def generate(body: GenerateIn) -> dict:
    """非流式生成（集成测试/简单调用）。"""
    router: Router = get_router()
    resp = router.chat(body.step, _messages(body), body.model, body.max_tokens)
    return {"text": resp.text, "usage": resp.usage.as_dict(), "step": body.step}


@app.post("/generate/stream")
def generate_stream(body: GenerateIn) -> StreamingResponse:
    """SSE 流式生成。"""
    router: Router = get_router()

    def gen() -> Iterator[str]:
        for ev in router.chat_stream(body.step, _messages(body),
                                     body.model, body.max_tokens):
            if ev.kind == "delta":
                yield f"event: delta\ndata: {json_dumps(ev.text)}\n\n"
            elif ev.kind == "usage":
                yield f"event: usage\ndata: {json_dumps(ev.usage.as_dict())}\n\n"
            elif ev.kind == "error":
                yield f"event: notice\ndata: {json_dumps(ev.text)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _paginate(items: list, page: int | None, page_size: int) -> tuple[list, dict]:
    """列表分页：page 缺省不分页（响应保持旧形状，兼容旧调用方）；
    给出 page 时返回切片并附 total/page/page_size 元信息。"""
    if page is None:
        return items, {}
    size = max(1, min(page_size, 200))
    start = (max(1, page) - 1) * size
    return items[start:start + size], {"total": len(items), "page": page, "page_size": size}


# ---- Prompt 工坊（M1-T5）----

class PromptEditIn(BaseModel):
    text: str
    note: str = ""


class DryRunIn(BaseModel):
    x_block: str = ""
    user_directive: str | None = None
    sections: dict[str, str] | None = None
    layer: str | None = None  # 与 URL 冲突时以 URL 为准


def _prompt_summary(layer: str) -> dict:
    return {
        "layer": layer,
        "effective": _prompt_store.effective(layer),
        "default": _prompt_store.default(layer),
        "overridden": _prompt_store.is_overridden(layer),
        "history_count": len(_prompt_store.history(layer)),
    }


@app.get("/api/prompts")
def prompts_list() -> dict:
    return {"layers": [_prompt_summary(layer) for layer in PromptStore.layers()]}


@app.get("/api/prompts/{layer}")
def prompts_detail(layer: str, page: int | None = None, page_size: int = 20) -> dict:
    try:
        summary = _prompt_summary(layer)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    history = [v.as_dict() for v in _prompt_store.history(layer)]
    history, page_meta = _paginate(history, page, page_size)
    summary["history"] = history
    summary.update(page_meta)
    return summary


@app.put("/api/prompts/{layer}")
def prompts_edit(layer: str, body: PromptEditIn) -> dict:
    try:
        effective = _prompt_store.set_override(layer, body.text, note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"layer": layer, "effective": effective, "overridden": True}


@app.delete("/api/prompts/{layer}")
def prompts_reset(layer: str) -> dict:
    try:
        effective = _prompt_store.reset(layer)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"layer": layer, "effective": effective, "overridden": False}


@app.post("/api/prompts/{layer}/dry_run")
def prompts_dry_run(layer: str, body: DryRunIn) -> dict:
    """只渲染不调用：返回该层最终发出的 messages、警告与 token 估算。"""
    try:
        rendered = render_generation_messages(
            _prompt_store,
            layer,
            body.x_block,
            body.sections or {},
            body.user_directive,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "layer": layer,
        "messages": rendered.messages,
        "warnings": list(rendered.warnings),
        "prompt_bytes": rendered.prompt_bytes,
        "est_prompt_tokens": rendered.est_prompt_tokens,
    }


# ---- 批量运行 + 断点收据（M1-T9）----

class MaterialIn(BaseModel):
    source_id: str
    family: Literal["required", "finalized", "candidate", "reference"] = "reference"
    content: str
    title: str = ""


class CorpusQueryIn(BaseModel):
    """运行时范文检索：命中片段作为参考资料注入（先借结构再动笔）。"""
    excerpt_type: str | None = None
    tag: str | None = None
    keyword: str | None = None
    limit: int = 3


class LayerRunIn(BaseModel):
    x_pairs: list[tuple[str, str]]
    materials: list[MaterialIn] | None = None
    user_directive: str | None = None
    x_chunk_length: int | None = None
    modules: list[str] | None = None      # 写作模块；缺省用层默认集
    corpus_query: CorpusQueryIn | None = None
    chapter_no: int | None = None         # 提供时自动注入该章 pending 必现标记
    project: str | None = None            # 提供时收据与标记按项目隔离
    project_state: bool = False           # 注入项目状态账本（已写剧情/正文近况/实体账本）


_FAMILIES = {"context": MaterialFamily.CONTEXT, "required": MaterialFamily.REQUIRED,
             "finalized": MaterialFamily.FINALIZED,
             "candidate": MaterialFamily.CANDIDATE, "reference": MaterialFamily.REFERENCE}


def _journal_path(run_id: str, project: str | None = None):
    if project:
        return _config.REPO / "backend" / "data" / "projects" / project / "runs" / f"{run_id}.json"
    return _config.REPO / "backend" / "data" / "runs" / f"{run_id}.json"


def _router_provider(step: str):
    """把 Router（带 failover 的 step 路由）适配成 PipelineRunner 需要的 Provider。

    仅当底层路由支持流式时才暴露 chat_stream，runner 据此选择流式/整块路径。
    """
    router: Router = get_router()

    class _Adapter:
        name = f"router:{step}"
        default_model = None

        @staticmethod
        def chat(messages, model=None, max_tokens=None, extra=None):
            return router.chat(step, messages, model, max_tokens)

    if hasattr(router, "chat_stream"):
        def chat_stream(messages, model=None, max_tokens=None, extra=None):
            yield from router.chat_stream(step, messages, model, max_tokens)

        _Adapter.chat_stream = staticmethod(chat_stream)
    return _Adapter()


def _prepare_layer(layer: str, body: LayerRunIn) -> dict:
    """层运行共用准备：材料装配 + 运行键 + 幂等收据 + runner。"""
    if layer not in SPECS:
        raise HTTPException(status_code=404, detail=f"未知层：{layer}")
    spec = SPECS[layer]
    mats = [
        Material(source_id=m.source_id, family=_FAMILIES[m.family], content=m.content, title=m.title)
        for m in (body.materials or [])
    ]
    try:
        injected_skills = skill_materials(layer, body.modules)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    corpus_mats = []
    if body.corpus_query:
        for hit in corpus_search(excerpt_type=body.corpus_query.excerpt_type,
                                 tag=body.corpus_query.tag, keyword=body.corpus_query.keyword,
                                 limit=max(1, min(body.corpus_query.limit, 10))):
            corpus_mats.append(Material(
                source_id=hit.to_material_dict()["source_id"],
                family=MaterialFamily.REFERENCE,
                content=hit.text,
                title=hit.to_material_dict()["title"],
                keywords=hit.tags,
            ))
    marker_mat = None
    if body.project:
        _require_project(body.project)
    state_mats: list[Material] = []
    if body.project_state:
        if not body.project:
            raise HTTPException(status_code=422, detail="project_state 需要同时提供 project")
        state_mats = build_state_materials(
            _project_store, _entities_for(body.project), _desk_for(body.project), body.project,
            book_store=_book_for(body.project), field_store=_fields_for(body.project))
    if body.chapter_no is not None:
        marker_mat = markers_material(
            _markers_for(body.project).pending_for(body.chapter_no), body.chapter_no)
    all_mats = (([marker_mat] if marker_mat else []) + injected_skills
                + state_mats + mats + corpus_mats)
    x_list = [x for x, _ in body.x_pairs]
    run_key = compute_run_key(layer, x_list,
                              [(m.source_id, m.family.value, m.content) for m in all_mats],
                              body.user_directive)
    journal = RunJournal(_journal_path(run_key, body.project), layer, run_key)
    runner = PipelineRunner(_router_provider(layer), prompt_store=_prompt_store)
    return {"spec": spec, "mats": all_mats, "run_key": run_key, "journal": journal,
            "runner": runner, "skills": injected_skills, "corpus": corpus_mats,
            "state": state_mats}


def _layer_response(prep: dict, result) -> dict:
    journal: RunJournal = prep["journal"]
    chapter_no = prep.get("chapter_no")
    markers = _markers_for(prep.get("project"))
    return {
        "run_id": prep["run_key"],
        "layer": prep["spec"].name,
        "y": result.y,
        "pairs": result.pairs,
        "resumed_indices": result.resumed_indices,
        "skills_injected": [m.source_id for m in prep["skills"]],
        "corpus_injected": [m.source_id for m in prep["corpus"]],
        "state_injected": [m.title for m in prep["state"]],
        "markers_injected": [m.as_dict() for m in markers.pending_for(chapter_no or 0)]
        if chapter_no is not None else [],
        "calls": [{"completion_status": c.completion_status, "attempts": c.attempts}
                  for c in result.calls],
        "journal": journal.summary(),
    }


def _run_layer(layer: str, body: LayerRunIn) -> dict:
    prep = _prepare_layer(layer, body)
    prep["project"] = body.project
    prep["chapter_no"] = body.chapter_no
    try:
        result = prep["runner"].run_layer(prep["spec"], body.x_pairs, materials=prep["mats"],
                                          user_directive=body.user_directive,
                                          x_chunk_length=body.x_chunk_length,
                                          journal=prep["journal"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成失败（已完成块已留收据）：{e}")
    return _layer_response(prep, result)


@app.post("/api/runs/{layer}")
def runs_start(layer: str, body: LayerRunIn) -> dict:
    """运行一层流水线；同输入重跑自动命中收据（幂等），失败块可重试。"""
    return _run_layer(layer, body)


@app.post("/api/runs/{layer}/retry")
def runs_retry(layer: str, body: LayerRunIn) -> dict:
    """幂等重试：语义同 runs_start，completed 块不重生成，只补失败/缺失块。"""
    return _run_layer(layer, body)


@app.post("/api/runs/{layer}/stream")
def runs_stream(layer: str, body: LayerRunIn) -> StreamingResponse:
    """SSE 流式运行：实时透出思考过程（thinking）与正文增量（delta），
    每块完成发 block_done，收据复用发 resumed，结束时发 result + done。"""
    prep = _prepare_layer(layer, body)
    prep["project"] = body.project
    prep["chapter_no"] = body.chapter_no

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json_dumps(data)}\n\n"

    def gen() -> Iterator[str]:
        q: Queue = Queue()
        END = object()

        def work():
            try:
                def on_event(kind, payload):
                    q.put((kind, payload))
                result = prep["runner"].run_layer(
                    prep["spec"], body.x_pairs, materials=prep["mats"],
                    user_directive=body.user_directive,
                    x_chunk_length=body.x_chunk_length,
                    journal=prep["journal"], on_event=on_event)
                q.put(("result", _layer_response(prep, result)))
            except Exception as e:
                q.put(("fail", {"detail": f"生成失败（已完成块已留收据）：{e}"}))
            finally:
                q.put((END, None))

        threading.Thread(target=work, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind is END:
                break
            yield sse(kind, payload)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class RefineIn(BaseModel):
    text: str
    directive: str
    project: str | None = None


@app.post("/api/refine/stream")
def refine_stream(body: RefineIn) -> StreamingResponse:
    """迭代润色：在【原文】基础上按【要求】调整，SSE 实时透出 thinking/delta，
    结束发 result（y 为调整后全文）。走 refine 路由（提示词可在工坊改）。"""
    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json_dumps(data)}\n\n"

    def gen() -> Iterator[str]:
        q: Queue = Queue()
        END = object()

        def work():
            parts: list[str] = []
            try:
                instruction = _prompt_store.effective("refine")
                user = (f"{instruction}\n\n【原文】\n{body.text}\n\n【要求】\n{body.directive}\n{OUTPUT_CONTRACT}")
                messages = [
                    {"role": "system", "content": SYSTEM_CONTRACT},
                    {"role": "user", "content": user},
                ]
                # 输出预算 = 正文等长 + 思考余量（推理模型思考与正文共享 max_tokens），
                # 不做人为上限——131072 是 MiniMax-M3 的模型硬顶，非产品限制
                out_budget = min(131_072, max(8_192,
                                              int(len(body.text) * PROSE_TOKENS_PER_CHAR) + 4_096))
                for ev in get_router().chat_stream("refine", messages, max_tokens=out_budget):
                    if ev.kind == "delta":
                        parts.append(ev.text)
                        q.put(("delta", {"text": ev.text}))
                    elif ev.kind == "thinking":
                        q.put(("thinking", {"text": ev.text}))
                    elif ev.kind == "error":
                        q.put(("notice", {"text": ev.text}))
                y = "".join(parts).strip()
                if not y:
                    # 空输出多半是思考耗尽预算被截断：报失败让前端还原原文，绝不静默清空
                    q.put(("fail", {"detail": "模型输出为空：思考可能耗尽了 token 预算，请重试"}))
                else:
                    if y == body.text.strip():
                        q.put(("notice", {"text": "模型未产生任何改动：输出与原文完全相同，可换更具体的调整要求重试"}))
                    q.put(("result", {"y": y}))
            except Exception as e:
                q.put(("fail", {"detail": f"调整失败：{e}"}))
            finally:
                q.put((END, None))

        threading.Thread(target=work, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind is END:
                break
            yield sse(kind, payload)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}")
def runs_status(run_id: str) -> dict:
    path = _journal_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"运行不存在：{run_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    journal = RunJournal(path, data.get("layer", ""), data.get("run_key", ""))
    return journal.summary()


# ---- 写作模块库（M1-T10）----

@app.get("/api/skills/modules")
def skills_list() -> dict:
    return {"modules": available_modules()}


@app.get("/api/skills/modules/{name}")
def skills_detail(name: str) -> dict:
    if name not in MODULES:
        raise HTTPException(status_code=404, detail=f"未知写作模块：{name}")
    return {"name": name, "title": MODULES[name]["title"], "rules": module_rules(name)}


# ---- 范文语料检索（M1-T10）----

@app.get("/api/corpus/types")
def corpus_types() -> dict:
    return {"types": [{"type": t, "count": c} for t, c in list_types()]}


@app.get("/api/corpus/tags")
def corpus_tags() -> dict:
    return {"tags": [{"tag": t, "count": c} for t, c in list_tags()]}


@app.get("/api/corpus/search")
def corpus_query(excerpt_type: str | None = None, tag: str | None = None,
                 keyword: str | None = None, limit: int = 5,
                 page: int | None = None, page_size: int = 20) -> dict:
    if page is not None:
        size = max(1, min(page_size, 50))
        hits = corpus_search(excerpt_type=excerpt_type, tag=tag, keyword=keyword,
                             limit=size, offset=(max(1, page) - 1) * size)
        total = corpus_count(excerpt_type=excerpt_type, tag=tag, keyword=keyword)
        return {"total": total, "page": page, "page_size": size,
                "excerpts": [e.as_dict() for e in hits]}
    hits = corpus_search(excerpt_type=excerpt_type, tag=tag, keyword=keyword,
                         limit=max(1, min(limit, 20)))
    return {"total": len(hits), "excerpts": [e.as_dict() for e in hits]}


# ---- 逐项目标审稿（M2）----

class ChapterReviewIn(BaseModel):
    outline: str = ""
    prose: str
    model: str | None = None
    prev_tail: str = ""      # 上一章结尾：给定时作为第七项「跨章联结」的对照材料


@app.post("/api/review/chapter")
def review_chapter_api(body: ChapterReviewIn) -> dict:
    """七项一致性逐项审查；不稳 >= 2 判 blocked（不交稿）。"""
    router: Router = get_router()

    class _Adapter:
        name = "router:review"
        default_model = None

        @staticmethod
        def chat(messages, model=None, max_tokens=None, extra=None):
            return router.chat("review", messages, model, max_tokens)

    try:
        review = review_chapter(_Adapter(), _prompt_store, body.outline, body.prose,
                                model=body.model, prev_tail=body.prev_tail)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return review.as_dict()


# ---- 必现标记（M2）----

class MarkerIn(BaseModel):
    target_chapter: int
    content: str


class MarkerExtractIn(BaseModel):
    text: str


class MarkerStatusIn(BaseModel):
    status: Literal["pending", "done", "dropped"]


class MarkerVerifyIn(BaseModel):
    chapter_no: int | None = None
    prose: str


class MarkersFromReviewIn(BaseModel):
    """审稿不稳项 → 下一章必现标记（修复要求落位）。"""
    problems: list[str]
    next_chapter: int


def _marker_summary(project: str | None) -> dict:
    store = _markers_for(project)
    return {
        "project": project,
        "markers": [m.as_dict() for m in store.markers],
        "pending_count": sum(1 for m in store.markers if m.status == STATUS_PENDING),
    }


@app.get("/api/markers")
def markers_list(project: str | None = None, page: int | None = None, page_size: int = 20) -> dict:
    summary = _marker_summary(project)
    markers, page_meta = _paginate(summary["markers"], page, page_size)
    summary["markers"] = markers
    summary.update(page_meta)
    return summary


@app.post("/api/markers")
def markers_add(body: MarkerIn, project: str | None = None) -> dict:
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="标记内容不能为空")
    if project:
        _require_project(project)
    _markers_for(project).add(body.target_chapter, body.content)
    return _marker_summary(project)


@app.post("/api/markers/extract")
def markers_extract(body: MarkerExtractIn, project: str | None = None) -> dict:
    added = _markers_for(project).extract_and_add(body.text)
    return {**_marker_summary(project), "added": [m.as_dict() for m in added]}


@app.put("/api/markers/{marker_id}/status")
def markers_status(marker_id: str, body: MarkerStatusIn, project: str | None = None) -> dict:
    try:
        _markers_for(project).set_status(marker_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _marker_summary(project)


@app.post("/api/markers/verify")
def markers_verify(body: MarkerVerifyIn, project: str | None = None) -> dict:
    return {"results": _markers_for(project).verify(body.prose, body.chapter_no)}


@app.post("/api/markers/from_review")
def markers_from_review(body: MarkersFromReviewIn, project: str | None = None) -> dict:
    store = _markers_for(project)
    for p in body.problems:
        if p.strip():
            store.add(body.next_chapter, f"必须修复：{p.strip()}", source="review")
    return _marker_summary(project)


# ---- 小说项目（M3a）----

class ProjectIn(BaseModel):
    title: str
    intro: str = ""


class ProjectUpdateIn(BaseModel):
    title: str | None = None
    intro: str | None = None


class AdoptIn(BaseModel):
    x_pairs: list[tuple[str, str]] = []
    y: str
    note: str = ""


class RollbackIn(BaseModel):
    seq: int


@app.get("/api/projects")
def projects_list(page: int | None = None, page_size: int = 20) -> dict:
    out = []
    for meta in _project_store.list():
        meta["wizard_summary"] = _wizard_summary(meta["id"])
        out.append(meta)
    out, page_meta = _paginate(out, page, page_size)
    return {"projects": out, **page_meta}


@app.post("/api/projects")
def projects_create(body: ProjectIn) -> dict:
    try:
        meta = _project_store.create(body.title, body.intro)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return meta.as_dict()


@app.get("/api/projects/{pid}")
def projects_detail(pid: str) -> dict:
    _require_project(pid)
    meta = _project_store.get(pid)
    return {**meta.as_dict(), "layers": {
        layer: {
            "current": (_project_store.current(pid, layer).as_summary()
                        if _project_store.current(pid, layer) else None),
            "history": [v.as_summary() for v in _project_store.history(pid, layer)],
        }
        for layer in ("intro", "outline", "plot", "draft", "polish")
    }}


@app.put("/api/projects/{pid}")
def projects_update(pid: str, body: ProjectUpdateIn) -> dict:
    _require_project(pid)
    try:
        return _project_store.update(pid, body.title, body.intro).as_dict()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/projects/{pid}")
def projects_delete(pid: str) -> dict:
    _require_project(pid)
    _project_store.delete(pid)
    return {"deleted": pid}


@app.get("/api/projects/{pid}/layers/{layer}")
def project_layer(pid: str, layer: str, seq: int | None = None,
                  page: int | None = None, page_size: int = 20) -> dict:
    """层详情：seq 缺省看当前版本，否则看指定历史版本。history 支持 page 分页。"""
    _require_project(pid)
    if layer not in ("intro", "outline", "plot", "draft", "polish"):
        raise HTTPException(status_code=404, detail=f"未知层：{layer}")
    v = _project_store.version(pid, layer, seq) if seq else _project_store.current(pid, layer)
    history, page_meta = _paginate(
        [x.as_summary() for x in _project_store.history(pid, layer)], page, page_size)
    return {
        "version": v.as_dict() if v else None,
        "history": history,
        **page_meta,
    }


@app.post("/api/projects/{pid}/layers/{layer}")
def project_adopt(pid: str, layer: str, body: AdoptIn) -> dict:
    """采纳一层产出（生成结果人工编辑后入库，版本留痕）。"""
    _require_project(pid)
    if not body.y.strip():
        raise HTTPException(status_code=422, detail="采纳内容不能为空")
    v = _project_store.adopt(pid, layer, body.x_pairs, body.y, body.note)
    # 采纳 outline 时同步章→场/日/间章绑定到 DeskStore，并维护 FieldStore 章分配
    if layer == "outline":
        _sync_outline_meta(pid, body.y)
    return v.as_dict()


def _sync_outline_meta(pid: str, text: str) -> None:
    """大纲采纳时：根据「第N场/第X天/现实」头，把 chapter 的 field_no/day/is_interlude
    写入 DeskStore，并同步 FieldStore 的 Day.chapters / interlude_chapters。"""
    try:
        from ..wizard import parse_outline_ext
        _, _, chapter_meta = parse_outline_ext(text)
        if not chapter_meta:
            return
        desk = _desk_for(pid)
        fs = _fields_for(pid)
        # 先清场：把 field/day/is_interlude 设为 None/False 避免旧数据残留
        for no in desk.all_nos():
            w = desk.get(no)
            if w is not None and (w.field_no is not None or w.day is not None or w.is_interlude):
                desk.assign_field(no, None, None, False)
        # 按 field/day 分组，便于批量写入 FieldStore
        field_day_chapters: dict[tuple, list[int]] = {}
        field_interlude_chapters: dict[int, list[int]] = {}
        for no, m in chapter_meta.items():
            fno = m.get("field_no")
            day = m.get("day")
            inter = bool(m.get("is_interlude"))
            desk.assign_field(no, fno, day, inter)
            if fno is None:
                continue
            if inter:
                field_interlude_chapters.setdefault(fno, []).append(no)
            elif day is not None:
                field_day_chapters.setdefault((fno, day), []).append(no)
        # 同步 FieldStore（仅对已存在的场；未创建的场不自动生成，由用户先建场）
        # 先把所有已存在场的 day/interlude 章分配清空，再按新 outline 重建
        for fno in fs.all_nos():
            fw = fs.get(fno)
            if fw is None:
                continue
            for dw in fw.days.values():
                dw.chapters = []
            fw.interlude_chapters = []
            fs._flush(fw)
        for (fno, day), chs in field_day_chapters.items():
            if fs.get(fno) is not None:
                fs.assign_chapters_to_day(fno, day, sorted(chs))
        for fno, chs in field_interlude_chapters.items():
            if fs.get(fno) is not None:
                fs.assign_interlude_chapters(fno, sorted(chs))
    except Exception:
        # 同步失败不阻断采纳（旧格式/解析异常不影响使用）
        pass


@app.post("/api/projects/{pid}/layers/{layer}/rollback")
def project_rollback(pid: str, layer: str, body: RollbackIn) -> dict:
    _require_project(pid)
    try:
        v = _project_store.rollback(pid, layer, body.seq)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return v.as_dict()


# ---- 实体库（M3b）----

class EntityIn(BaseModel):
    type: Literal["character", "location", "item", "plotline", "foreshadow"]
    name: str
    summary: str = ""
    details: str = ""
    status: str | None = None
    chapter: int | None = None


class EntityUpdateIn(BaseModel):
    name: str | None = None
    summary: str | None = None
    details: str | None = None
    status: str | None = None


class EntityExtractIn(BaseModel):
    text: str
    chapter: int | None = None
    model: str | None = None


def _entity_summary(pid: str) -> dict:
    store = _entities_for(pid)
    counts: dict[str, int] = {t: 0 for t in ENTITY_TYPES}
    for e in store.entities:
        counts[e.type] += 1
    return {
        "project": pid,
        "entities": [e.as_dict() for e in store.entities],
        "counts": counts,
        "total": len(store.entities),
    }


@app.get("/api/projects/{pid}/entities")
def entities_list(pid: str, type: str | None = None, status: str | None = None,
                  keyword: str | None = None,
                  page: int | None = None, page_size: int = 20) -> dict:
    _require_project(pid)
    if type and type not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"未知实体类型：{type}")
    store = _entities_for(pid)
    found = store.list(type=type, status=status, keyword=keyword)
    items, page_meta = _paginate([e.as_dict() for e in found], page, page_size)
    return {
        "project": pid,
        "entities": items,
        "counts": {t: sum(1 for e in store.entities if e.type == t)
                   for t in ENTITY_TYPES},
        "total": len(store.entities),
        "matched": len(found),
        **page_meta,
    }


# ---- 主角（唐迟）档案 ----
class ProtagonistPatchIn(BaseModel):
    current_body_name: str | None = None
    current_body_identity: str | None = None
    current_location: str | None = None
    sanity_hint: str | None = None
    known_truths: list[str] | None = None
    key_relations: list[str] | None = None
    blue_star_bonds: list[dict] | None = None
    contamination_level: int | None = None
    # 允许整段替换 profile_text（给 AI 一段自由文本描述，覆盖 current_body_identity/sanity_hint 的短字段）
    profile_text: str | None = None


@app.get("/api/projects/{pid}/protagonist")
def protagonist_get(pid: str) -> dict:
    _require_project(pid)
    es = _entities_for(pid)
    return {"protagonist": es.protagonist.as_dict()}


@app.patch("/api/projects/{pid}/protagonist")
def protagonist_patch(pid: str, body: ProtagonistPatchIn) -> dict:
    _require_project(pid)
    es = _entities_for(pid)
    p = es.protagonist
    data = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else body.dict(exclude_none=True)
    profile_text = data.pop("profile_text", None)
    for k, v in data.items():
        if hasattr(p, k):
            setattr(p, k, v)
    if profile_text is not None:
        # profile_text 作为长文本人设简介，存进 sanity_hint 后面追加标记不适合——
        # 另起一个字段要改 dataclass，简化：直接覆盖 current_body_identity（身份长描述）
        p.current_body_identity = profile_text
    if p.contamination_level is not None:
        p.contamination_level = max(0, min(100, int(p.contamination_level or 0)))
    from datetime import datetime
    p.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    es._flush()
    return {"ok": True, "protagonist": p.as_dict()}


@app.post("/api/projects/{pid}/entities")
def entities_add(pid: str, body: EntityIn) -> dict:
    _require_project(pid)
    try:
        e = _entities_for(pid).add(body.type, body.name, body.summary, body.details,
                                   status=body.status, chapter=body.chapter)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex))
    return e.as_dict()


@app.put("/api/projects/{pid}/entities/{eid}")
def entities_update(pid: str, eid: str, body: EntityUpdateIn) -> dict:
    _require_project(pid)
    try:
        return _entities_for(pid).update(
            eid, body.name, body.summary, body.details, body.status).as_dict()
    except ValueError as ex:
        raise HTTPException(status_code=404 if "不存在" in str(ex) else 422, detail=str(ex))


@app.delete("/api/projects/{pid}/entities/{eid}")
def entities_delete(pid: str, eid: str) -> dict:
    _require_project(pid)
    try:
        _entities_for(pid).delete(eid)
    except ValueError as ex:
        raise HTTPException(status_code=404, detail=str(ex))
    return {"deleted": eid}


@app.post("/api/projects/{pid}/entities/extract")
def entities_extract(pid: str, body: EntityExtractIn) -> dict:
    """LLM 从文本抽取实体并合并入库（同类型同名只补空与章节）。"""
    _require_project(pid)
    try:
        result = extract_entities(_chore_provider(), _entities_for(pid), _prompt_store,
                                  body.text, chapter=body.chapter, model=body.model)
    except ValueError as ex:
        raise HTTPException(status_code=502, detail=str(ex))
    return {
        "created": result.created,
        "updated": result.updated,
        "dropped": result.dropped,
        "attempts": result.attempts,
        **_entity_summary(pid),
    }


# ---- Obsidian Vault 导出（M3b）----

class VaultExportIn(BaseModel):
    out_dir: str | None = None   # 缺省导出到 backend/data/exports/{pid}


@app.post("/api/projects/{pid}/export/vault")
def projects_export_vault(pid: str, body: VaultExportIn | None = None) -> dict:
    """导出 Obsidian Vault：MOC 首页 + 层级/章节/实体互链目录。"""
    _require_project(pid)
    out_root = Path((body.out_dir if body and body.out_dir else "")
                    or (_config.REPO / "backend" / "data" / "exports" / pid))
    result = export_vault(_project_store, _entities_for(pid), pid, out_root)
    return {
        "out_dir": result.out_dir,
        "files": result.files,
        "chapters": result.chapters,
        "entities": result.entities,
        "layers_exported": result.layers_exported,
        "warnings": result.warnings,
    }


# ---- 导入拆解（M4）----

_import_journals: dict[str, ImportJournal] = {}


def _import_journal_for(pid: str) -> ImportJournal:
    if pid not in _import_journals:
        _import_journals[pid] = ImportJournal(
            _config.REPO / "backend" / "data" / "projects" / pid / "import_journal.json"
        )
    return _import_journals[pid]


class ImportTextIn(BaseModel):
    text: str
    dry_run: bool = True   # true 只预览切分；false 采纳为当前 draft 新版本


@app.post("/api/projects/{pid}/import/text")
def import_text(pid: str, body: ImportTextIn) -> dict:
    _require_project(pid)
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="导入文本不能为空")
    chapters = split_import(body.text)
    if not chapters:
        raise HTTPException(status_code=422, detail="未能从文本中识别出任何内容")
    resp: dict = {
        "chapters": [{k: c[k] for k in ("no", "title", "chars")} for c in chapters],
        "total": len(chapters),
    }
    if body.dry_run:
        resp["adopted"] = False
        return resp
    v = _project_store.adopt(
        pid, "draft",
        x_pairs=[(f"第{c['no']}章", "") for c in chapters],
        y=body.text.strip(),
        note=f"导入原文（{len(chapters)} 章）",
    )
    resp["adopted"] = True
    resp["draft_version"] = v.seq
    return resp


def _chore_provider(step: str = "chore"):
    router: Router = get_router()

    class _Adapter:
        name = f"router:{step}"
        default_model = None

        @staticmethod
        def chat(messages, model=None, max_tokens=None, extra=None):
            return router.chat(step, messages, model, max_tokens)

    return _Adapter()


class ImportAnalyzeIn(BaseModel):
    chapter: int
    text: str | None = None   # 缺省从当前 draft 版本按章号取
    force: bool = False       # true 忽略已完成的收据重拆
    model: str | None = None


def _chapter_text_from_draft(pid: str, chapter: int) -> str:
    v = _project_store.current(pid, "draft")
    if v is None:
        raise HTTPException(status_code=422, detail="项目尚无正文层，请先导入或生成正文")
    for c in split_import(v.y):
        if c["no"] == chapter:
            return c["body"]
    raise HTTPException(status_code=404, detail=f"正文中未找到第 {chapter} 章")


@app.post("/api/projects/{pid}/import/analyze_chapter")
def import_analyze_chapter(pid: str, body: ImportAnalyzeIn) -> dict:
    """拆一章（实体抽取 + 反向大纲）；已完成的步骤幂等复用，force 重拆。"""
    _require_project(pid)
    text = body.text.strip() if body.text and body.text.strip() \
        else _chapter_text_from_draft(pid, body.chapter)
    if body.force:
        _import_journal_for(pid).reset_chapter(body.chapter)
    try:
        result = analyze_chapter(
            _chore_provider(), _entities_for(pid), _prompt_store,
            _import_journal_for(pid), body.chapter, text, model=body.model,
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@app.get("/api/projects/{pid}/import/status")
def import_status(pid: str, page: int | None = None, page_size: int = 50) -> dict:
    """逐章拆解进度：正文切分 ∪ 已有收据。chapters 支持 page 分页。"""
    _require_project(pid)
    journal = _import_journal_for(pid)
    v = _project_store.current(pid, "draft")
    chapters: dict[int, dict] = {}
    if v is not None:
        for c in split_import(v.y):
            chapters[c["no"]] = {"no": c["no"], "title": c["title"], "chars": c["chars"]}
    for r in journal.receipts.values():
        chapters.setdefault(r.chapter, {"no": r.chapter, "title": "", "chars": 0})
    out = []
    for no in sorted(chapters):
        info = chapters[no]
        ent = journal.get(no, "entities")
        ol = journal.get(no, "outline")
        info.update({
            "entities_status": ent.status if ent else None,
            "outline_status": ol.status if ol else None,
            "outline_preview": (ol.result[:80] if ol and ol.status == "completed" else ""),
            "error": (ent.error if ent and ent.status == "failed" else "")
            or (ol.error if ol and ol.status == "failed" else ""),
        })
        out.append(info)
    done = sum(1 for c in out if c["entities_status"] == "completed"
               and c["outline_status"] == "completed")
    total = len(out)
    out, page_meta = _paginate(out, page, page_size)
    return {"project": pid, "chapters": out, "total": total, "done": done, **page_meta}


@app.post("/api/projects/{pid}/import/adopt_outline")
def import_adopt_outline(pid: str) -> dict:
    """把已拆解完成的章大纲汇总采纳为 outline 层新版本。"""
    _require_project(pid)
    outlines = _import_journal_for(pid).completed_outlines()
    if not outlines:
        raise HTTPException(status_code=422, detail="尚无已完成的章大纲，请先拆解")
    y = "\n\n".join(f"第{no}章\n{outline}" for no, outline in outlines)
    v = _project_store.adopt(pid, "outline", x_pairs=[], y=y,
                             note=f"导入拆解汇总（{len(outlines)} 章）")
    _sync_outline_meta(pid, y)
    return {"seq": v.seq, "chapters": len(outlines)}


class ImportToCorpusIn(BaseModel):
    title: str                        # 书名（语料标题与标签用）
    tags: list[str] = []              # 附加标签：类型/题材等
    file_path: str = ""               # 来源备注


@app.post("/api/projects/{pid}/import/to_corpus")
def import_to_corpus(pid: str, body: ImportToCorpusIn) -> dict:
    """把已拆解完成的章大纲存入范文语料库：每章一条 + 全书脉络一条。

    长篇大纲入语料后，运行台「融合再创作」可检索注入——借鉴他书的节奏
    结构与伏笔布局再创作。同一书名只能存一次（重复会被拒）。
    """
    _require_project(pid)
    outlines = _import_journal_for(pid).completed_outlines()
    if not outlines:
        raise HTTPException(status_code=422, detail="尚无已完成的章大纲，请先拆解")
    title = body.title.strip() or "未命名长篇"
    slug = "novel-" + hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
    if corpus_article_exists(slug):
        raise HTTPException(status_code=409, detail=f"《{title}》的大纲已在语料库中，请勿重复存入")
    tags = ["长篇", title, *body.tags]
    rows = [
        {
            "excerpt_id": f"{slug}-ch{no:04d}",
            "article_id": slug,
            "title": f"{title}·第{no}章",
            "file_path": body.file_path,
            "excerpt_type": "长篇章节大纲",
            "tags": tags,
            "text": f"第{no}章\n{outline}",
        }
        for no, outline in outlines
    ]
    rows.append({
        "excerpt_id": f"{slug}-arc",
        "article_id": slug,
        "title": f"{title}·全书脉络",
        "file_path": body.file_path,
        "excerpt_type": "长篇全书大纲",
        "tags": tags,
        "text": "\n\n".join(f"第{no}章 {outline}" for no, outline in outlines),
    })
    corpus_add_excerpts(rows)
    return {"added": len(rows), "chapters": len(outlines), "title": title}


class ChaishuImportIn(BaseModel):
    dir: str                          # 拆书 CLI 输出目录（拆书-书名/）
    title: str = ""                   # 书名（缺省从目录名「拆书-」之后推断）
    tags: list[str] = []              # 附加标签：题材/类型等
    source_txt: str = ""              # 原书 txt 路径（旧版底料无正文时按章号补齐）
    distill: bool = True              # 提炼写法卡：聚合统计 → 可迁移技法（LLM，失败不阻断导入）


_CHAISHU_EVENT_CN = {"shuangdian": "爽点", "foreshadow": "伏笔",
                     "upgrade": "升级", "other": "转折"}
_ARC_CHUNK = 100  # 全书脉络每块章数：装配器按预算注入，单块不能过大


def _load_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@app.post("/api/corpus/import_chaishu")
def corpus_import_chaishu(body: ChaishuImportIn) -> dict:
    """导入独立拆书 CLI（tools/chaishu）的产物目录到范文语料库。

    读取 chapters.jsonl / labels.jsonl / report.md，生成三类条目：
    - 长篇节奏分析 ×1：report.md 全文（聚合统计 + 诊断，无具体情节）
    - 长篇全书大纲 ×N：全部章标题骨架（未拆章仅标题），100 章一块
    - 长篇章节大纲 ×M：每个已标注章一条（概要 + 钩子 + 事件）
    同书重复导入 = 覆盖更新（先试点后全量的自然路径）。
    """
    d = Path(body.dir)
    if not d.is_dir():
        raise HTTPException(status_code=422, detail=f"目录不存在：{body.dir}")
    chapters = _load_jsonl(d / "chapters.jsonl")
    if not chapters:
        raise HTTPException(status_code=422,
                            detail="目录中没有 chapters.jsonl（或为空），请确认是拆书工具的输出目录")

    # 旧版底料只存元数据不存 body：给了原书 txt 就按章号切章补正文（剧情/正文层/实体抽取都靠它）
    has_body = any(str(c.get("body", "")).strip() for c in chapters)
    if not has_body:
        src = body.source_txt.strip()
        if not src:
            raise HTTPException(status_code=422, detail=(
                "旧版拆书底料不含正文（chapters.jsonl 无 body）：请填写原书 txt 路径补齐正文，"
                "或重跑 `python chaishu.py 原书.txt --skip-label` 重建底料后再导入"))
        sp = Path(src)
        if not sp.is_file():
            raise HTTPException(status_code=422, detail=f"原书 txt 不存在：{src}")
        try:
            raw = sp.read_bytes()
            text = ""
            for enc in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if not text:
                raise ValueError("解码失败")
        except Exception:
            raise HTTPException(status_code=422, detail=f"无法读取原书 txt：{src}")
        by_no = {c["no"]: c for c in split_import(text) if str(c.get("body", "")).strip()}
        merged = 0
        for c in chapters:
            src_ch = by_no.get(int(c.get("no", -1)))
            if src_ch is not None:
                c["body"] = src_ch["body"]
                merged += 1
        if merged == 0:
            raise HTTPException(status_code=422, detail="原书 txt 切章后无法与底料章号对齐，请检查是否同一本书")

    labels = {int(o["no"]): o for o in _load_jsonl(d / "labels.jsonl") if "no" in o}
    report_text = ""
    rp = d / "report.md"
    if rp.exists():
        report_text = rp.read_text(encoding="utf-8").strip()

    title = body.title.strip()
    if not title:
        title = d.name[3:] if d.name.startswith("拆书-") else d.name
    if not title:
        raise HTTPException(status_code=422, detail="无法从目录名推断书名，请填写")

    slug = "chaishu-" + hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
    tags = ["拆书", "长篇", title, *body.tags]
    rows: list[dict] = []
    if report_text:
        rows.append({
            "excerpt_id": f"{slug}-report", "article_id": slug,
            "title": f"{title}·节奏体检表", "file_path": str(d),
            "excerpt_type": "长篇节奏分析", "tags": tags, "text": report_text,
        })

    def ch_name(no: int) -> str:
        return f"第{no}章" if no else "前言"

    labeled_n = sum(1 for c in chapters if c.get("no") in labels)
    n_chunks = (len(chapters) + _ARC_CHUNK - 1) // _ARC_CHUNK
    for i in range(n_chunks):
        chunk = chapters[i * _ARC_CHUNK:(i + 1) * _ARC_CHUNK]
        lines = [f"《{title}》全书脉络（已拆 {labeled_n}/{len(chapters)} 章，"
                 f"本块 {ch_name(chunk[0].get('no', 0))}–{ch_name(chunk[-1].get('no', 0))}）", ""]
        vol = None
        for c in chunk:
            no = c.get("no", 0)
            if c.get("volume") != vol:
                vol = c.get("volume")
                lines.append(f"【{vol}】")
            lb = labels.get(no)
            if lb:
                lines.append(f"{ch_name(no)} {c.get('title', '')}：{lb.get('summary', '')}")
            else:
                lines.append(f"{ch_name(no)} {c.get('title', '')}")
        rows.append({
            "excerpt_id": f"{slug}-arc{i:03d}", "article_id": slug,
            "title": f"{title}·全书脉络 {i + 1}/{n_chunks}",
            "file_path": str(d), "excerpt_type": "长篇全书大纲",
            "tags": tags, "text": "\n".join(lines),
        })

    for c in chapters:
        no = c.get("no", 0)
        lb = labels.get(no)
        if not lb:
            continue
        evs = "；".join(
            f"{e.get('note', '')}（{_CHAISHU_EVENT_CN.get(e.get('type', ''), e.get('type', ''))}）"
            for e in lb.get("events", []))
        rows.append({
            "excerpt_id": f"{slug}-ch{no:04d}", "article_id": slug,
            "title": f"{title}·{ch_name(no)} {c.get('title', '')}",
            "file_path": str(d), "excerpt_type": "长篇章节大纲",
            "tags": tags,
            "text": (f"{ch_name(no)} {c.get('title', '')}（卷：{c.get('volume', '正文')}）\n"
                     f"概要：{lb.get('summary', '')}\n"
                     f"断章钩：{'有' if lb.get('hook') else '无'}｜"
                     f"升级：{'有' if lb.get('power_up') else '无'}\n"
                     f"事件：{evs or '无'}"),
        })

    if not rows:
        raise HTTPException(status_code=422, detail="目录中没有可导入的内容（无 report.md 且无已标注章）")
    n = corpus_replace_article(slug, rows)
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["excerpt_type"]] = by_type.get(r["excerpt_type"], 0) + 1

    # ---- 同步建参考项目：与自己的小说项目平行，互相可作范文 ----
    pid, ent_counts, has_entities = _ensure_reference_project(
        d, title, body, chapters, labels, labeled_n, report_text)

    # ---- ⑤拆书闭环：提炼可迁移写法卡入语料库（失败不阻断导入） ----
    distill_info: dict = {"status": "skipped"}
    if body.distill:
        try:
            cards = _distill_technique_cards(
                _chore_provider("distill"), _prompt_store, title, report_text, chapters, labels)
            if cards:
                card_rows = [{
                    "excerpt_id": f"{slug}-card{i:02d}",
                    "article_id": f"{slug}-cards",
                    "title": f"{title}·写法卡·{card['title']}",
                    "file_path": str(d),
                    "excerpt_type": "写法卡",
                    "tags": ["拆书", "写法卡", title, *body.tags],
                    "text": f"{card['title']}——{card['technique']}\n适用场景：{card['when']}",
                } for i, card in enumerate(cards)]
                corpus_replace_article(f"{slug}-cards", card_rows)
                distill_info = {"status": "ok", "cards": len(cards)}
            else:
                distill_info = {"status": "empty"}
        except Exception as e:
            distill_info = {"status": "failed", "error": str(e)[:200]}

    return {"added": n, "by_type": by_type, "title": title, "article_id": slug,
            "chapters_total": len(chapters), "labeled": labeled_n, "project_id": pid,
            "entities": ent_counts, "entities_preextracted": has_entities,
            "distill": distill_info}


def _ensure_reference_project(d: Path, title: str, body: ChaishuImportIn, chapters: list[dict],
                              labels: dict[int, dict], labeled_n: int,
                              report_text: str) -> str:
    """拆书产物项目化：复用同书名参考项目（重复导入=层版本更新）。
    五层全摆：intro=体检表、outline=全书脉络、plot=已标注章概要、draft=全书原文；
    实体库：entities.jsonl 直转角色/地点/物品，labels 伏笔事件直转（均零 LLM）。"""
    existing = _project_store.find_by_title(title, kind="reference")
    meta = existing if existing else _project_store.create(
        title=title, intro=(body.tags[0] if body.tags else "拆书参考项目"), kind="reference")

    def ch_name(no: int) -> str:
        return f"第{no}章" if no else "前言"

    # 简介层：体格概要 + 节奏体检表全文（聚合统计，无具体情节）
    intro_text = (f"《{title}》——拆书参考项目（仅借鉴节奏与结构，非本人创作）\n\n"
                  f"共 {len(chapters)} 章，已标注 {labeled_n} 章"
                  + (f"；附加标签：{'、'.join(body.tags)}" if body.tags else "")
                  + "\n\n" + report_text) if report_text else (
                  f"《{title}》——拆书参考项目（仅借鉴节奏与结构，非本人创作）\n\n"
                  f"共 {len(chapters)} 章，已标注 {labeled_n} 章。"
                  f"完整节奏体检表待全量拆解后重新导入生成。")
    _project_store.adopt(meta.id, "intro", [], intro_text,
                         note=f"拆书导入：{labeled_n}/{len(chapters)} 章已标注")

    # 大纲层：全书脉络（每章一行：章号+标题+概要），配合创作导航按章浏览
    lines: list[str] = [f"《{title}》全书脉络（已拆 {labeled_n}/{len(chapters)} 章）", ""]
    vol = None
    for c in chapters:
        no = c.get("no", 0)
        if c.get("volume") != vol:
            vol = c.get("volume")
            lines.append(f"【{vol}】")
        lb = labels.get(no)
        line = f"{ch_name(no)} {c.get('title', '')}"
        if lb and lb.get("summary"):
            line += f"：{lb['summary']}"
        lines.append(line)
    _project_store.adopt(meta.id, "outline", [], "\n".join(lines),
                         note=f"拆书导入：全书 {len(chapters)} 章脉络")

    # 剧情层：已标注章的概要（未标注章无概要，不硬凑）
    plot_lines = [f"《{title}》剧情概要（来自拆书标签，已标注 {labeled_n}/{len(chapters)} 章）", ""]
    vol = None
    for c in chapters:
        no = c.get("no", 0)
        if no not in labels:
            continue
        if c.get("volume") != vol:
            vol = c.get("volume")
            plot_lines.append(f"【{vol}】")
        lb = labels.get(no) or {}
        events = "；".join(
            f"{e.get('note', '')}（{_CHAISHU_EVENT_CN.get(e.get('type', ''), e.get('type', ''))}）"
            for e in lb.get("events", []))
        plot_lines.append(f"{ch_name(no)} {c.get('title', '')}：{lb.get('summary', '')}"
                          + (f"｜事件：{events}" if events else ""))
    _project_store.adopt(meta.id, "plot", [], "\n".join(plot_lines),
                         note=f"拆书导入：{labeled_n} 章剧情概要")

    # 正文层：全书原文（章号+标题+body），创作导航按章浏览不卡
    parts = [f"{ch_name(c.get('no', 0))} {c.get('title', '')}\n{c.get('body', '')}"
             for c in chapters if str(c.get("body", "")).strip()]
    total_chars = sum(len(str(c.get("body", ""))) for c in chapters)
    _project_store.adopt(meta.id, "draft", [], "\n\n".join(parts),
                         note=f"拆书导入：全书原文 {len(parts)} 章约 {total_chars // 10000} 万字")

    # 实体库：工具实体层（entities.jsonl）直转角色/地点/物品 + labels 伏笔直转。
    # 同名幂等：只补出场章，不覆盖既有内容；导入零 LLM 调用（提取已在拆书夜跑完成）。
    ent_counts = {"character": 0, "location": 0, "item": 0, "foreshadow": 0}
    store = _entities_for(meta.id)
    ent_rows = _load_jsonl(d / "entities.jsonl")
    ent_key_type = {"characters": "character", "locations": "location", "items": "item"}
    for row in ent_rows:
        try:
            no = int(row.get("no", 0))
        except (TypeError, ValueError):
            continue
        for key, etype in ent_key_type.items():
            for e in row.get(key) or []:
                if not isinstance(e, dict):
                    continue
                name = str(e.get("name", "")).strip()[:24]
                if not name:
                    continue
                existing_e = store.find(etype, name)
                if existing_e is None:
                    store.add(etype, name, summary=str(e.get("note", "")).strip()[:60],
                              details="拆书工具提取", chapter=no)
                    ent_counts[etype] += 1
                elif no not in existing_e.appearances:
                    store.record_appearance(existing_e.id, no)
    for no in sorted(labels):
        for e in (labels[no].get("events") or []):
            if e.get("type") != "foreshadow":
                continue
            note = str(e.get("note", "")).strip()
            if not note:
                continue
            name = note[:16]
            existing_e = store.find("foreshadow", name)
            if existing_e is None:
                store.add("foreshadow", name, summary=note,
                          details=f"拆书标签：第{no}章伏笔事件", chapter=no)
                ent_counts["foreshadow"] += 1
            elif no not in existing_e.appearances:
                store.record_appearance(existing_e.id, no)
    return meta.id, ent_counts, bool(ent_rows)


# ---- ⑤拆书闭环：拆解材料 → 可迁移写法卡（LLM 提炼，入语料库可检索注入） ----

DISTILL_SYSTEM = """你是网文写作技法研究员，任务是把一本书的拆解分析提炼成可跨书复用的写法卡。
只输出一个 JSON 数组，每个元素格式：{"title": "技法名（10字内）", "technique": "怎么用（60-120字，写给写作者的直接指令）",
"when": "什么时候用（一句话场景）"}。只提炼「换一本书还能用」的技法，严禁复述具体情节、人名、设定。
不要输出数组以外的任何文字。"""

_DISTILL_CAP_CHARS = 60_000   # 逐章概要字符上限：超出保留开篇全量 + 其余等距抽样
_DISTILL_KEEP_HEAD = 30      # 开篇结构对「开篇收束套路」最有参考价值，优先保留


def _distill_material(report_text: str, chapters: list[dict],
                      labels: dict[int, dict]) -> str:
    """提炼材料 = 节奏体检表（聚合统计）+ 逐章概要（钩子/事件标注）。"""
    parts: list[str] = []
    if report_text:
        parts.append(f"【节奏体检表】\n{report_text}")
    lines: list[str] = []
    for c in chapters:
        lb = labels.get(c.get("no", 0))
        if not lb:
            continue
        evs = "；".join(
            f"{e.get('note', '')}（{_CHAISHU_EVENT_CN.get(e.get('type', ''), e.get('type', ''))}）"
            for e in lb.get("events", []))
        line = f"第{c.get('no', 0)}章 {lb.get('summary', '')}"
        if lb.get("hook"):
            line += "｜断章钩"
        if evs:
            line += f"｜{evs}"
        lines.append(line)
    if lines:
        text = "\n".join(lines)
        head = "【逐章概要】"
        if len(text) > _DISTILL_CAP_CHARS:
            stride = max(2, int(len(text) / _DISTILL_CAP_CHARS) + 1)
            text = "\n".join(lines[:_DISTILL_KEEP_HEAD] + lines[_DISTILL_KEEP_HEAD::stride])
            head = f"【逐章概要】（章数多，开篇 {_DISTILL_KEEP_HEAD} 章保留全量，其余每 {stride} 章取 1 章）"
        parts.append(head + "\n" + text)
    return "\n\n".join(parts)


def _distill_technique_cards(provider, prompt_store, title: str, report_text: str,
                             chapters: list[dict], labels: dict[int, dict]) -> list[dict]:
    """LLM 从拆解材料提炼写法卡 [{title, technique, when}]；材料为空直接空手而归。"""
    material = _distill_material(report_text, chapters, labels)
    if not material:
        return []
    instruction = prompt_store.effective("distill")
    messages = [
        {"role": "system", "content": DISTILL_SYSTEM},
        {"role": "user", "content": f"{instruction}\n\n【拆书材料】\n《{title}》拆解如下：\n\n{material}"},
    ]

    def generate() -> str:
        return provider.chat(messages).text

    completion = complete_structured(generate)
    if not isinstance(completion.data, list):
        return []
    cards: list[dict] = []
    for item in completion.data:
        if not isinstance(item, dict):
            continue
        card = {k: str(item.get(k, "") or "").strip() for k in ("title", "technique", "when")}
        if card["title"] and card["technique"]:
            cards.append(card)
    return cards


# ---- M5 引导式写作：主路向导 + 今日章台 ----

_wizard_stores: dict[str, WizardStore] = {}
_desk_stores: dict[str, DeskStore] = {}


def _wizard_for(pid: str) -> WizardStore:
    if pid not in _wizard_stores:
        _wizard_stores[pid] = WizardStore(
            _config.REPO / "backend" / "data" / "projects" / pid / "wizard.json")
    return _wizard_stores[pid]


def _desk_for(pid: str) -> DeskStore:
    if pid not in _desk_stores:
        _desk_stores[pid] = DeskStore(
            _config.REPO / "backend" / "data" / "projects" / pid / "desk.json")
    return _desk_stores[pid]


def _journal_dir(pid: str) -> Path:
    return _config.REPO / "backend" / "data" / "projects" / pid / "runs"


def _desk_summary(pid: str) -> dict:
    """章台总览：章来源 = 大纲拆章 ∪ 章台记录 ∪ draft 正文拆章；卷头按 volumes store 真实数据分组。"""
    desk = _desk_for(pid)
    vs = _volumes_for(pid)
    vols = vs.all_list()
    v = _project_store.current(pid, "outline")
    o_map, _ol_volumes = parse_outline(v.y) if v else ({}, [])
    # 建立章号 → 卷显示名 的映射（按 chapter_kind/field_no 真实归属）
    vol_title_of: dict[int, str] = {}
    ml_vol = next((vv for vv in vols if getattr(vv, "volume_kind", None) == "mainline"), None)
    interludes = [vv for vv in vols if getattr(vv, "volume_kind", None) == "interlude"]
    dungeons = [vv for vv in vols if getattr(vv, "volume_kind", None) == "dungeon"]
    for no in desk.all_nos():
        w = desk.get(no)
        if not w:
            continue
        kind = getattr(w, "chapter_kind", None) or ""
        if kind == "mainline" and ml_vol is not None:
            vol_title_of[no] = f"第{ml_vol.volume_no}卷 {ml_vol.title or '蓝星开篇'}"
            continue
        if getattr(w, "is_interlude", False):
            # 找 field_no 匹配的 interlude；否则按 no 顺序归到最近一个未绑章的 interlude
            cand = next((iv for iv in interludes if iv.field_no == w.field_no), None)
            if cand is None:
                used = {n for nn, t in vol_title_of.items() for iv in interludes if t == f"第{iv.volume_no}卷 {iv.title}" for n in []}
                cand = interludes[0] if interludes else None
            if cand is not None:
                vol_title_of[no] = f"第{cand.volume_no}卷 {cand.title or '蓝星间章'}"
            continue
        # dungeon 章：field_no 指向对应 dungeon 卷
        fn = getattr(w, "field_no", None)
        cand = next((dv for dv in dungeons if dv.volume_no == fn or dv.field_no == fn), None)
        if cand is not None:
            vol_title_of[no] = f"第{cand.volume_no}卷 {cand.title or f'第{cand.volume_no}卷'}"
        elif dungeons:
            # fallback：归到最新 dungeon 卷
            cand = dungeons[-1]
            vol_title_of[no] = f"第{cand.volume_no}卷 {cand.title or f'第{cand.volume_no}卷'}"
        elif ml_vol is not None:
            vol_title_of[no] = f"第{ml_vol.volume_no}卷 {ml_vol.title or '蓝星开篇'}"
    # outline-only 章（只有大纲没进 desk）的旧逻辑保留，但优先用 desk 绑定
    for vol in vols:
        for no in (getattr(vol, "chapter_nos", None) or []):
            if no not in vol_title_of:
                vol_title_of[no] = f"第{vol.volume_no}卷 {vol.title or ''}".rstrip()
    draft_titles: dict[int, str] = {}
    v = _project_store.current(pid, "draft")
    if v is not None:
        for c in split_import(v.y):
            draft_titles[c["no"]] = c["title"]
    chapters = []
    for no in sorted(set(o_map) | set(draft_titles) | set(desk.all_nos())):
        w = desk.get(no)
        body = o_map.get(no, "")
        latest = (work_text(w, "polish") or work_text(w, "draft")) if w else ""
        ch_kind = getattr(w, "chapter_kind", None) if w else None
        is_interlude = bool(getattr(w, "is_interlude", False)) if w else False
        field_no = getattr(w, "field_no", None) if w else None
        # 计算 volume_no / volume 显示名
        vno: int | None = None
        if ch_kind == "mainline" and ml_vol is not None:
            vno = ml_vol.volume_no
        elif is_interlude:
            iv = next((iv for iv in interludes if iv.volume_no == field_no or iv.field_no == field_no), None)
            if iv is None:
                iv = next((iv for iv in interludes), None)
            if iv is not None:
                vno = iv.volume_no
        else:
            dv = next((dv for dv in dungeons if dv.volume_no == field_no or dv.field_no == field_no), None)
            if dv is None and field_no is not None:
                dv = next((dv for dv in dungeons if dv.start_chapter_no and no >= dv.start_chapter_no), None)
            if dv is not None:
                vno = dv.volume_no
            elif dungeons:
                vno = dungeons[-1].volume_no
            elif ml_vol is not None:
                vno = ml_vol.volume_no
        chapters.append({
            "no": no,
            "title": (w.title if w and w.title else "") or draft_titles.get(no, "")
            or chapter_title_of(body),
            "volume": vol_title_of.get(no, ""),
            "volume_no": vno,
            "chapter_kind": ch_kind or ("interlude" if is_interlude else "dungeon"),
            "field_no": field_no,
            "has_outline": no in o_map,
            "plot_done": bool(work_text(w, "plot")),
            "draft_done": bool(work_text(w, "draft")),
            "polish_done": bool(work_text(w, "polish")),
            "review_verdict": (w.review or {}).get("verdict") if w and w.review else None,
            "finalized_at": w.finalized_at if w else "",
            "payoff": w.payoff if w else "",
            "strands": list(w.strands) if w else [],
            "chars": len(latest),
            "outline_preview": body[:80],
        })
    vol_summaries = []
    for vv in vols:
        vch = [c["no"] for c in chapters if c["volume_no"] == vv.volume_no]
        vch.sort()
        vol_summaries.append({
            "title": f"第{vv.volume_no}卷 {vv.title or ''}".rstrip(),
            "note": "",
            "volume_no": vv.volume_no,
            "volume_kind": getattr(vv, "volume_kind", "dungeon"),
            "chapters": vch,
        })
    return {
        "project": pid, "chapters": chapters, "total": len(chapters),
        "finalized": sum(1 for c in chapters if c["finalized_at"]),
        "outline_chapters": len(o_map),
        "volumes": vol_summaries,
    }


def _wizard_summary(pid: str) -> dict:
    """项目卡进度摘要（列表页免 N+1 全量状态）。"""
    try:
        state = _wizard_for(pid).stations_state(pid, _project_store, _desk_for(pid))
        desk = _desk_summary(pid)
        return {
            "done": sum(1 for s in state["stations"] if s["done"]),
            "graduated": state["graduated"],
            "current_station": state["current_station"],
            "chapters_total": desk["total"],
            "chapters_finalized": desk["finalized"],
        }
    except Exception:
        return {"done": 0, "graduated": False, "current_station": None,
                "chapters_total": 0, "chapters_finalized": 0}


class WizardUpdateIn(BaseModel):
    idea: dict[str, str] | None = None
    seed: str | None = None
    direction: str | None = None
    direction_source: str | None = None
    n_chapters: int | None = None


@app.get("/api/projects/{pid}/wizard")
def wizard_detail(pid: str) -> dict:
    _require_project(pid)
    store = _wizard_for(pid)
    return {"project": pid, "wizard": store.get().as_dict(),
            **store.stations_state(pid, _project_store, _desk_for(pid))}


@app.put("/api/projects/{pid}/wizard")
def wizard_update(pid: str, body: WizardUpdateIn) -> dict:
    _require_project(pid)
    if body.direction is not None and not body.direction.strip():
        raise HTTPException(status_code=422, detail="方向不能为空（不想用方向卡请跳过该站）")
    try:
        d = _wizard_for(pid).update(idea=body.idea, seed=body.seed, direction=body.direction,
                                    direction_source=body.direction_source,
                                    n_chapters=body.n_chapters)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"project": pid, "wizard": d.as_dict()}


@app.post("/api/projects/{pid}/wizard/brainstorm")
def wizard_brainstorm(pid: str) -> dict:
    """生成 5 张方向卡（chore 通道）：先问后给，采纳仍需作者签收。"""
    _require_project(pid)
    d = _wizard_for(pid).get()
    try:
        result = generate_direction_cards(_chore_provider(), _prompt_store, d.seed,
                                          idea_context=d.idea.get("context", ""))
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"cards": result.cards, "attempts": result.attempts}


@app.post("/api/projects/{pid}/wizard/{station}/skip")
def wizard_skip(pid: str, station: str) -> dict:
    _require_project(pid)
    if station not in STATIONS:
        raise HTTPException(status_code=404, detail=f"未知站：{station}")
    _wizard_for(pid).toggle_skip(station)
    store = _wizard_for(pid)
    return {"project": pid, "wizard": store.get().as_dict(),
            **store.stations_state(pid, _project_store, _desk_for(pid))}


class DeskRunIn(BaseModel):
    stage: Literal["plot", "draft", "polish"]
    directive: str | None = None
    force: bool = False
    materials: list[MaterialIn] | None = None   # 跨项目参考层（互为范文）
    checklist: bool = True                      # 开章任务书（五段结构）
    modules: list[str] | None = None            # 写作模块：显式点名（注入完整规则）
    full_modules: bool = False                  # 未点名时 True=默认集完整规则（默认摘要版）


class DeskTextIn(BaseModel):
    text: str


class DeskFinalizeIn(BaseModel):
    auto_entities: bool = True
    payoff: Literal["none", "small", "medium", "climax"] | None = None   # 定稿时顺手点选兑现规模


class DeskPayoffIn(BaseModel):
    payoff: Literal["none", "small", "medium", "climax"]


@app.get("/api/projects/{pid}/desk")
def desk_detail(pid: str, page: int | None = None, page_size: int = 50) -> dict:
    _require_project(pid)
    summary = _desk_summary(pid)
    chapters, page_meta = _paginate(summary["chapters"], page, page_size)
    summary["chapters"] = chapters
    summary.update(page_meta)
    return summary


@app.get("/api/projects/{pid}/desk/{chapter:int}")
def desk_chapter(pid: str, chapter: int) -> dict:
    """单章工作区：各阶段全文 + 本章大纲。"""
    _require_project(pid)
    work = _desk_for(pid).get(chapter)
    return {
        "project": pid,
        "chapter": work.as_dict() if work else {"no": chapter, "title": "", "plot": {},
                                                "draft": {}, "polish": {}, "review": None,
                                                "finalized_at": "", "payoff": "",
                                                "strands": []},
        "outline": outline_map(_project_store, pid).get(chapter, ""),
    }


@app.put("/api/projects/{pid}/desk/{chapter}/payoff")
def desk_set_payoff(pid: str, chapter: int, body: DeskPayoffIn) -> dict:
    """记录/修改本章兑现规模四选一（未定稿可先记，定稿后可改——节奏数据自家生长）。
    注意：必须先于 PUT /{stage} 泛型路由注册，否则 "payoff" 会被当作 stage 吃掉。"""
    _require_project(pid)
    try:
        work = _desk_for(pid).record_payoff(chapter, body.payoff)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"chapter": chapter, "payoff": work.payoff,
            "payoff_label": PAYOFF_LABELS.get(work.payoff, "")}


class DeskStrandsIn(BaseModel):
    strands: list[Literal["quest", "fire", "bond"]] = []


@app.put("/api/projects/{pid}/desk/{chapter}/strands")
def desk_set_strands(pid: str, chapter: int, body: DeskStrandsIn) -> dict:
    """记录/修改本章推进的三线标签（主线/冲突/羁绊多选，可清空）。
    注意：必须先于 PUT /{stage} 泛型路由注册，否则 "strands" 会被当作 stage 吃掉。"""
    _require_project(pid)
    try:
        work = _desk_for(pid).record_strands(chapter, body.strands)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"chapter": chapter, "strands": work.strands,
            "strand_labels": [STRAND_LABELS.get(s, s) for s in work.strands]}


@app.put("/api/projects/{pid}/desk/{chapter}/{stage}")
def desk_save_text(pid: str, chapter: int, stage: str, body: DeskTextIn) -> dict:
    """作者编辑保存（三出口之「自己写/改」一级公民）：文本直存章台。"""
    _require_project(pid)
    try:
        work = _desk_for(pid).record(chapter, stage, body.text, run_id="manual")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"chapter": chapter, "stage": stage, "chars": len(body.text),
            "finalized_at": work.finalized_at}


@app.post("/api/projects/{pid}/desk/{chapter}/run")
def desk_run(pid: str, chapter: int, body: DeskRunIn) -> dict:
    """单章单层生成：plot/draft/polish；同输入幂等回收据，force 重生成。
    materials：跨项目参考层（REFERENCE 族），与其他项目互为范文。"""
    _require_project(pid)
    try:
        return run_chapter_stage(
            _router_provider(body.stage), _prompt_store, _project_store, pid,
            _markers_for(pid), _wizard_for(pid), _desk_for(pid),
            chapter, body.stage, directive=body.directive, force=body.force,
            journal_dir=_journal_dir(pid),
            extra_materials=_desk_extra_materials(body),
            entity_store=_entities_for(pid) if body.checklist else None,
            modules=body.modules, full_modules=body.full_modules,
            book_store=_book_for(pid), field_store=_fields_for(pid),
            volume_store=_volume_for_chapter(pid, chapter))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成失败（已完成块已留收据）：{e}")


def _desk_extra_materials(body: DeskRunIn) -> list[Material] | None:
    if not body.materials:
        return None
    return [Material(source_id=m.source_id, family=_FAMILIES[m.family],
                     content=m.content, title=m.title) for m in body.materials]


@app.post("/api/projects/{pid}/desk/{chapter}/run/stream")
def desk_run_stream(pid: str, chapter: int, body: DeskRunIn) -> StreamingResponse:
    """章台流式生成：thinking/delta 实时透出，结束时发 result（与同步版同构）。"""
    _require_project(pid)

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json_dumps(data)}\n\n"

    def gen() -> Iterator[str]:
        q: Queue = Queue()
        END = object()

        def work():
            try:
                def on_event(kind, payload):
                    q.put((kind, payload))
                result = run_chapter_stage(
                    _router_provider(body.stage), _prompt_store, _project_store, pid,
                    _markers_for(pid), _wizard_for(pid), _desk_for(pid),
                    chapter, body.stage, directive=body.directive, force=body.force,
                    journal_dir=_journal_dir(pid), extra_materials=_desk_extra_materials(body),
                    entity_store=_entities_for(pid) if body.checklist else None,
                    on_event=on_event,
                    modules=body.modules, full_modules=body.full_modules,
                    book_store=_book_for(pid), field_store=_fields_for(pid),
                    volume_store=_volume_for_chapter(pid, chapter))
                q.put(("result", result))
            except ValueError as e:
                q.put(("fail", {"detail": str(e)}))
            except Exception as e:
                q.put(("fail", {"detail": f"生成失败（已完成块已留收据）：{e}"}))
            finally:
                q.put((END, None))

        threading.Thread(target=work, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind is END:
                break
            yield sse(kind, payload)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/projects/{pid}/desk/{chapter}/review")
def desk_review(pid: str, chapter: int) -> dict:
    """单章九项一致性审稿（第九项克苏鲁白描空泛套话锁，第七项跨章联结对照上一章结尾），结果入章台记录；
    附节奏饥饿与三线断档/占比检查（纯数据，不走 LLM）。"""
    _require_project(pid)
    desk = _desk_for(pid)
    prose = work_text(desk.get(chapter), "polish") or work_text(desk.get(chapter), "draft")
    if not prose:
        raise HTTPException(status_code=422, detail="本章还没有正文，请先生成正文")
    outline = outline_map(_project_store, pid).get(chapter, "")
    try:
        review = review_chapter(_router_provider("review"), _prompt_store, outline, prose,
                                prev_tail=previous_tail(desk, chapter))
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    record = review.as_dict()
    hunger = rhythm_hunger(desk, chapter)
    if hunger:
        record["rhythm_warning"] = hunger
    s_warns = strand_warnings(desk, chapter)
    if s_warns:
        record["strand_warnings"] = s_warns
    b_warns = book_review_checks(desk, chapter,
                                book_store=_book_for(pid), field_store=_fields_for(pid))
    if b_warns:
        record["book_warnings"] = b_warns
    desk.record_review(chapter, record)
    return {"chapter": chapter, **record}


@app.post("/api/projects/{pid}/desk/{chapter}/fix/stream")
def desk_fix_stream(pid: str, chapter: int) -> StreamingResponse:
    """审稿定点修复：取章台最近一次审稿的不稳项，只修问题点不改其余——
    输出完整全文写回来源阶段（润色优先于正文），审稿记录标注已修复（不重审，
    省一轮全章审查；要复审可再点「运行审稿」）。SSE 实时透出 thinking/delta。"""
    _require_project(pid)
    desk = _desk_for(pid)
    work = desk.get(chapter)
    review = (work.review or {}) if work else {}
    problems = [it for it in (review.get("items") or [])
                if isinstance(it, dict) and it.get("status") == "不稳"]
    if not problems:
        raise HTTPException(status_code=422, detail="没有可修复的审稿问题——先运行审稿")
    prose = work_text(work, "polish") or work_text(work, "draft")
    if not prose:
        raise HTTPException(status_code=422, detail="本章没有可修复的正文")
    stage = "polish" if work_text(work, "polish") else "draft"
    problem_lines = "\n".join(
        f"{i}. {it.get('name', '')}：{it.get('problem', '') or '未说明问题'}"
        for i, it in enumerate(problems, 1))

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json_dumps(data)}\n\n"

    def gen() -> Iterator[str]:
        q: Queue = Queue()
        END = object()

        def work_thread():
            parts: list[str] = []
            try:
                instruction = _prompt_store.effective("fix")
                user = (f"{instruction}\n\n【原文】\n{prose}\n\n【问题清单】\n{problem_lines}\n"
                        f"{OUTPUT_CONTRACT}")
                messages = [
                    {"role": "system", "content": SYSTEM_CONTRACT},
                    {"role": "user", "content": user},
                ]
                out_budget = min(131_072, max(8_192,
                                              int(len(prose) * PROSE_TOKENS_PER_CHAR) + 4_096))
                for ev in get_router().chat_stream("refine", messages, max_tokens=out_budget):
                    if ev.kind == "delta":
                        parts.append(ev.text)
                        q.put(("delta", {"text": ev.text}))
                    elif ev.kind == "thinking":
                        q.put(("thinking", {"text": ev.text}))
                    elif ev.kind == "error":
                        q.put(("notice", {"text": ev.text}))
                y = "".join(parts).strip()
                if not y:
                    q.put(("fail", {"detail": "模型输出为空：思考可能耗尽了 token 预算，请重试"}))
                else:
                    desk.record(chapter, stage, y, run_id="review-fix")
                    updated = dict(review)
                    updated["fix_applied"] = True
                    updated["fix_summary"] = f"已按 {len(problems)} 条问题定点修复（未复审）"
                    desk.record_review(chapter, updated)
                    q.put(("result", {"y": y, "stage": stage, "fixed": len(problems)}))
            except Exception as e:
                q.put(("fail", {"detail": f"定点修复失败：{e}"}))
            finally:
                q.put((END, None))

        threading.Thread(target=work_thread, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind is END:
                break
            yield sse(kind, payload)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/projects/{pid}/desk/{chapter}/finalize")
def desk_finalize(pid: str, chapter: int, body: DeskFinalizeIn | None = None) -> dict:
    """定稿 + 保鲜飞轮：实体抽取、标记核验、整本合成采纳 draft 版本。"""
    _require_project(pid)
    auto = body.auto_entities if body else True
    payoff = body.payoff if body else None
    try:
        return finalize_chapter(
            _chore_provider(), _prompt_store, _project_store, pid,
            _entities_for(pid), _markers_for(pid), _desk_for(pid),
            chapter, auto_entities=auto, payoff=payoff)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/projects/{pid}/desk/{chapter}/unfinalize")
def desk_unfinalize(pid: str, chapter: int) -> dict:
    """反定稿：清除 finalized_at，保留正文/润色/审稿结果，允许作者改完再定稿。"""
    _require_project(pid)
    try:
        work = _desk_for(pid).unfinalize(chapter)
        return {"chapter": chapter, "finalized_at": work.finalized_at, "ok": True}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ============================================================
# 书级硬卡（book/）CRUD
# ============================================================

@app.get("/api/projects/{pid}/book")
def book_get_all(pid: str) -> dict:
    """返回 7 张书卡完整内容 + 渲染后的 Markdown 预览。"""
    _require_project(pid)
    bs = _book_for(pid)
    return {
        "cards": {c: bs._read(c) for c in bs.CARDS},
        "rendered": bs.render_all(),
    }


@app.get("/api/projects/{pid}/book/render")
def book_render(pid: str) -> dict:
    """单独返回渲染后的 Markdown（任务书里注入的那段），便于前端预览。"""
    _require_project(pid)
    return {"rendered": _book_for(pid).render_all()}


@app.get("/api/projects/{pid}/book/{card}")
def book_get_card(pid: str, card: str) -> dict:
    _require_project(pid)
    bs = _book_for(pid)
    if card not in bs.CARDS:
        raise HTTPException(status_code=404, detail=f"未知书卡：{card}")
    return {"card": card, "data": bs._read(card)}


class BookCardUpdateIn(BaseModel):
    data: dict


@app.put("/api/projects/{pid}/book/{card}")
def book_update_card(pid: str, card: str, body: BookCardUpdateIn) -> dict:
    _require_project(pid)
    bs = _book_for(pid)
    if card not in bs.CARDS:
        raise HTTPException(status_code=404, detail=f"未知书卡：{card}")
    try:
        bs.update(card, body.data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"card": card, "data": bs._read(card), "ok": True}


# ============================================================
# 场（fields/）CRUD
# ============================================================

@app.get("/api/projects/{pid}/fields")
def field_list(pid: str) -> dict:
    """列出所有场（索引级信息）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    return {"fields": fs.list_all()}


@app.post("/api/projects/{pid}/fields")
def field_create(pid: str, body: dict | None = None) -> dict:
    """新建一场（自动分配场号，初始化 Day1-7）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    try:
        fw = fs.create(
            title=(body or {}).get("title", ""),
            volume_no=int((body or {}).get("volume_no", 1) or 1),
            world_type=(body or {}).get("world_type", "克苏鲁小镇"),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"field": fw.as_dict(), "ok": True}


@app.get("/api/projects/{pid}/fields/{no}")
def field_get(pid: str, no: int) -> dict:
    _require_project(pid)
    fs = _fields_for(pid)
    fw = fs.get(no)
    if fw is None:
        raise HTTPException(status_code=404, detail=f"场不存在：{no}")
    return {"field": fw.as_dict()}


class FieldUpdateIn(BaseModel):
    title: str | None = None
    volume_no: int | None = None
    world_type: str | None = None
    world_premise: str | None = None
    obituary: dict | None = None
    drama_status: str | None = None
    interlude_status: str | None = None


@app.put("/api/projects/{pid}/fields/{no}")
def field_update(pid: str, no: int, body: FieldUpdateIn) -> dict:
    _require_project(pid)
    fs = _fields_for(pid)
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        fw = fs.update(no, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"field": fw.as_dict(), "ok": True}


class DayUpdateIn(BaseModel):
    summary: str | None = None
    required_hook: str | None = None


@app.put("/api/projects/{pid}/fields/{no}/days/{day}")
def field_update_day(pid: str, no: int, day: int, body: DayUpdateIn) -> dict:
    """更新单日（Day1-7）的 summary / required_hook。"""
    _require_project(pid)
    if not (1 <= day <= 7):
        raise HTTPException(status_code=422, detail="日号必须在 1-7 之间")
    fs = _fields_for(pid)
    try:
        dw = fs.ensure_day(no, day, summary=body.summary or "",
                            required_hook=body.required_hook or "")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    fw = fs.get(no)
    return {"field": fw.as_dict() if fw else None, "day": dw.as_dict(), "ok": True}


class ChapterAssignIn(BaseModel):
    chapters: list[int]


@app.put("/api/projects/{pid}/fields/{no}/days/{day}/chapters")
def field_assign_day_chapters(pid: str, no: int, day: int, body: ChapterAssignIn) -> dict:
    """把若干章分配到指定 Day（会从其他 Day/幕间中移除）。"""
    _require_project(pid)
    if not (1 <= day <= 7):
        raise HTTPException(status_code=422, detail="日号必须在 1-7 之间")
    fs = _fields_for(pid)
    try:
        fw = fs.assign_chapters_to_day(no, day, body.chapters or [])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 同步 DeskStore
    desk = _desk_for(pid)
    for c in body.chapters or []:
        w = desk.get(c)
        if w is not None:
            desk.assign_field(c, field_no=no, day=day, is_interlude=False)
    return {"field": fw.as_dict(), "ok": True}


@app.put("/api/projects/{pid}/fields/{no}/interlude/chapters")
def field_assign_interlude_chapters(pid: str, no: int, body: ChapterAssignIn) -> dict:
    """把若干章标记为该场幕间。"""
    _require_project(pid)
    fs = _fields_for(pid)
    try:
        fw = fs.assign_interlude_chapters(no, body.chapters or [])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    desk = _desk_for(pid)
    for c in body.chapters or []:
        w = desk.get(c)
        if w is not None:
            desk.assign_field(c, field_no=no, day=None, is_interlude=True)
    return {"field": fw.as_dict(), "ok": True}


@app.post("/api/projects/{pid}/fields/{no}/finalize")
def field_finalize(pid: str, no: int, body: dict | None = None) -> dict:
    """定稿一场的戏里部分（drama）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    try:
        fw = fs.mark_finalized(no)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"field": fw.as_dict(), "ok": True}


@app.post("/api/projects/{pid}/fields/{no}/finalize_interlude")
def field_finalize_interlude(pid: str, no: int) -> dict:
    """定稿一场的幕间部分。"""
    _require_project(pid)
    fs = _fields_for(pid)
    try:
        fw = fs.mark_interlude_finalized(no)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"field": fw.as_dict(), "ok": True}


class VariantsAddIn(BaseModel):
    node_type: str
    variants: list[dict]


@app.post("/api/projects/{pid}/fields/{no}/variants")
def field_add_variants(pid: str, no: int, body: VariantsAddIn) -> dict:
    """追加候选池（adapt/coc/tang_outline/interlude_outline 等步骤输出）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    try:
        fw = fs.add_variants(no, body.node_type, body.variants or [])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"field": fw.as_dict(), "ok": True}


class VariantChooseIn(BaseModel):
    node_type: str
    vid: str


@app.post("/api/projects/{pid}/fields/{no}/variants/choose")
def field_choose_variant(pid: str, no: int, body: VariantChooseIn) -> dict:
    """选定某个候选（chosen=True，其他 False）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    try:
        fw = fs.choose_variant(no, body.node_type, body.vid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"field": fw.as_dict(), "chosen_text": fs.chosen_variant_text(no, body.node_type),
            "ok": True}


# ============================================================
# 场级流水线（adapt → COC → tang大纲 → 分章 → 沉淀 → 间章）
# ============================================================

@app.get("/api/projects/{pid}/fields/{no}/pipeline")
def field_pipeline_status(pid: str, no: int) -> dict:
    """列出本场所有节点状态。"""
    _require_project(pid)
    fs = _fields_for(pid)
    if fs.get(no) is None:
        raise HTTPException(status_code=404, detail=f"场不存在：{no}")
    rs = _field_run_for(pid)
    return {
        "field_no": no,
        "drama_status": fs.get(no).drama_status,
        "interlude_status": fs.get(no).interlude_status,
        "nodes": rs.list_artifacts(no),
        "node_order": [n[0] for n in FIELD_PIPELINE_NODES],
    }


@app.get("/api/projects/{pid}/fields/{no}/pipeline/{node_id}")
def field_pipeline_node_get(pid: str, no: int, node_id: str) -> dict:
    """获取单个节点产物（含 raw_text / variants / chosen）。"""
    _require_project(pid)
    rs = _field_run_for(pid)
    art = rs.get(no, node_id)
    if art is None:
        raise HTTPException(status_code=404, detail=f"节点未执行：{node_id}")
    return {"artifact": art.as_dict()}


class FieldNodeRunIn(BaseModel):
    user_directive: str | None = None
    force: bool = False
    n_variants: int | None = None


@app.post("/api/projects/{pid}/fields/{no}/pipeline/{node_id}/run")
def field_pipeline_node_run(pid: str, no: int, node_id: str, body: FieldNodeRunIn | None = None) -> dict:
    """运行（或重跑）指定节点。"""
    _require_project(pid)
    fs = _fields_for(pid)
    if fs.get(no) is None:
        raise HTTPException(status_code=404, detail=f"场不存在：{no}")
    rs = _field_run_for(pid)
    coc_s = _coc_run_for(pid)
    runner = PipelineRunner(_chore_provider(), prompt_store=_prompt_store)
    try:
        art = run_field_node(
            runner, pid, no, node_id,
            book_store=_book_for(pid), field_store=fs, run_store=rs,
            desk=_desk_for(pid), coc_store=coc_s,
            user_directive=body.user_directive if body else None,
            force=body.force if body else False,
            n_variants=body.n_variants if body else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])
    return {"artifact": art.as_dict(), "ok": True}


@app.post("/api/projects/{pid}/fields/{no}/pipeline/{node_id}/run/stream")
def field_pipeline_node_run_stream(pid: str, no: int, node_id: str, body: FieldNodeRunIn | None = None) -> StreamingResponse:
    """流式运行节点（SSE）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    if fs.get(no) is None:
        raise HTTPException(status_code=404, detail=f"场不存在：{no}")
    from fastapi.responses import StreamingResponse as _SR

    def gen():
        runner = PipelineRunner(_chore_provider(), prompt_store=_prompt_store)
        rs = _field_run_for(pid)
        cs = _coc_run_for(pid)

        def on_event(kind: str, payload: dict):
            try:
                evt = json.dumps({"kind": kind, **payload}, ensure_ascii=False)
            except Exception:
                evt = json.dumps({"kind": kind}, ensure_ascii=False)
            yield f"data: {evt}\n\n"

        # 直接用支持 on_event 的 run_field_node —— run_layer 会回调 delta/thinking/block_done
        q: Queue = Queue()

        def bridge(k, p):
            q.put((k, p))
        try:
            t = threading.Thread(
                target=lambda: (
                    run_field_node(
                        runner, pid, no, node_id,
                        book_store=_book_for(pid), field_store=fs, run_store=rs,
                        desk=_desk_for(pid), coc_store=cs,
                        user_directive=body.user_directive if body else None,
                        force=body.force if body else False,
                        n_variants=body.n_variants if body else None,
                        on_event=bridge,
                    ),
                    q.put(("__end__", None)),
                ),
                daemon=True,
            )
            t.start()
            while True:
                k, p = q.get()
                if k == "__end__":
                    break
                try:
                    evt = json.dumps({"kind": k, **(p or {})}, ensure_ascii=False)
                except Exception:
                    evt = json.dumps({"kind": k}, ensure_ascii=False)
                yield f"data: {evt}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'kind':'error','detail':str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'kind':'error','detail':str(e)[:500]}, ensure_ascii=False)}\n\n"

    return _SR(gen(), media_type="text/event-stream")


class FieldNodeChooseIn(BaseModel):
    vid: str


@app.post("/api/projects/{pid}/fields/{no}/pipeline/{node_id}/choose")
def field_pipeline_node_choose(pid: str, no: int, node_id: str, body: FieldNodeChooseIn) -> dict:
    """在 variant-pool 节点里选定一个候选（自动写入下游依赖）。"""
    _require_project(pid)
    rs = _field_run_for(pid)
    try:
        art = rs.choose_variant(no, node_id, body.vid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"artifact": art.as_dict(), "ok": True}


class FieldNodeEditIn(BaseModel):
    raw_text: str | None = None
    parsed: dict | list | None = None
    variants: list[dict] | None = None
    chosen_vid: str | None = None


@app.put("/api/projects/{pid}/fields/{no}/pipeline/{node_id}")
def field_pipeline_node_edit(pid: str, no: int, node_id: str, body: FieldNodeEditIn) -> dict:
    """人工编辑节点产物（改分章/改大纲/改残页文字）。"""
    _require_project(pid)
    rs = _field_run_for(pid)
    art = rs.get(no, node_id)
    if art is None:
        from ..fields import NodeArtifact
        node_def = next((d for d in FIELD_PIPELINE_NODES if d[0] == node_id), None)
        if node_def is None:
            raise HTTPException(status_code=404, detail=f"未知节点：{node_id}")
        art = NodeArtifact(node_id=node_id, layer=node_def[1])
    if body.raw_text is not None:
        art.raw_text = body.raw_text
    if body.parsed is not None:
        art.parsed = body.parsed
    if body.variants is not None:
        art.variants = body.variants
    if body.chosen_vid is not None:
        art.chosen_vid = body.chosen_vid
    rs.save(no, art)
    return {"artifact": art.as_dict(), "ok": True}


class SplitApplyIn(BaseModel):
    start_chapter: int
    override: dict | None = None  # 允许传编辑后的 chapters JSON


@app.post("/api/projects/{pid}/fields/{no}/pipeline/split_chapters/apply")
def field_pipeline_apply_split(pid: str, no: int, body: SplitApplyIn) -> dict:
    """把 split_chapters 的结果应用到 FieldStore + DeskStore（分章定锤）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    rs = _field_run_for(pid)
    try:
        r = apply_split_to_field(fs, rs, _desk_for(pid), no,
                                  start_chapter=body.start_chapter,
                                  override=body.override)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 场进入 writing 状态
    fs.update(no, drama_status="writing")
    r["field"] = fs.get(no).as_dict()
    return {**r, "ok": True}


@app.post("/api/projects/{pid}/fields/{no}/pipeline/epilogue_ash/apply")
def field_pipeline_apply_epilogue(pid: str, no: int, body: dict | None = None) -> dict:
    """把 epilogue_ash 的残页+杀青一句话写入 BookStore。"""
    _require_project(pid)
    rs = _field_run_for(pid)
    bs = _book_for(pid)
    try:
        r = apply_epilogue_to_book(bs, rs, no, override=(body or {}).get("override"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {**r, "ok": True}


@app.post("/api/projects/{pid}/fields/{no}/pipeline/distill_obsession/apply")
def field_pipeline_apply_obsession(pid: str, no: int, body: dict | None = None) -> dict:
    """把执念代价/侵蚀度/crafts 更新写入 BookStore。"""
    _require_project(pid)
    rs = _field_run_for(pid)
    bs = _book_for(pid)
    try:
        r = apply_obsession_to_book(bs, rs, no, override=(body or {}).get("override"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {**r, "ok": True}


@app.post("/api/projects/{pid}/fields/{no}/pipeline/distill_world/apply")
def field_pipeline_apply_world(pid: str, no: int) -> dict:
    """把世界规则卡（可选）挂到实体库。"""
    _require_project(pid)
    rs = _field_run_for(pid)
    r = apply_world_rules(_entities_for(pid), rs, no)
    return {**r, "ok": True}


@app.post("/api/projects/{pid}/fields/{no}/pipeline/interlude_outline/apply")
def field_pipeline_apply_interlude(pid: str, no: int, body: SplitApplyIn | None = None) -> dict:
    """间章大纲定锤：在 DeskStore 创建幕间章节（接到 split 的场章后）。"""
    _require_project(pid)
    fs = _fields_for(pid)
    rs = _field_run_for(pid)
    fw = fs.get(no)
    if fw is None:
        raise HTTPException(status_code=404, detail=f"场不存在：{no}")
    chosen = rs.chosen_parsed(no, "interlude_outline")
    if not chosen:
        # fallback：用 override
        chosen = (body.override if body else None)
    if not chosen:
        raise HTTPException(status_code=422, detail="interlude_outline 未选定或解析失败")
    # 起章号：当前 DeskStore 最大章号 +1
    desk = _desk_for(pid)
    existing = desk.all_nos()
    start = (max(existing) + 1) if existing else 1
    if body is not None and body.start_chapter:
        start = body.start_chapter
    chs: list[int] = []
    # 简易：每个候选就是一章（作者可后续切分）
    # 如果 parsed 带 chapters 数组就用它
    if isinstance(chosen, dict) and isinstance(chosen.get("chapters"), list):
        chapters_in = chosen["chapters"]
    else:
        chapters_in = [{"title": (chosen.get("title") if isinstance(chosen, dict) else f"第{no}场间章"),
                        "summary": (chosen.get("structure") if isinstance(chosen, dict) else chosen) or ""}]
    for ch in chapters_in:
        w = desk.ensure(start, title=str(ch.get("title", "") or f"第{no}场间章"))
        desk.assign_field(start, field_no=no, day=None, is_interlude=True)
        chs.append(start)
        start += 1
    fs.assign_interlude_chapters(no, chs)
    fs.update(no, interlude_status="writing")
    return {"interlude_chapters": chs, "field": fs.get(no).as_dict(), "ok": True}


# ============================================================
# COC 多轮跑团仿真（Phase 3）
# ============================================================

def _coc_runner() -> PipelineRunner:
    return PipelineRunner(_chore_provider(), prompt_store=_prompt_store)

# Alias used in endpoints below (matches style of existing endpoints)
_runner = _coc_runner


class CocStartIn(BaseModel):
    force_reset: bool = False
    san: int | None = None
    hp: int | None = None
    max_turns: int = 40
    player_card_patch: dict | None = None


class CocAutorunIn(BaseModel):
    max_steps: int | None = 8
    user_directive: str | None = None


class CocInterveneIn(BaseModel):
    text: str
    as_role: str = "author"          # author | investigator
    user_directive: str | None = None


class CocPatchIn(BaseModel):
    san: int | None = None
    hp: int | None = None
    mp: int | None = None
    current_day: int | None = None
    obsession_uses: int | None = None
    npcs: dict | None = None
    clue: str | None = None
    cost_note: str | None = None


def _require_coc_ready(pid: str, no: int):
    """校验场存在 + adapt_scenario 已选定，返回 (fs, rs, coc_store, adapt_parsed)。"""
    _require_project(pid)
    fs = _fields_for(pid)
    rs = _field_run_for(pid)
    coc_store = _coc_run_for(pid)
    fw = fs.get(no)
    if fw is None:
        raise HTTPException(status_code=404, detail=f"场不存在：{no}")
    adapt = rs.chosen_parsed(no, "adapt_scenario")
    if not adapt:
        raise HTTPException(status_code=422, detail="请先选定 adapt_scenario 候选再启动跑团")
    return fs, rs, coc_store, adapt


@app.get("/api/projects/{pid}/fields/{no}/coc")
def coc_get(pid: str, no: int) -> dict:
    """获取本场 COC 跑团当前状态（若不存在返回 null）。"""
    _require_project(pid)
    coc_store = _coc_run_for(pid)
    state = coc_store.get(no)
    if state is None:
        return {"exists": False, "state": None}
    return {"exists": True, "state": state.as_dict()}


@app.post("/api/projects/{pid}/fields/{no}/coc/start")
def coc_start(pid: str, no: int, body: CocStartIn | None = None) -> dict:
    """初始化/重置一场 COC 跑团会话。KP 先不发言（等待 /autorun 或单步推进）。"""
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    body = body or CocStartIn()
    if coc_store.exists(no) and not body.force_reset:
        state = coc_store.get(no)
        return {"ok": True, "reset": False, "state": state.as_dict()}
    from ..pipeline import coc_init_run
    state = coc_init_run(
        coc_store, no, adapt,
        player_card_patch=body.player_card_patch,
        san=body.san, hp=body.hp, max_turns=body.max_turns,
    )
    return {"ok": True, "reset": True, "state": state.as_dict()}


@app.post("/api/projects/{pid}/fields/{no}/coc/keeper")
def coc_step_keeper(pid: str, no: int, body: CocAutorunIn | None = None) -> dict:
    """手动执行一轮 KP。"""
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动，请先 POST /coc/start")
    from ..pipeline import step_keeper as _sk
    runner = _runner()
    turn = _sk(runner, state, adapt_parsed=adapt, book_store=_book_for(pid),
               user_directive=(body.user_directive if body else None))
    coc_store.save(state)
    return {"ok": True, "turn": turn.as_dict(), "state": state.as_dict()}


@app.post("/api/projects/{pid}/fields/{no}/coc/investigator")
def coc_step_investigator(pid: str, no: int, body: CocAutorunIn | None = None) -> dict:
    """手动执行一轮 PL（唐迟）。"""
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动")
    from ..pipeline import step_investigator as _si
    runner = _runner()
    turn = _si(runner, state, user_directive=(body.user_directive if body else None))
    coc_store.save(state)
    return {"ok": True, "turn": turn.as_dict(), "state": state.as_dict()}


@app.post("/api/projects/{pid}/fields/{no}/coc/autorun")
def coc_autorun(pid: str, no: int, body: CocAutorunIn | None = None) -> dict:
    """自动交替跑 KP↔PL，直到终止条件或达到 max_steps 对。"""
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动，请先 POST /coc/start")
    from ..pipeline import autorun_until_break
    runner = _runner()
    body = body or CocAutorunIn()
    result = autorun_until_break(
        runner, state, adapt_parsed=adapt, book_store=_book_for(pid),
        run_store=coc_store, field_no=no,
        max_steps=body.max_steps, user_directive=body.user_directive,
    )
    return {"ok": True, **result, "state": state.as_dict()}


@app.post("/api/projects/{pid}/fields/{no}/coc/autorun/stream")
def coc_autorun_stream(pid: str, no: int, body: CocAutorunIn | None = None) -> StreamingResponse:
    """SSE 流式自动跑团，边跑边推 turn 事件。"""
    from queue import Queue
    import threading
    _require_project(pid)
    fs = _fields_for(pid); rs = _field_run_for(pid); coc_store = _coc_run_for(pid)
    adapt = rs.chosen_parsed(no, "adapt_scenario")
    if not adapt:
        raise HTTPException(status_code=422, detail="请先选定 adapt_scenario")
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动")
    runner = _runner()
    bs = _book_for(pid)
    body = body or CocAutorunIn()
    q: Queue = Queue()

    def on_ev(kind: str, payload: dict):
        q.put({"event": kind, "data": payload})

    def worker():
        try:
            from ..pipeline import autorun_until_break
            res = autorun_until_break(
                runner, state, adapt_parsed=adapt, book_store=bs,
                run_store=coc_store, field_no=no,
                max_steps=body.max_steps, user_directive=body.user_directive,
                on_event=on_ev,
            )
            q.put({"event": "done", "data": {**res, "state": state.as_dict()}})
        except Exception as e:
            q.put({"event": "error", "data": {"error": f"{type(e).__name__}: {e}"}})

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            ev = q.get()
            yield f"event: {ev['event']}\ndata: {json_dumps(ev['data'])}\n\n"
            if ev["event"] in ("done", "error"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/projects/{pid}/fields/{no}/coc/intervene")
def coc_intervene(pid: str, no: int, body: CocInterveneIn) -> dict:
    """作者介入：插入一段作者指令或替代 PL 行动。"""
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动")
    from ..pipeline import coc_intervene as _intervene
    turn = _intervene(state, body.text, coc_store, as_role=body.as_role or "author")
    return {"ok": True, "turn": turn.as_dict(), "state": state.as_dict()}


@app.patch("/api/projects/{pid}/fields/{no}/coc/state")
def coc_patch_state(pid: str, no: int, body: CocPatchIn) -> dict:
    """手动修正 SAN/HP/Day/clues 等字段（补扣/纠错）。"""
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动")
    from ..pipeline import apply_state_patch
    applied = apply_state_patch(state, body.model_dump(exclude_none=True), coc_store)
    return {"ok": True, "applied": applied, "state": state.as_dict()}


@app.post("/api/projects/{pid}/fields/{no}/coc/finalize")
def coc_finalize(pid: str, no: int, body: CocAutorunIn | None = None) -> dict:
    """结束跑团：用 coc_outcome 层把完整日志压成 Day1-7 真相，写入 coc_outcome 节点。
    finalize 之后 tang_outline 可以正常运行。"""
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动")
    from ..pipeline import coc_finalize as _finalize
    runner = _runner()
    art = _finalize(runner, state, coc_store, rs, field_no=no,
                    adapt_parsed=adapt, book_store=_book_for(pid))
    return {"ok": True, "artifact": art.as_dict(), "state": state.as_dict()}


@app.post("/api/projects/{pid}/fields/{no}/coc/finalize/stream")
def coc_finalize_stream(pid: str, no: int, body: CocAutorunIn | None = None) -> StreamingResponse:
    """SSE 流式 finalize。"""
    from queue import Queue
    import threading
    fs, rs, coc_store, adapt = _require_coc_ready(pid, no)
    state = coc_store.get(no)
    if state is None:
        raise HTTPException(status_code=422, detail="跑团尚未启动")
    runner = _runner()
    bs = _book_for(pid)
    q: Queue = Queue()

    def on_ev(kind, payload):
        q.put({"event": kind, "data": payload})

    def worker():
        try:
            from ..pipeline import coc_finalize as _finalize
            art = _finalize(runner, state, coc_store, rs, field_no=no,
                            adapt_parsed=adapt, book_store=bs, on_event=on_ev)
            q.put({"event": "done", "data": {"artifact": art.as_dict(), "state": state.as_dict()}})
        except Exception as e:
            q.put({"event": "error", "data": {"error": f"{type(e).__name__}: {e}"}})

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            ev = q.get()
            yield f"event: {ev['event']}\ndata: {json_dumps(ev['data'])}\n\n"
            if ev["event"] in ("done", "error"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.delete("/api/projects/{pid}/fields/{no}/coc")
def coc_reset(pid: str, no: int) -> dict:
    """重置本场跑团（会清掉 turns 与状态）。注意：不会清 coc_outcome 节点产物——
    想一并清，请显式 PUT /pipeline/coc_outcome 覆盖或重跑 tang_outline。"""
    _require_project(pid)
    coc_store = _coc_run_for(pid)
    if coc_store.exists(no):
        coc_store.delete(no)
    return {"ok": True, "deleted": True}


@app.post("/api/projects/{pid}/fields/{no}/coc/transcript")
def coc_transcript(pid: str, no: int, last_n: int | None = None) -> dict:
    """导出跑团完整/最近日志（纯文本，供作者查看或外部使用）。"""
    _require_project(pid)
    state = _coc_run_for(pid).get(no)
    if state is None:
        return {"exists": False, "transcript": ""}
    return {"exists": True, "transcript": state.transcript(last_n=last_n),
            "turns_total": len(state.turns),
            "status": state.status}


# ============================================================
# 创作会话（Phase 5：对话驱动 + 阶段门控）
# ============================================================

@app.get("/api/projects/{pid}/sessions")
def session_list(pid: str, scope: str | None = None,
                 field_no: int | None = None, chapter: int | None = None,
                 status: str | None = None) -> dict:
    _require_project(pid)
    ss = _sessions_for(pid)
    items = ss.list_sessions(scope=scope, field_no=field_no, chapter=chapter, status=status)
    return {"sessions": items}


@app.post("/api/projects/{pid}/sessions")
def session_create(pid: str, body: SessionCreateIn) -> dict:
    _require_project(pid)
    # 校验 scope 合法性
    if body.scope not in ("field", "chapter"):
        raise HTTPException(status_code=422, detail="scope 必须是 field 或 chapter")
    if body.scope == "field" and body.field_no is None:
        raise HTTPException(status_code=422, detail="副本会话必须传 field_no")
    if body.scope == "chapter" and body.chapter is None:
        raise HTTPException(status_code=422, detail="日更会话必须传 chapter")
    ss = _sessions_for(pid)
    # 副本会话：如果 field_no 对应的 FieldWork 还不存在，先自动建一份
    # （避免 kindle 阶段锁 core 时报「场不存在」）
    if body.scope == "field" and body.field_no is not None:
        fs = _fields_for(pid)
        if fs.get(body.field_no) is None:
            fs.create(title=body.title or f"第{body.field_no}场",
                      volume_no=1, no=body.field_no)
    s = ss.create(
        project_id=pid, scope=body.scope,
        field_no=body.field_no, chapter=body.chapter,
        title=body.title, start_phase=body.start_phase,
    )
    # ---- M3：settle_up 阶段自动建卷（幂等） ----
    if s.scope == "field" and s.field_no is not None and s.current_phase == "settle_up":
        try:
            vs = _volumes_for(pid)
            vol = vs.create_next(_book_for(pid), s.field_no)
            s.volume_no = vol.volume_no
        except Exception:
            pass
    # 加一条开场 AI 欢迎消息（按阶段）
    from ..sessions import PHASE_META
    pm = PHASE_META[s.current_phase]
    s.add_message("ai", f"【{pm['title']}】{pm['prompt']}", kind="suggestion",
                  meta={"phase": s.current_phase, "ai_mode": pm["ai_mode"]})
    ss.save(s)
    return {"ok": True, "session": s.as_dict()}


@app.get("/api/projects/{pid}/sessions/{sid}")
def session_get(pid: str, sid: str) -> dict:
    _require_project(pid)
    s = _sessions_for(pid).get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": s.as_dict()}


def _build_chat_context_block(pid: str, s) -> str:
    """给自由聊天用的紧凑上下文块：书级 premise + 主角档案 + 已写章节摘要 + 最近章尾 +
    当前会话阶段已锁卡片 + 当前卷规划。总长度控制在 ~6000 字以内，避免撑爆上下文窗口。"""
    parts: list[str] = []

    # (1) 书级硬卡（premise）
    try:
        bs = _book_for(pid)
        premise_text = ""
        if hasattr(bs, "premise"):
            prof = bs.premise()
            if prof and hasattr(prof, "render"):
                premise_text = (prof.render() or "").strip()
        if premise_text:
            parts.append(f"【书级设定·Premise（已锁定）】\n{_truncate(premise_text, 1500)}")
        # world rules 短版（不重复 WORLD_RULES_SHORT，避免和系统提示重复）
        try:
            hardcards = bs.render_all() if hasattr(bs, "render_all") else ""
            if hardcards:
                # premise 已经带过了，这里只取「唐迟刻度/七日基线/终局锚」类硬卡
                # 简化：直接截后半段（避免和 premise 重复）
                lines = hardcards.split("\n")
                tail = "\n".join(lines[-60:])
                if tail and tail not in premise_text:
                    parts.append(f"【书级硬卡·核心刻度】\n{_truncate(tail, 1500)}")
        except Exception:
            pass
    except Exception:
        pass

    # (2) 主角档案（跨场累积状态）
    try:
        es = _entities_for(pid)
        p = es.protagonist
        plines = ["【主角唐迟·跨场档案】"]
        plines.append(f"当前身份/位置：{(p.current_body_name or '唐迟')}（{p.current_body_identity or '身份未明'}）· {p.current_location or '位置未明'}"
                      + (f" · 第{p.current_day}天" if p.current_day else ""))
        plines.append(f"本场侵蚀度：{p.erosion_level}（{p.sanity_hint or '状态稳定'}）")
        plines.append(f"跨卷累积污染度：{p.contamination_level}/100 ｜ 已完成副本 {p.field_count} 场")
        if p.active_crafts:
            plines.append("已掌握手艺：" + "；".join(
                f"{c.get('name','?')}({c.get('proficiency','初窥')}，{c.get('from_field','?')})"
                for c in p.active_crafts[-6:]))
        if p.dead_echoes:
            plines.append("死者残影：" + "、".join(
                f"{e.get('name','?')}({e.get('obsession','?')}，{e.get('fade_stage','?')})"
                for e in p.dead_echoes[-4:]))
        if p.god_marks:
            plines.append("邪神标记：" + "、".join(
                f"{m.get('god','?')}·{m.get('seal_form','?')}" for m in p.god_marks[-4:]))
        if p.brought_items:
            plines.append("带回复本物品：" + "、".join(
                f"{b.get('name','?')}(污染{b.get('contam',0)})" for b in p.brought_items[-6:]))
        if p.blue_star_bonds:
            plines.append("蓝星羁绊：" + "、".join(
                f"{b.get('name','?')}({b.get('relation','?')}，羁绊{b.get('depth','浅')})"
                for b in p.blue_star_bonds))
        if p.kill_record:
            plines.append("杀青账本：" + " | ".join(p.kill_record[-6:]))
        parts.append("\n".join(plines))
    except Exception:
        pass

    # (3) 已写章节摘要 + 最近一章正文尾
    try:
        desk = _desk_for(pid)
        nos = sorted(desk.all_nos())
        if nos:
            # 摘要表（章号/标题/状态/长度/概要）
            rows = []
            for no in nos:
                w = desk.get(no)
                if w is None:
                    continue
                title = w.title or f"第{no}章"
                status_bits = []
                if work_text(w, "polish"):
                    status_bits.append("润色已定")
                elif work_text(w, "draft"):
                    status_bits.append("草稿完")
                if work_text(w, "plot") and not work_text(w, "draft"):
                    status_bits.append("有剧情卡")
                body = work_text(w, "polish") or work_text(w, "draft") or ""
                chars = len(body or "")
                preview = (w.outline_preview or "")[:80].replace("\n", " ")
                rows.append(f"  第{no}章《{title}》｜{'/'.join(status_bits) or '空'}｜{chars}字｜{preview}")
            # 最近一章正文结尾（最多 1200 字，句边界截断）
            recent_no = nos[-1]
            w_recent = desk.get(recent_no)
            recent_tail = ""
            if w_recent is not None:
                recent_body = work_text(w_recent, "polish") or work_text(w_recent, "draft") or ""
                if recent_body:
                    recent_tail = _tail_at_sentence(recent_body, 1200)
            chap_block = ["【已写章节总览（含章号/状态/字数）】"]
            chap_block.extend(rows[-20:])  # 最多列最近 20 章
            if len(rows) > 20:
                chap_block.insert(1, f"（共 {len(rows)} 章，以下只列最近 20 章；更早的章默认已在剧情中发生过）")
            if recent_tail:
                chap_block.append(f"\n【最近一章（第{recent_no}章）正文尾·请据此衔接】\n{recent_tail}")
            parts.append("\n".join(chap_block))
    except Exception:
        pass

    # (4) 场会话：复用 _build_context_block 拿本场上锁卡片 / 当前卷规划
    if s.scope == "field" and s.field_no is not None:
        try:
            from ..pipeline.sushi_generator import _build_context_block as _bcb
            fs = _fields_for(pid)
            es = _entities_for(pid)
            bs = _book_for(pid)
            vs = None
            try:
                vs = _volumes_for(pid)
            except Exception:
                vs = None
            # 当前阶段在写什么卡？kindle→core；scaffold→obituary_card/entity_card；
            # walkthrough/schedule/daily_write 没对应卡，统一用 core 让上下文带上已锁副本核即可
            card_hint = {
                "kindle": "core",
                "scaffold": "entity_card",
                "walkthrough": "entity_card",
                "schedule": "entity_card",
                "daily_write": "entity_card",
                "settle_up": "wrapup",
            }.get(s.current_phase, "core")
            field_ctx = _bcb(
                book_store=bs, fs=fs, es=es, field_no=s.field_no, card_key=card_hint,
                extra_context=(s.artifacts.get("selected_pdf_adapt") or "") if isinstance(s.artifacts, dict) else "",
                volume_store=vs, volume_no=getattr(s, "volume_no", None),
                phase=s.current_phase, is_interlude=bool(getattr(s, "is_interlude", False)),
            )
            if field_ctx.strip():
                parts.append(f"【本场（第{s.field_no}场）已锁素材·卷规·当前阶段={s.current_phase}】\n{field_ctx}")
        except Exception:
            pass

    return "\n\n".join(p for p in parts if p and p.strip())


def _truncate(text: str, n: int) -> str:
    if not text:
        return ""
    if len(text) <= n:
        return text
    return text[:n] + f"\n…（截断，余 {len(text)-n} 字）"


def _tail_at_sentence(text: str, n: int) -> str:
    if not text:
        return ""
    if len(text) <= n:
        return text
    tail = text[-n:]
    # 跳过前导的半句话
    for i, ch in enumerate(tail):
        if ch in "。！？…\n":
            return tail[i + 1 :]
    return tail


@app.post("/api/projects/{pid}/sessions/{sid}/send")
def session_send(pid: str, sid: str, body: SessionSendIn) -> dict:
    """发送一条消息。作者消息会触发 AI 回应：优先走 chore 通道接真 LLM（带项目完整上下文
    + 当前阶段系统提示 + 缺卡提示 + 近 12 条对话），失败时降级为阶段引导桩。
    若指定 output_key，同时把文本写入 outputs。"""
    import logging
    log = logging.getLogger("session.chat")
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if s.status != "active":
        raise HTTPException(status_code=422, detail=f"会话状态为 {s.status}，不能发消息")
    role = "ai" if body.as_ai else "author"
    msg = s.add_message(role, body.text, kind=body.kind, meta=body.meta or {})
    if body.output_key:
        s.set_output(body.output_key, body.text)

    reply_text: str | None = None
    if role == "author":
        from ..sessions import PHASE_META
        pm = PHASE_META.get(s.current_phase, {})
        mode = pm.get("ai_mode", "review")
        ok, miss = s.can_advance()
        # silent 模式默认不主动回复（除非有缺卡提示）
        if mode != "silent" or miss:
            # 组装消息列表：system(世界观+阶段+缺卡) + 最近 12 条历史
            from ..pipeline.sushi_generator import WORLD_RULES_SHORT
            system_parts = [
                "你是长篇克苏鲁×无限流男频小说《第七日的亡者》的写作陪练 AI。",
                WORLD_RULES_SHORT,
                f"当前阶段：{pm.get('title', s.current_phase)}。",
                f"阶段任务：{pm.get('prompt', '')}",
                f"你的模式：{mode}。",
            ]
            if mode == "diverge":
                system_parts.append(
                    "你要发散：直接给灵感、抛画面、开脑洞，不要急着收敛。\n"
                    "【重要】作者现在主动跟你聊，**优先回答他的问题/满足他的请求**（要灵感就立刻给 2-3 条具体、可写的灵感），"
                    "不要用「还有 X 项必填」来回避作答。缺项提醒最多放在回复末尾用一句话带过，不要喧宾夺主。"
                )
            elif mode == "challenge":
                system_parts.append("你要挑刺：追问逻辑漏洞、人物动机、伏笔回收，不要一味附和。先回应作者的具体问题，再挑刺。")
            elif mode == "narrate":
                system_parts.append("你要朗读/复述骨架，节奏要稳，帮助作者走查。")
            elif mode == "plan":
                system_parts.append("你要排期：把内容按 Day/章拆成可执行计划。先回应作者当前提问。")
            elif mode == "review":
                system_parts.append("你是记账员风格，客观列账、标红警告。先回应作者当前提问，再视情况提示缺漏。")
            if miss:
                system_parts.append(
                    f"目前还有 {len(miss)} 项必填产物未完成：{'；'.join(miss[:5])}。"
                    "在回答完作者的问题后，可以用一句话提一句怎么填这些产物，但不要整段都在说缺项。"
                )
            else:
                system_parts.append("本阶段必填产物已齐，可以提示作者推进。")
            system_parts.append(
                "输出格式：直接给正文，不要写「收到/好的/我理解了」这类客套开场；"
                "正文可以分段，但不要写 markdown 标题/列表符号，保持可读散文或短段落风格。"
                "用中文回复，语气直接、有干货。"
            )
            # 注入完整项目上下文（书级 premise + 主角档案 + 已写章节 + 当前场状态）
            try:
                ctx_block = _build_chat_context_block(pid, s)
                if ctx_block.strip():
                    system_parts.append(
                        "===== 你现在必须阅读的项目实况（真实已写内容，不要假装没看过）=====\n"
                        + ctx_block
                        + "\n===== 以上是项目实况，回答作者问题时必须基于这些内容，不得声称'检索不到/没看到'。====="
                    )
            except Exception as _ctx_err:
                log.warning("chat context block build failed: %s", _ctx_err)
            messages = [{"role": "system", "content": "\n".join(system_parts)}]
            # 最近 12 条历史（跳过系统/纯 gate 噪音）；LLM 只认 assistant，内部 ai 角色需转换
            history = [m for m in s.messages if m.role in ("author","ai")][-12:]
            for m in history:
                llm_role = "user" if m.role == "author" else "assistant"
                messages.append({"role": llm_role, "content": m.text or ""})
            try:
                provider = _chore_provider("chat")
                result = provider.chat(messages)
                text = (getattr(result, "text", None) or "").strip()
                if text:
                    reply_text = text
            except Exception as e:
                log.warning("session chat LLM failed, falling back to stub: %s", e)

        # 兜底桩：LLM 挂了也要给出干货，不能只说"收到"
        if reply_text is None:
            if miss:
                miss_tail = f"（还差：{'；'.join(miss[:3])}" + ("…" if len(miss) > 3 else "") + "）"
            else:
                miss_tail = "（本段必填已齐，可以推进到下一阶段。）"
            phase = s.current_phase
            if mode == "diverge" and phase == "kindle":
                reply_text = (
                    "先抛四条方向种子（题材各异，都可以做），挑一个揉或者直接拿来写副本核：\n"
                    "1)【民俗阴冷·老筒子楼】梅雨季节，唐迟附身在一个刚搬回外婆旧居的夜班校对员身上，"
                    "第一晚发现楼道里每家门口都摆着一双湿透的红布鞋，但整层楼没住过女人；"
                    "规则：不能在凌晨 2:47 回应门外喊自己名字的声音，否则鞋尖会转向你家。\n"
                    "2)【规则冷峻·乡镇医院】替位成来报到的实习医生，夜里巡房发现 3 号病房总在熄灯后多出一个病人，"
                    "病历上写着同一个名字但每天换一张脸；规则：不能给那个不存在的病人开处方，"
                    "否则处方上的药会出现在你自己第二天的医嘱里。\n"
                    "3)【诡异日常·末班地铁】替位成末班车临时调度员，车总在不存在的「青水湾」站停车，"
                    "上来的人影子没有脚；规则：不能查那个站的任何资料，"
                    "查了就会被安排去那个站值第二天的夜班。\n"
                    "4)【科幻外壳·深空观测站】替位成火星轨道观测站的值班工程师，"
                    "第七天值班周期结束时换班船永远不会来；规则：每次舷窗外出现「地球」时"
                    "不能看超过 7 秒，否则你会开始数自己到底有几根手指。\n"
                    f"挑一个顺着外壳→地点→日常→开场异常→规则→钩子→唐迟入场锚（替换谁、为什么七天后死）写副本核就行。{miss_tail}"
                )
            elif mode == "diverge":
                reply_text = (
                    "暂时没接到大模型，我先按当前阶段给你点可写的抓手："
                    "盯住一个具体的空间细节（墙皮、气味、声音），往里塞进一个违背常识的小动作，"
                    "再给这个动作加一个不遵守就出事的代价——钩子基本就出来了。"
                    f"{miss_tail}"
                )
            elif mode == "review":
                reply_text = f"目前本阶段账面上：{miss_tail} 你可以继续往下写，写完我再帮你盘点。"
            elif mode == "challenge":
                reply_text = f"先标记一下：{miss_tail} 等大模型恢复后我帮你挑细节逻辑。"
            elif mode == "narrate":
                reply_text = f"复述暂不可用。{miss_tail}"
            elif mode == "plan":
                reply_text = f"暂时没法拆计划。{miss_tail}"
            else:
                reply_text = miss_tail

        s.add_message("ai", reply_text, kind="chat",
                      meta={"phase": s.current_phase, "ai_mode": mode,
                            "responding_to": msg.id, "can_advance": ok, "missing": miss})
    ss.save(s)
    return {"ok": True, "session": s.as_dict(), "message": msg.as_dict()}


@app.post("/api/projects/{pid}/sessions/{sid}/gate")
def session_set_gate(pid: str, sid: str, body: SessionGateIn) -> dict:
    """设置一个阶段必填产物的值（比如 settlement_confirmed=true，core_pitch=一段文本）。"""
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    s.set_output(body.key, body.value)
    s.add_message("system", f"已填：{body.key}", kind="gate",
                  meta={"key": body.key, "value": body.value})
    ok, miss = s.can_advance()
    if ok:
        s.add_message("ai", "本段所有必填产物已齐，可以推进到下一阶段。",
                      kind="gate", meta={"can_advance": True})
    ss.save(s)
    return {"ok": True, "session": s.as_dict(), "can_advance": ok, "missing": miss}


@app.post("/api/projects/{pid}/sessions/{sid}/advance")
def session_advance(pid: str, sid: str) -> dict:
    _require_project(pid)
    ss = _sessions_for(pid)
    prev_phase = None
    try:
        cur = ss.get(sid)
        prev_phase = cur.current_phase if cur else None
        s = ss.advance_phase(sid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 进入新阶段时插入 AI 开场提示
    from ..sessions import PHASE_META
    pm = PHASE_META.get(s.current_phase)
    if pm and s.current_phase != prev_phase and s.status == "active":
        s.add_message("ai", f"【{pm['title']}】{pm['prompt']}", kind="suggestion",
                      meta={"phase": s.current_phase, "ai_mode": pm["ai_mode"]})
    # 副作用：schedule 阶段定稿 → 自动在 DeskStore 创建章位；全副本会话结束
    if s.status == "completed" and prev_phase == "schedule" and s.scope == "field":
        _materialize_schedule_to_desk(pid, s)
    ss.save(s)
    return {"ok": True, "session": s.as_dict()}


def _materialize_schedule_to_desk(pid: str, s) -> None:
    """schedule 阶段锁定排期后，按 outputs.chapter_plan 在 DeskStore 里建章位。
    chapter_plan 格式：list[dict]，每项 {no, title, day, summary}。缺省用 outputs 里的整数起点。"""
    desk = _desk_for(pid)
    plan = s.outputs.get("chapter_plan")
    created: list[dict] = []
    if isinstance(plan, list):
        for item in plan:
            if not isinstance(item, dict): continue
            no = int(item.get("no") or 0)
            if no <= 0: continue
            title = str(item.get("title") or "")
            desk.ensure(no, title=title)
            # 若带 plot 摘要就作为 plot 初始值（留空则留给作者）
            summary = str(item.get("summary") or "").strip()
            if summary:
                desk.record(no, "plot", summary, run_id=f"session:{s.id}", title=title)
            created.append({"no": no, "title": title})
    elif s.outputs.get("chapter_count"):
        # 没有精细 plan，就按 chapter_count 建空章位（从已有最大章号 +1 起）
        try:
            n = int(s.outputs["chapter_count"])
        except Exception:
            n = 0
        if n > 0:
            existing = desk.all_nos()
            start = (max(existing) + 1) if existing else 1
            for i in range(n):
                desk.ensure(start + i, title="")
                created.append({"no": start + i, "title": ""})
    if created:
        s.add_message(
            "system",
            "排期已锁定：在章台创建了 {} 个章位（{}）。下次打开工具会自动定位到第 {} 章。".format(
                len(created),
                ", ".join(f"第{c['no']}章" for c in created[:6]) + ("…" if len(created) > 6 else ""),
                created[0]["no"],
            ),
            kind="gate",
            meta={"created_chapters": created},
        )


@app.post("/api/projects/{pid}/sessions/{sid}/rollback")
def session_rollback(pid: str, sid: str, body: SessionRollbackIn) -> dict:
    _require_project(pid)
    ss = _sessions_for(pid)
    try:
        s = ss.rollback_phase(sid, body.to_phase)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 回退时解锁回退点之后阶段写进 FieldStore 的卡，让作者能重新编辑
    # 阶段→该阶段产出的 card_key 映射；只清回退后阶段以后的
    phase_cards = {
        "settle_up": ["wrapup"],
        "kindle": ["core"],
        "scaffold": list(SCAFFOLD_CARD_KEYS),  # obituary_card/entity_card/truth_outline/...
        "walkthrough": ["walkthrough"],
        "schedule": ["chapter_plan"],
        "daily_write": [],
    }
    phase_order = ["settle_up", "kindle", "scaffold", "walkthrough", "schedule", "daily_write"]
    try:
        target_idx = phase_order.index(body.to_phase)
    except ValueError:
        target_idx = -1
    to_unlock: list[str] = []
    clear_outputs: list[str] = []
    if target_idx >= 0:
        # 清目标阶段之后所有阶段的卡（目标阶段本身也"重新来"，所以把目标阶段自己的也清掉）
        for ph in phase_order[target_idx:]:
            for k in phase_cards.get(ph, []):
                if k not in to_unlock:
                    to_unlock.append(k)
            # kindle 的 core 在 outputs 里叫 core_pitch
            if ph == "kindle":
                clear_outputs.append("core_pitch")
            else:
                clear_outputs.extend(phase_cards.get(ph, []))
        # 清会话侧的 locked_cards / editing / 阶段相关 outputs
        for k in list(s.locked_cards.keys()):
            card_key = "core" if k == "core_pitch" else k
            if card_key in to_unlock:
                s.locked_cards.pop(k, None)
        for k in clear_outputs:
            s.outputs.pop(k, None)
        # 回退后任何卡都不在编辑态
        s.editing = None
        # 同时清 FieldStore 里的锁文本
        fs = _fields_for(pid)
        if s.scope == "field" and s.field_no is not None:
            fs.unlock_cards(s.field_no, to_unlock)
    ss.save(s)
    s2 = ss.get(sid)
    return {"ok": True, "session": s2.as_dict()}


# 三种风味，圆桌结论里 kindle 阶段要的「民俗/心理/都市怪谈」三栏雏形
_KINDLE_FLAVORS = [
    {"label": "民俗怪谈", "hook": "老物件、旧俗、乡里禁忌",
     "obsession_power": "伞面能映出死者生前最后一眼",
     "obsession_cost": "每用一次，自己的记忆会被死者借走一段",
     "world_premise": "乡镇旧俗里残留的怪异，根源是被遗忘的祭祀"},
    {"label": "心理诡异", "hook": "内心执念的实体化、分不清是谁的记忆",
     "obsession_power": "在心跳之间能短暂进入死者的主观视角",
     "obsession_cost": "现实中自己的情绪会被死者的情绪污染",
     "world_premise": "SAN 越低越分不清现实与死者记忆，恐怖来自主观体验"},
    {"label": "都市怪谈", "hook": "现代城市缝隙里的规则怪谈、匿名的他者",
     "obsession_power": "在监控/玻璃/水面反射里能看到死者指向的线索",
     "obsession_cost": "每次看见反射，自己也会被某个看不见的东西多看一眼",
     "world_premise": "现代都市日常表层下藏着按规则运行的诡异网络"},
]


def _kindle_diverge(pid: str, s, *, n_variants: int = 3,
                    user_directive: str | None = None) -> list[dict]:
    """kindle 阶段：用作者的 core_pitch 构造 3 个风味伪 adapt，跑 N 场 COC 仿真，
    返回 variants 列表（summary card 结构）。不写入正式 field_run_store，
    只存在 session.artifacts["kindle_variants"] 里供作者选/揉。"""
    from ..pipeline.coc_simulator import run_coc_simulation_batch
    core_pitch = (s.outputs.get("core_pitch") or "").strip()
    if not core_pitch:
        raise HTTPException(status_code=422, detail="先写一段副本核（core_pitch）再发散")
    field_no = int(s.field_no or 1)
    # 若 flavor 数少于 n_variants，重复补
    flavors = list(_KINDLE_FLAVORS)
    while len(flavors) < n_variants:
        flavors.append(flavors[len(flavors) % len(_KINDLE_FLAVORS)])
    flavors = flavors[:n_variants]

    book_store = _book_for(pid)
    field_store = _fields_for(pid)
    field_run_store = _field_run_for(pid)
    coc_store = _coc_run_for(pid)
    runner = _coc_runner()

    # 用第一个 flavor 构造一份迷你 adapt，作为三场共同的骨架（每场通过 variant_seed 再注入差异）
    base_adapt = {
        "vid": "kindle",
        "title": f"副本雏形（来自：{core_pitch[:24]}…）",
        "world_premise": core_pitch,
        "hook": core_pitch,
        "obsession_power": "",
        "obsession_cost": "",
        "entry_mood": "潮湿、不安",
    }
    directive_parts = []
    if user_directive:
        directive_parts.append(user_directive)
    directive_parts.append(
        "【副本核】" + core_pitch + "\n"
        "本场是 kindle 发散阶段的一个雏形，不需要严谨收敛，"
        "重点是给出一个有画面感、有诡异气质、有 1-2 个让人后背发凉的 NPC/反转的 Day1-7 雏形。"
    )

    events: list[dict] = []
    def _on_event(kind: str, payload: dict) -> None:
        events.append({"kind": kind, "payload": payload})

    variants = run_coc_simulation_batch(
        runner,
        book_store=book_store, field_store=field_store,
        field_no=field_no, coc_store=coc_store, field_run_store=field_run_store,
        adapt_parsed=base_adapt,
        n_sessions=n_variants,
        max_pairs_per_session=4,     # kindle 阶段少跑几轮，只给雏形
        max_turns_per_session=16,
        on_event=_on_event,
        user_directive="\n".join(directive_parts),
    )
    # 给每个 variant 打 flavor 标签
    for i, v in enumerate(variants):
        fl = flavors[i % len(flavors)]
        v["flavor"] = fl["label"]
        v["flavor_hook"] = fl["hook"]
    return variants


@app.post("/api/projects/{pid}/sessions/{sid}/action")
def session_action(pid: str, sid: str, body: SessionActionIn) -> dict:
    """会话内动作：阶段特有的「主动让 AI 干活」接口（区别于 send 闲聊）。
    支持的 action：
      - kindle_diverge：kindle 阶段用 core_pitch 跑 3 场 COC 雏形，结果存 artifacts
      - daily_summon：  daily_write 阶段召唤 AI 给 2-3 个卡壳选项（桩实现）
      - walkthrough_step：walkthrough 阶段让 AI 朗读下一段骨架（桩实现）
    """
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if s.status != "active":
        raise HTTPException(status_code=422, detail=f"会话状态为 {s.status}")
    from ..sessions import PHASE_META
    phase = s.current_phase
    pm = PHASE_META.get(phase, {})
    args = body.args or {}

    if body.action == "kindle_diverge":
        if phase != "kindle":
            raise HTTPException(status_code=422, detail=f"当前阶段是 {pm.get('title', phase)}，不能 kindle_diverge")
        n = max(1, min(5, int(body.n_variants or 3)))
        s.add_message("system", f"开始跑 {n} 场 COC 雏形（kindle 发散）…", kind="gate")
        ss.save(s)
        try:
            variants = _kindle_diverge(pid, s, n_variants=n,
                                       user_directive=args.get("user_directive"))
        except HTTPException:
            raise
        except Exception as e:
            s.add_message("ai", f"发散失败：{type(e).__name__}: {e}", kind="suggestion")
            ss.save(s)
            raise HTTPException(status_code=500, detail=str(e))
        s.artifacts["kindle_variants"] = variants
        # 拼一个结构化的 AI 消息：三栏雏形摘要
        lines = ["我先跑了 {} 个雏形，方向都基于你写的副本核：".format(len(variants))]
        for i, v in enumerate(variants, 1):
            lines.append(f"\n### 雏形 {i}·{v.get('flavor','')}（{v.get('ending_label','?')}）\n")
            lines.append(f"- 一句话：{v.get('one_liner','')}")
            if v.get("core_twist"):
                lines.append(f"- 核心反转：{v['core_twist']}")
            if v.get("extra_npcs"):
                raw_npcs = v["extra_npcs"] or []
                npc_names = [
                    (n.get("name","?") if isinstance(n, dict) else str(n))
                    for n in raw_npcs
                ]
                lines.append(f"- 新增 NPC：{', '.join(npc_names[:4])}")
            lines.append(f"- 结局 SAN {v.get('final_san')} / HP {v.get('final_hp')} / {v.get('turns')} 回合")
        lines.append("\n选一个你最喜欢的（或揉几个），把它改写成你的 300-500 字副本核发我，我再陪你进骨架。")
        s.add_message("ai", "\n".join(lines), kind="suggestion",
                      meta={"phase": phase, "action": "kindle_diverge",
                            "variant_count": len(variants),
                            "variant_titles": [v.get("title") for v in variants]})
        ss.save(s)
        return {"ok": True, "session": s.as_dict(), "variants": variants}

    if body.action == "daily_summon":
        return {"status": "construction", "message": "日更召唤建设中，后续版本接入 AI 卡壳建议"}

    if body.action == "walkthrough_step":
        return {"status": "construction", "message": "走查朗读建设中"}

    if body.action == "plan_chapters":
        return {"status": "deprecated", "message": "plan_chapters 已废弃，章前5分钟（brief）替代一次性拆章；请通过 /workbench 新流程逐章生成 brief"}

    raise HTTPException(status_code=422, detail=f"未知 action：{body.action}")


@app.delete("/api/projects/{pid}/sessions/{sid}")
def session_abandon(pid: str, sid: str) -> dict:
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    s.status = "abandoned"
    s.add_message("system", "会话已放弃。", kind="gate")
    ss.save(s)
    return {"ok": True, "session": s.as_dict()}


# ======================================================================
# M1：回转寿司 + 世界状态热板新端点
# ======================================================================

# ---- 锁卡片（lock-on-write 统一入口） ----

class LockCardIn(BaseModel):
    card_key: str                       # core | obituary_card | entity_card | truth_outline |
                                        # tangchi_start | core_twist | foreshadows | cost_cap |
                                        # walkthrough_log | schedule | scene:{idx}
    text: str
    scope: str = "field"                # field | chapter
    field_no: int | None = None
    chapter: int | None = None          # chapter 模式下的章号
    source_msg_id: str | None = None    # 触发锁的对话消息 id（溯源）
    note: str = ""


@app.post("/api/projects/{pid}/sessions/{sid}/lock")
def session_lock_card(pid: str, sid: str, body: LockCardIn) -> dict:
    """回转寿司的「落锤」动作：把当前编辑态文本写进正式存储（FieldStore/DeskStore），
    同时在会话里记录锁事件。M1 阶段不自动生成 StateDiff（M3 接 LLM 抽取）。
    """
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if s.status != "active":
        raise HTTPException(status_code=422, detail=f"会话状态为 {s.status}，不能锁定")

    key = body.card_key
    text = body.text or ""
    scope = body.scope or (s.scope or "field")

    # 定位上下文编号（field_no / chapter）：优先 body，其次 session
    field_no = body.field_no if body.field_no is not None else s.field_no
    chapter = body.chapter if body.chapter is not None else s.chapter

    # ---- 路由到对应 store 写入（ValueError 统一转 422，并兜底补建缺失场）----
    settle_preview = None
    try:
        if key == "core":
            if field_no is None:
                raise HTTPException(status_code=422, detail="锁 core 需要 field_no")
            fs = _fields_for(pid)
            if fs.get(field_no) is None:
                fs.create(title=s.title or f"第{field_no}场", volume_no=1, no=field_no)
            fw = fs.set_core(field_no, text, source_msg_id=body.source_msg_id)
            locked_ref = {"scope": "field", "field_no": field_no, "card_key": key}
        elif key.startswith("scene:"):
            # 场景锁：key 形如 "scene:2"（章内场景 idx=2）
            try:
                idx = int(key.split(":", 1)[1])
            except ValueError:
                raise HTTPException(status_code=422, detail=f"scene key 格式错：{key}")
            chapter_no = chapter
            if chapter_no is None:
                raise HTTPException(status_code=422, detail="锁场景需要 chapter 号")
            desk = _desk_for(pid)
            scene = desk.lock_scene(chapter_no, idx, text, source_msg_id=body.source_msg_id)
            locked_ref = {"scope": "chapter", "chapter": chapter_no,
                          "card_key": key, "scene_idx": idx, "scene": scene.as_dict()}
        elif key in ALL_CARD_KEYS:
            if field_no is None:
                raise HTTPException(status_code=422, detail=f"锁 {key} 需要 field_no")
            fs = _fields_for(pid)
            if fs.get(field_no) is None:
                fs.create(title=s.title or f"第{field_no}场", volume_no=1, no=field_no)
            lock_obj = fs.lock_card(field_no, key, text,
                                    source_msg_id=body.source_msg_id, note=body.note)
            locked_ref = {"scope": "field", "field_no": field_no,
                          "card_key": key, "lock": lock_obj.as_dict()}
            # ---- M3：锁 wrapup → 自动抽取归账预览（不写入） ----
            if key == "wrapup" and text.strip():
                try:
                    from ..pipeline.settle import extract_settle_from_wrapup
                    settle_preview = extract_settle_from_wrapup(
                        text, _chore_provider("settle"), _prompt_store,
                        field_no=field_no,
                    )
                except Exception:
                    settle_preview = None
        else:
            supported = "core、scene:N、" + "、".join(ALL_CARD_KEYS)
            raise HTTPException(status_code=422,
                                detail=f"未知 card_key：{key}；支持 {supported}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # ---- 会话侧记录锁（record_lock 内部会清 editing 并存盘）----
    sys_msg = s.add_message("system", f"已锁定「{key}」。",
                            kind="card_edit",
                            meta={"card_key": key, "action": "lock",
                                  "source_msg_id": body.source_msg_id,
                                  "locked_ref": locked_ref})
    s2 = ss.record_lock(sid, key, sys_msg.id)
    # 把锁定文本同步写到 outputs[gate_key]，推进阶段的 can_advance 才能识别
    gate_key = "core_pitch" if key == "core" else key
    s2.set_output(gate_key, text)
    ss.save(s2)
    s3 = ss.get(sid)
    return {"ok": True, "session": s3.as_dict(), "locked_ref": locked_ref,
            "settle_preview": settle_preview,
            "diff_id": None}   # M1 不自动产生 diff；M3 settle_preview 直接给前端


# ---- 世界状态热板：查询 ----

@app.get("/api/projects/{pid}/state")
def state_overview(pid: str) -> dict:
    """返回世界状态热板总览：主角 + 实体计数 + 最近变动 + 关系数。"""
    _require_project(pid)
    es = _entities_for(pid)
    return {
        "protagonist": es.protagonist.as_dict(),
        "counts": {t: len(es.list(type=t)) for t in ENTITY_TYPES},
        "changes_total": len(es.changes),
        "relations_total": len(es.relations),
        "pending_diffs": len(es.pending_diffs),
        "applied_diffs": len(es.applied_diffs),
    }


@app.get("/api/projects/{pid}/state/pending")
def state_pending_diffs(pid: str) -> dict:
    """返回待确认的状态变更清单（来自会话/草稿抽取后 AI 提议）。"""
    _require_project(pid)
    es = _entities_for(pid)
    items = [d.as_dict() for d in es.pending_diffs if d.status == "pending"]
    return {"pending": items, "count": len(items)}


@app.get("/api/projects/{pid}/state/snapshot")
def state_snapshot(pid: str, chapter: int | None = None) -> dict:
    """截至某章的实体快照（用于章台开章任务书/prompt 注入）。chapter 缺省返回最新。"""
    _require_project(pid)
    es = _entities_for(pid)
    ch = chapter if chapter is not None else 999999
    snap = es.chapter_snapshot(ch)
    snap["protagonist"] = es.protagonist.as_dict()
    return snap


# ---- 世界状态热板：确认 diff ----

class ApplyDiffIn(BaseModel):
    # selections 结构：{bucket: {index_in_bucket: true/false}}
    # bucket ∈ new_entities/state_changes/new_foreshadows/foreshadow_payoffs/ripple_hints
    selections: dict[str, dict[str, bool]]


@app.post("/api/projects/{pid}/state/apply-diff/{diff_id}")
def state_apply_diff(pid: str, diff_id: str, body: ApplyDiffIn) -> dict:
    """确认一份 StateDiff：按 selections 把选中的变更写入 EntityStore。"""
    _require_project(pid)
    es = _entities_for(pid)
    try:
        # 把前端传的字符串 key 转回 int
        norm: dict[str, dict[int, bool]] = {}
        for bucket, idx_map in (body.selections or {}).items():
            norm[bucket] = {int(k): bool(v) for k, v in idx_map.items()}
        result = es.apply_diff(diff_id, norm)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    es._flush()
    return {"ok": True, "diff_id": diff_id, "applied": result}


@app.post("/api/projects/{pid}/state/dismiss-diff/{diff_id}")
def state_dismiss_diff(pid: str, diff_id: str) -> dict:
    """丢弃一份待确认 diff（不改任何实体）。"""
    _require_project(pid)
    es = _entities_for(pid)
    es.dismiss_diff(diff_id)
    es._flush()
    return {"ok": True, "diff_id": diff_id}


# ---- 回转寿司：生成候选（M2 接真 LLM，core/obituary_card/entity_card 三卡） ----

class GenerateCandidatesIn(BaseModel):
    card_key: str                       # core / obituary_card / entity_card / scene:{idx}/...
    phase: str                          # kindle/scaffold/walkthrough/...
    axis_label: str = ""                # 可选：覆盖默认差异轴标签
    axis_dimension: str = ""            # 可选：覆盖默认差异轴维度
    n: int = 3
    context_hint: str = ""              # 作者额外要求（可选）


@app.post("/api/projects/{pid}/sessions/{sid}/source-pdf")
async def session_upload_source_pdf(
    pid: str, sid: str,
    file: UploadFile = File(...),
    user_hint: str = Form(""),
) -> dict:
    """上传跑团/模组 PDF → 服务端抽文本 → LLM 做版权规避改编 → 存到 session.source_material，
    之后 kindle 的 generate-candidates 会自动把改编稿注入上下文。"""
    import time as _time
    from datetime import datetime
    from ..pipeline.sushi_generator import extract_pdf_text, adapt_pdf_source
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if s.status != "active":
        raise HTTPException(status_code=422, detail=f"会话状态为 {s.status}")
    fname = (file.filename or "").lower()
    if not fname.endswith(".pdf"):
        raise HTTPException(status_code=422, detail="仅支持 .pdf 文件")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="PDF 过大（>20MB），请先压缩")
    if not pdf_bytes:
        raise HTTPException(status_code=422, detail="PDF 文件为空")
    # 抽文本
    t0 = _time.time()
    try:
        pdf_text, _ = extract_pdf_text(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF 解析失败：{e}")
    log.info("pdf-upload %s: extracted %d chars in %.1fs",
             file.filename, len(pdf_text), _time.time() - t0)
    if len(pdf_text.strip()) < 100:
        raise HTTPException(
            status_code=422,
            detail="提取出的文本太短（可能是扫描版 PDF，需要 OCR；或文件已损坏）",
        )
    # 接真 LLM 改编（复用 sushi 通道，与回转寿司同配置）
    t1 = _time.time()
    try:
        result = adapt_pdf_source(
            _chore_provider("sushi"),
            _prompt_store,
            pdf_text,
            user_hint=user_hint or "",
        )
    except ValueError as e:
        log.warning("pdf-upload %s: adapt ValueError after %.1fs: %s",
                    file.filename, _time.time() - t1, e)
        raise HTTPException(status_code=502, detail=f"版权规避改编失败：{e}")
    except Exception as e:
        log.warning("pdf-upload %s: adapt exception after %.1fs: %s",
                    file.filename, _time.time() - t1, e)
        raise HTTPException(status_code=502, detail=f"改编调用异常：{e}")
    log.info("pdf-upload %s: LLM adapt done in %.1fs (attempts=%d, premise=%d chars)",
             file.filename, _time.time() - t1, result.attempts, len(result.adapted_premise))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    src = {
        "kind": "pdf_adapt",
        "premise": result.adapted_premise,
        "renamed_cast": list(result.renamed_cast),
        "changed_elements": list(result.changed_elements),
        "risk_flags": list(result.risk_flags),
        "original_excerpt": result.original_excerpt,
        "original_chars": result.original_chars,
        "filename": file.filename or "",
        "adapted_at": now_str,
        "attempts": result.attempts,
    }
    # 写回 session + 加一条系统消息
    s.source_material = src
    cast_n = len(result.renamed_cast)
    change_n = len(result.changed_elements)
    risk_n = len(result.risk_flags)
    s.add_message(
        "system",
        f"已上传跑团剧本文档《{file.filename}》并完成版权规避改编："
        f"替换角色 {cast_n} 位，关键事件改动 {change_n} 处，风险提示 {risk_n} 条。"
        f"接下来点击「上三盘」时 AI 会以改编稿为灵感生成副本核候选。",
        kind="source_upload",
        meta={"source_material": src},
    )
    ss.save(s)
    s2 = ss.get(sid)
    return {
        "ok": True,
        "session": s2.as_dict(),
        "adapt": {
            "premise": result.adapted_premise,
            "renamed_cast": list(result.renamed_cast),
            "changed_elements": list(result.changed_elements),
            "risk_flags": list(result.risk_flags),
            "original_excerpt": result.original_excerpt,
            "original_chars": result.original_chars,
            "filename": file.filename or "",
            "attempts": result.attempts,
        },
    }


@app.post("/api/projects/{pid}/sessions/{sid}/generate-candidates")
def session_generate_candidates(pid: str, sid: str, body: GenerateCandidatesIn) -> dict:
    """回转寿司生成 N 份候选。core/obituary_card/entity_card 接真 LLM，
    其他卡（M2 暂不支持）返回 422 提示先用旧编辑框。"""
    _require_project(pid)
    from ..sessions import Candidate
    from ..pipeline.sushi_generator import (
        generate_card_candidates, supported_card_keys, CARD_AXES,
        _build_context_block,
    )
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if s.status != "active":
        raise HTTPException(status_code=422, detail=f"会话状态为 {s.status}")

    if body.card_key not in supported_card_keys():
        raise HTTPException(
            status_code=422,
            detail=f"M2 回转寿司暂不支持 card_key={body.card_key}（目前只支持"
                   f" {', '.join(supported_card_keys())}）；其他卡请先用旧编辑框手动写。",
        )

    default_n = int(CARD_AXES[body.card_key].get("default_n", 3))
    n = max(1, min(8, int(body.n or default_n)))
    axis = CARD_AXES[body.card_key]
    axis_dim = body.axis_dimension or axis.get("dimension", "")
    axis_label = body.axis_label or axis["axis_label"]

    # 取上下文（book+上游已锁卡+主角状态）
    fs = _fields_for(pid)
    es = _entities_for(pid)
    bs = _book_for(pid)
    field_no = s.field_no
    if field_no is None:
        raise HTTPException(status_code=422, detail="当前会话未关联 field_no，无法生成场级卡")
    # 如果作者上传过跑团 PDF 改编稿，把改编后的副本核雏形作为素材注入
    extra_ctx = ""
    src = s.source_material or {}
    if src.get("kind") == "pdf_adapt" and src.get("premise"):
        extra_ctx = (
            f"【跑团剧本版权规避改编稿（供参考，不要直接复用其中可能残留的原名）】\n"
            f"{src.get('premise','')}\n"
        )
        if src.get("changed_elements"):
            extra_ctx += "【相较原剧本的关键改动】\n" + "\n".join(f"- {x}" for x in src["changed_elements"]) + "\n"
        if src.get("risk_flags"):
            extra_ctx += "【需要继续规避的风险点】\n" + "\n".join(f"- {x}" for x in src["risk_flags"]) + "\n"
    ctx = _build_context_block(
        book_store=bs, fs=fs, es=es, field_no=field_no, card_key=body.card_key,
        extra_context=extra_ctx,
        volume_store=_volumes_for(pid),
        volume_no=s.volume_no,
        phase=s.current_phase,
        is_interlude=False,   # 回转寿司候选生成是卡面生成，不按单章间章判断；间章提示由 daily_write 阶段拼
    )

    try:
        result = generate_card_candidates(
            _chore_provider("sushi"),
            _prompt_store,
            card_key=body.card_key,
            context_block=ctx,
            user_hint=body.context_hint or "",
            n=n,
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"候选生成失败：{e}")

    cands = []
    for item in result.candidates:
        twist = str(item.get("twist", "") or "").strip()
        c = Candidate(
            id="",
            phase=body.phase,
            card_key=body.card_key,
            content=item["content"],
            axis_label=axis_label,
            axis_dimension=axis_dim,
            twist=twist,
            rationale=item.get("rationale", ""),
            created_at="",
        )
        # twist 作为盘头标签拼在 rationale 前面（前端回转寿司一盘左上角显示）
        if twist:
            tag = f"[{twist}] "
            if not c.rationale.startswith(tag):
                c.rationale = tag + c.rationale
        cands.append(c)

    saved = ss.add_candidates(sid, cands)
    # add_candidates 内部 get→改→save；外层再 get 一次拿最新快照
    s2 = ss.get(sid)
    return {
        "ok": True,
        "session": s2.as_dict(),
        "candidates": [c.as_dict() for c in saved],
        "stub": False,
        "axis_label": axis_label,
        "axis_dimension": axis_dim,
    }


# ---- 回转寿司：挑候选 / 编辑 ----

class PickCandidateIn(BaseModel):
    candidate_id: str | None = None     # None = 从空白开始
    card_key: str
    initial_text: str | None = None     # 不传则自动填候选文本


@app.post("/api/projects/{pid}/sessions/{sid}/pick-candidate")
def session_pick_candidate(pid: str, sid: str, body: PickCandidateIn) -> dict:
    """挑中一份候选 → 进入编辑态。candidate_id=None 表示从空白开始。"""
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 找候选内容
    initial = body.initial_text
    if initial is None and body.candidate_id:
        cand = next((c for c in s.candidates if c.id == body.candidate_id), None)
        if cand is None:
            raise HTTPException(status_code=404, detail=f"候选不存在：{body.candidate_id}")
        initial = cand.content
    try:
        editing = ss.start_editing(sid, body.card_key,
                                   candidate_id=body.candidate_id,
                                   initial_text=initial or "")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # start_editing 内部已 save；重新读回避免外层 s 引用过期
    s = ss.get(sid)
    return {"ok": True, "session": s.as_dict(), "editing": editing.as_dict()}


class EditingUpdateIn(BaseModel):
    text: str


@app.post("/api/projects/{pid}/sessions/{sid}/editing/update")
def session_editing_update(pid: str, sid: str, body: EditingUpdateIn) -> dict:
    """作者手动改动编辑态文本（不记消息，避免刷屏）。"""
    _require_project(pid)
    ss = _sessions_for(pid)
    if ss.get(sid) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        ss.update_editing_text(sid, body.text or "")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "session": ss.get(sid).as_dict()}


@app.post("/api/projects/{pid}/sessions/{sid}/editing/end")
def session_editing_end(pid: str, sid: str) -> dict:
    """退出编辑态（不锁），用于切换卡片。"""
    _require_project(pid)
    ss = _sessions_for(pid)
    if ss.get(sid) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        ss.end_editing(sid)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "session": ss.get(sid).as_dict()}


# ---- 回转寿司：编辑态 AI 建议（suggestion：返回完整改后全文） ----

class EditingSuggestIn(BaseModel):
    instruction: str                   # 作者改稿要求（如"开头更冷一点"）
    apply: bool = False                # True=直接把建议写回编辑态文本


@app.post("/api/projects/{pid}/sessions/{sid}/editing/ai-suggest")
def session_editing_ai_suggest(pid: str, sid: str, body: EditingSuggestIn) -> dict:
    """编辑态 AI 改稿建议（suggestion 类型）：返回完整改后全文 + 理由。"""
    _require_project(pid)
    from ..pipeline.sushi_generator import suggest_edit, _build_context_block
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if s.editing is None:
        raise HTTPException(status_code=422, detail="当前不在编辑态，先选一个候选或新建")
    instruction = (body.instruction or "").strip()
    # 前端允许空指令：不填就走"润色优化"默认提示
    if not instruction:
        instruction = "请在保持原意与结构不变的前提下，做一版润色优化：让语言更贴合克苏鲁无限流的阴冷基调，节奏更紧凑，细节更瘆人。"
    fs = _fields_for(pid)
    es = _entities_for(pid)
    bs = _book_for(pid)
    ctx = ""
    if s.field_no is not None:
        ctx = _build_context_block(
            book_store=bs, fs=fs, es=es, field_no=s.field_no,
            card_key=s.editing.card_key,
            volume_store=_volumes_for(pid),
            volume_no=s.volume_no,
            phase=s.current_phase,
            is_interlude=False,
        )
    try:
        result = suggest_edit(
            _chore_provider("sushi_edit"),
            _prompt_store,
            card_key=s.editing.card_key,
            current_text=s.editing.current_text,
            instruction=instruction,
            context_block=ctx,
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI 改稿失败：{e}")

    # 写入一条 suggestion 消息（作者可以看到全文）
    msg = s.add_message(
        role="ai",
        text=f"【改稿建议·{result.reason}】\n\n{result.content}",
        kind="suggestion",
        meta={
            "card_key": s.editing.card_key,
            "reason": result.reason,
            "suggested_text": result.content,
        },
    )
    if body.apply:
        # apply=True 直接把建议写回编辑态文本
        s.editing.current_text = result.content
        s.editing.edit_round = (s.editing.edit_round or 0) + 1
        s.editing.last_suggestion_msg_id = msg.id
    ss.save(s)
    s2 = ss.get(sid)
    return {
        "ok": True,
        "session": s2.as_dict(),
        "message_id": msg.id,
        "suggested_text": result.content,
        "reason": result.reason,
        "applied": bool(body.apply),
    }





# ============================================================
# 卷（volumes/）CRUD + 杀青归账预览/应用（M3）
# ============================================================

class VolumeCreateIn(BaseModel):
    title: str = ""
    tone_arc: str = ""
    planned_interludes: int = 3
    foreshadows_to_plant: list[str] = []
    bonds_to_deepen: list[str] = []
    gods_to_hint: list[str] = []
    contamination_goal: int = 0
    satisfaction_beats: list[str] = []
    # Task 11 扩展：支持通过 kind 创建 mainline/interlude 卷；start_chapter_no 指定起始章号
    kind: str = "dungeon"   # mainline | dungeon | interlude
    start_chapter_no: int | None = None


class VolumeUpdateIn(BaseModel):
    title: str | None = None
    tone_arc: str | None = None
    planned_interludes: int | None = None
    foreshadows_to_plant: list[str] | None = None
    bonds_to_deepen: list[str] | None = None
    gods_to_hint: list[str] | None = None
    contamination_goal: int | None = None
    satisfaction_beats: list[str] | None = None
    endgame_layer_hints: dict | None = None
    adapted_module: dict | str | None = None
    simulated_run: dict | None = None
    host_character: dict | None = None
    entry_point: dict | None = None
    info_gap: str | None = None
    stance: dict | None = None
    tangchi_story_outline: dict | str | None = None
    # Task 8：副本详设 6 卡
    death_nodes: list | None = None
    climax_nodes: list | None = None
    clue_nodes: list | None = None
    pasted_nodes: list | None = None
    scaffold_cards: dict | None = None
    stage_completed: dict | None = None


class SettlePreviewIn(BaseModel):
    wrapup_text: str


class SettleApplyIn(BaseModel):
    diff: dict


@app.get("/api/projects/{pid}/volumes")
def volumes_list(pid: str) -> dict:
    """列出所有卷（规划板摘要）。"""
    _require_project(pid)
    vs = _volumes_for(pid)
    vols = vs.all_list()
    return {"volumes": [v.as_dict() for v in vols], "total": len(vols)}


@app.get("/api/projects/{pid}/volumes/latest")
def volumes_latest(pid: str) -> dict:
    """最新卷（用于进入下一场时前端拿卷号）。"""
    _require_project(pid)
    vs = _volumes_for(pid)
    v = vs.latest()
    if v is None:
        return {"volume": None}
    return {"volume": v.as_dict()}


@app.post("/api/projects/{pid}/volumes")
def volumes_create_next(pid: str, body: VolumeCreateIn) -> dict:
    """创建新卷。默认 kind=dungeon（副本）；传 kind=mainline 创建蓝星开篇卷（自动预建 2 章）；
    kind=interlude 创建间章卷。start_chapter_no 可选（不指定则从 desk.next_available_no 开始）。
    同时兼容老接口（不传 kind 时按 dungeon 行为）。
    """
    _require_project(pid)
    vs = _volumes_for(pid)
    fs = _fields_for(pid)
    bs = _book_for(pid)
    desk = _desk_for(pid)
    kind = (body.kind or "dungeon").strip().lower()
    if kind not in ("mainline", "dungeon", "interlude"):
        kind = "dungeon"

    title = (body.title or "").strip()
    start_chapter_no = body.start_chapter_no

    if kind == "mainline":
        # 主线（蓝星开篇）永远从第 1 章开始，只允许一个 mainline 卷；若已存在则直接返回已有的
        existing_ml = [v for v in vs.all_list() if v.volume_kind == "mainline"]
        if existing_ml:
            v = existing_ml[0]
        else:
            if not title:
                title = "蓝星开篇"
            start_chapter_no = 1
            v = vs.create_new_volume(title=title, volume_kind="mainline", start_chapter_no=start_chapter_no)
            # 在 desk 中预建 2 个 mainline 章节占位（让 phase 判定直接进入 mainline-brief）
            ch_titles = ["蓝星日常·收到讣告", "讣告应验·穿越坠落"]
            for i in range(2):
                cno = start_chapter_no + i
                desk.ensure(cno, title=ch_titles[i], field_no=v.volume_no, chapter_kind="mainline")
    elif kind == "interlude":
        # dungeon / interlude：从下一个可用章号开始
        if start_chapter_no is None:
            start_chapter_no = desk.next_available_no()
        if not title:
            title = "蓝星间章"
        v = vs.create_new_volume(title=title, volume_kind="interlude", start_chapter_no=start_chapter_no)
    else:  # dungeon
        if start_chapter_no is None:
            start_chapter_no = desk.next_available_no()
        # 保留原有行为：在 field_no = latest.field_no + 1 处创建副本卷
        latest_vol = vs.latest()
        next_field = (latest_vol.field_no + 1) if latest_vol else 1
        v = vs.create_next(bs, next_field)
        # 纠正 start_chapter_no（create_next 不会设置）
        if v.start_chapter_no != start_chapter_no:
            v.start_chapter_no = start_chapter_no
            vs._flush(v)

    # 应用规划板初始字段（排除 kind/start_chapter_no）
    kwargs_raw = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict()
    kwargs = {k: val for k, val in kwargs_raw.items() if val is not None and k not in ("kind", "start_chapter_no")}
    if kwargs:
        try:
            v = vs.update_planning(v.volume_no, **kwargs)
        except ValueError:
            pass

    # 返回当前最新 phase
    try:
        from .phase_routes import _determine_phase as _dp
        ph = _dp(pid)
    except Exception:
        ph = None
    return {"volume": v.as_dict(), "ok": True, "volume_no": v.volume_no, "phase": ph}


@app.delete("/api/projects/{pid}/volumes/{vol_no}")
def volumes_delete(pid: str, vol_no: int) -> dict:
    """删除指定卷：清空该卷对应的章节 + desk 里 chapter_kind 对应的章节，再移除卷本身。
    安全约束：有章节已定稿则拒绝（force 参数可放开，但默认不暴露）。"""
    _require_project(pid)
    vs = _volumes_for(pid)
    desk = _desk_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(404, f"卷 {vol_no} 不存在")

    # 找 desk 中属于该卷的章节
    # mainline/interlude 按 chapter_kind/is_interlude 判定；dungeon 按 field_no 判定
    to_delete: list[int] = []
    for no in sorted(desk.all_nos(), reverse=True):
        ch = desk.get(no)
        if not ch:
            continue
        belong = False
        if v.volume_kind == "mainline":
            belong = (ch.chapter_kind == "mainline")
        elif v.volume_kind == "interlude":
            belong = bool(ch.is_interlude)
        else:
            belong = (ch.field_no == v.volume_no)
        if belong:
            if ch.finalized_at:
                raise HTTPException(400, f"第 {no} 章已定稿，无法删除该卷")
            to_delete.append(no)
    for no in to_delete:
        try:
            desk.delete(no, force=True)
        except ValueError:
            pass

    # 删除卷文件：索引 + 单卷 json
    if v.volume_no in getattr(vs, "_cache", {}):
        del vs._cache[v.volume_no]
    idx = vs._load_index()
    idx["volumes"] = [e for e in idx.get("volumes", []) if int(e.get("volume_no", 0)) != vol_no]
    vs._save_index(idx)
    vpath = vs._path(vol_no) if hasattr(vs, "_path") else (vs.root / f"{vol_no}.json")
    if vpath.exists():
        vpath.unlink()

    try:
        from .phase_routes import _determine_phase as _dp
        ph = _dp(pid)
    except Exception:
        ph = None
    return {"ok": True, "phase": ph}


@app.patch("/api/projects/{pid}/volumes/{vol_no}")
def volumes_update(pid: str, vol_no: int, body: VolumeUpdateIn) -> dict:
    """更新卷规划板字段。"""
    _require_project(pid)
    vs = _volumes_for(pid)
    # model_dump 默认 exclude_none，但 simulated_run 显式传 None 需要保留以清空；
    # 同时保留显式传 []/{}（清空 6 卡时需要）—— 用 model_fields_set 把所有被显式传入的字段（包括 falsey）都带进去
    kwargs = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else body.dict(exclude_none=True)
    explicit = body.model_fields_set if hasattr(body, "model_fields_set") else set()
    for k in explicit:
        v = getattr(body, k, None)
        if v is None and k == "simulated_run":
            kwargs[k] = None
        elif v is not None:
            kwargs[k] = v
        elif k in {"death_nodes", "climax_nodes", "clue_nodes",
                   "pasted_nodes", "scaffold_cards"}:
            kwargs[k] = v  # 允许传 []/{} 等空值，清卡
    try:
        v = vs.update_planning(vol_no, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"volume": v.as_dict(), "ok": True}


@app.post("/api/projects/{pid}/sessions/{sid}/settle-preview")
def session_settle_preview(pid: str, sid: str, body: SettlePreviewIn) -> dict:
    """根据 wrapup_text 抽取归账 diff（不写入）。供前端在用户修改了 wrapup 文本后
    手动重新预览 diff 时调用（锁卡时已自动返回一次 preview）。"""
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    field_no = s.field_no
    from ..pipeline.settle import extract_settle_from_wrapup
    try:
        diff = extract_settle_from_wrapup(
            body.wrapup_text or "",
            _chore_provider("settle"), _prompt_store,
            field_no=field_no,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"归账抽取失败：{e}")
    return {"ok": True, "diff": diff, "field_no": field_no}


@app.post("/api/projects/{pid}/sessions/{sid}/settle-apply")
def session_settle_apply(pid: str, sid: str, body: SettleApplyIn) -> dict:
    """前端确认归账 diff 后真正写入：
    1. EntityStore.apply_settle_diff 把 crafts/echoes/... 合进 ProtagonistState；
    2. VolumeStore.record_settle 写入最新卷/当前卷的 post_field_settle。
    """
    _require_project(pid)
    ss = _sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    field_no = s.field_no
    if field_no is None:
        raise HTTPException(status_code=422, detail="当前会话未关联 field_no")
    diff = body.diff or {}
    es = _entities_for(pid)
    try:
        summary = es.apply_settle_diff(diff, field_no)
        es._flush()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 写入当前卷（或最新卷）的 post_field_settle
    vs = _volumes_for(pid)
    target_vol_no = s.volume_no
    vol = None
    if target_vol_no is not None:
        vol = vs.get(target_vol_no)
    if vol is None:
        # 退回按 field_no 查
        vol = vs.get_by_field(field_no)
    if vol is None:
        # 再退回最新卷
        vol = vs.latest()
    settle_record = {**diff, "field_no": field_no, "applied_summary": summary}
    if vol is not None:
        vs.record_settle(vol.volume_no, settle_record)
        target_vol_no = vol.volume_no
    # 同步设置 session.outputs 里 settlement_confirmed=True，便于阶段推进
    s.set_output("settlement_confirmed", True)
    s.add_message("system",
                  f"杀青归账已应用：污染度 {summary.get('contam_before','?')}→{summary.get('contam_after','?')}，"
                  f"场数 {summary.get('field_count_before','?')}→{summary.get('field_count_after','?')}，"
                  f"新增手艺 {len(summary.get('crafts_added',[]))} 项、加深 {len(summary.get('crafts_deepened',[]))} 项，"
                  f"新增残影 {len(summary.get('echoes_added',[]))} 项。",
                  kind="gate",
                  meta={"action": "settle_apply", "diff": diff, "summary": summary,
                        "volume_no": target_vol_no})
    ss.save(s)
    return {"ok": True, "summary": summary, "volume_no": target_vol_no,
            "protagonist": es.protagonist.as_dict()}


# ============================================================
# 后台长任务路由注册（PDF×5 / COC×3 / Outline×5 / SSE 进度）
# ============================================================
from .jobs_routes import register_job_routes, register_pdf5_route

# 给 coc_runner 一个工厂（每次返回新 PipelineRunner，线程安全）
def _coc_runner_factory():
    return PipelineRunner(_chore_provider('coc'), prompt_store=_prompt_store)

register_job_routes(
    app,
    require_project=_require_project,
    sessions_for=_sessions_for,
    fields_for=_fields_for,
    field_run_for=_field_run_for,
    coc_run_for=_coc_run_for,
    book_for=_book_for,
    chore_provider=_chore_provider,
    coc_runner_factory=_coc_runner_factory,
    prompt_store=_prompt_store,
    json_dumps=json_dumps,
)
register_pdf5_route(app)

# ============================================================
# 工作台阶段判定路由（phase_routes）
# ============================================================
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

# ============================================================
# 章台管理（desk）REST：insert/delete/patch/reorder/spine
# ============================================================
from .desk_routes import register_desk_routes
register_desk_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
    entities_for=_entities_for,
)

# ============================================================
# 主线开篇卷章纲（mainline-outline）
# ============================================================
from .mainline_routes import register_mainline_routes
register_mainline_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    project_dir=_project_dir,
    chore_provider=_chore_provider,
)

# ============================================================
# 杀青归账（settle）+ 章前卡（brief）路由
# ============================================================
from .settle_routes import register_settle_routes
from .brief_routes import register_brief_routes
register_settle_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
    entities_for=_entities_for,
    chore_provider=_chore_provider,
)
register_brief_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
    chore_provider=_chore_provider,
)

# ============================================================
# 蓝星间章（interlude）路由
# ============================================================
from .interlude_routes import register_interlude_routes
register_interlude_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
    entities_for=_entities_for,
    chore_provider=_chore_provider,
)

# ============================================================
# 工作台 PDF 改编（adapt）路由
# ============================================================
from .adapt_routes import register_adapt_routes
register_adapt_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    project_dir=_project_dir,
    chore_provider=_chore_provider,
    prompt_store=_prompt_store,
)

# ============================================================
# 工作台 COC 并行跑团仿真（simulate）路由
# ============================================================
from .simulate_routes import register_simulate_routes
register_simulate_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    project_dir=_project_dir,
    book_for=_book_for,
    fields_for=_fields_for,
    coc_run_for=_coc_run_for,
    field_run_for=_field_run_for,
    chore_provider=_chore_provider,
    coc_runner_factory=_coc_runner_factory,
)

# ============================================================
# 唐迟魂穿（embody）路由
# ============================================================
from .embody_routes import register_embody_routes
register_embody_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    chore_provider=_chore_provider,
)

# ============================================================
# 副本详设（scaffold）6 卡 AI 预填 + 选版锁定
# ============================================================
from .scaffold_routes import register_scaffold_routes
register_scaffold_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    chore_provider=_chore_provider,
)

# ============================================================
# 节奏骨架（rhythm）7 天强度曲线 + 章节分配
# ============================================================
from .rhythm_routes import register_rhythm_routes
register_rhythm_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
    chore_provider=_chore_provider,
)
from .chat_routes import register as register_chat_routes
register_chat_routes(app, get_router)
