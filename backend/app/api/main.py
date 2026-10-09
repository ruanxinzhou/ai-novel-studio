"""Novel Studio API 骨架（M0）：健康检查 + 通道状态 + 路由生成测试。

M1-T5：Prompt 工坊（查看/编辑/重置/干跑）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import REPO
from ..context import Material, MaterialFamily
from ..corpus import list_tags, list_types, search as corpus_search
from ..entities import ENTITY_TYPES, TYPE_LABELS, EntityStore, extract_entities
from ..entities.vault import export_vault
from ..pipeline import (
    SPECS,
    PipelineRunner,
    PromptStore,
    RunJournal,
    compute_run_key,
    render_generation_messages,
)
from ..projects import ProjectStore
from ..providers import Router, get_router
from ..review import STATUS_PENDING, MarkerStore, review_chapter
from ..review.markers import markers_material
from ..skills import MODULES, available_modules, module_rules, skill_materials

app = FastAPI(title="Novel Studio API", version="0.4.0")

_prompt_store = PromptStore(persist_path=REPO / "backend" / "data" / "prompt_store.json")
_marker_store = MarkerStore(REPO / "backend" / "data" / "markers.json")
_project_store = ProjectStore(REPO / "backend" / "data" / "projects")

_project_marker_stores: dict[str, MarkerStore] = {}
_project_entity_stores: dict[str, EntityStore] = {}


def _markers_for(project: str | None) -> MarkerStore:
    """项目维度标记：project=None 用全局默认（兼容单书流程）。"""
    if project is None:
        return _marker_store
    if project not in _project_marker_stores:
        _project_marker_stores[project] = MarkerStore(
            REPO / "backend" / "data" / "projects" / project / "markers.json"
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
            REPO / "backend" / "data" / "projects" / pid / "entities.json"
        )
    return _project_entity_stores[pid]


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
def prompts_detail(layer: str) -> dict:
    try:
        summary = _prompt_summary(layer)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    summary["history"] = [v.as_dict() for v in _prompt_store.history(layer)]
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


_FAMILIES = {"required": MaterialFamily.REQUIRED, "finalized": MaterialFamily.FINALIZED,
             "candidate": MaterialFamily.CANDIDATE, "reference": MaterialFamily.REFERENCE}


def _journal_path(run_id: str, project: str | None = None):
    if project:
        return REPO / "backend" / "data" / "projects" / project / "runs" / f"{run_id}.json"
    return REPO / "backend" / "data" / "runs" / f"{run_id}.json"


def _router_provider(step: str):
    """把 Router（带 failover 的 step 路由）适配成 PipelineRunner 需要的 Provider。"""
    router: Router = get_router()

    class _Adapter:
        name = f"router:{step}"
        default_model = None

        @staticmethod
        def chat(messages, model=None, max_tokens=None, extra=None):
            return router.chat(step, messages, model, max_tokens)

    return _Adapter()


def _run_layer(layer: str, body: LayerRunIn) -> dict:
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
    if body.chapter_no is not None:
        marker_mat = markers_material(
            _markers_for(body.project).pending_for(body.chapter_no), body.chapter_no)
    all_mats = ([marker_mat] if marker_mat else []) + injected_skills + mats + corpus_mats
    x_list = [x for x, _ in body.x_pairs]
    run_key = compute_run_key(layer, x_list,
                              [(m.source_id, m.family.value, m.content) for m in all_mats],
                              body.user_directive)
    journal = RunJournal(_journal_path(run_key, body.project), layer, run_key)
    runner = PipelineRunner(_router_provider(layer), prompt_store=_prompt_store)
    try:
        result = runner.run_layer(spec, body.x_pairs, materials=all_mats,
                                  user_directive=body.user_directive,
                                  x_chunk_length=body.x_chunk_length, journal=journal)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成失败（已完成块已留收据）：{e}")
    return {
        "run_id": run_key,
        "layer": layer,
        "y": result.y,
        "pairs": result.pairs,
        "resumed_indices": result.resumed_indices,
        "skills_injected": [m.source_id for m in injected_skills],
        "corpus_injected": [m.source_id for m in corpus_mats],
        "markers_injected": [m.as_dict() for m in _markers_for(body.project).pending_for(body.chapter_no or 0)]
        if body.chapter_no is not None else [],
        "calls": [{"completion_status": c.completion_status, "attempts": c.attempts}
                  for c in result.calls],
        "journal": journal.summary(),
    }


@app.post("/api/runs/{layer}")
def runs_start(layer: str, body: LayerRunIn) -> dict:
    """运行一层流水线；同输入重跑自动命中收据（幂等），失败块可重试。"""
    return _run_layer(layer, body)


@app.post("/api/runs/{layer}/retry")
def runs_retry(layer: str, body: LayerRunIn) -> dict:
    """幂等重试：语义同 runs_start，completed 块不重生成，只补失败/缺失块。"""
    return _run_layer(layer, body)


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
                 keyword: str | None = None, limit: int = 5) -> dict:
    hits = corpus_search(excerpt_type=excerpt_type, tag=tag, keyword=keyword,
                         limit=max(1, min(limit, 20)))
    return {"total": len(hits), "excerpts": [e.as_dict() for e in hits]}


# ---- 逐项目标审稿（M2）----

class ChapterReviewIn(BaseModel):
    outline: str = ""
    prose: str
    model: str | None = None


@app.post("/api/review/chapter")
def review_chapter_api(body: ChapterReviewIn) -> dict:
    """六项一致性逐项审查；不稳 >= 2 判 blocked（不交稿）。"""
    router: Router = get_router()

    class _Adapter:
        name = "router:review"
        default_model = None

        @staticmethod
        def chat(messages, model=None, max_tokens=None, extra=None):
            return router.chat("review", messages, model, max_tokens)

    try:
        review = review_chapter(_Adapter(), _prompt_store, body.outline, body.prose,
                                model=body.model)
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
def markers_list(project: str | None = None) -> dict:
    return _marker_summary(project)


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
def projects_list() -> dict:
    return {"projects": _project_store.list()}


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
def project_layer(pid: str, layer: str, seq: int | None = None) -> dict:
    """层详情：seq 缺省看当前版本，否则看指定历史版本。"""
    _require_project(pid)
    if layer not in ("intro", "outline", "plot", "draft", "polish"):
        raise HTTPException(status_code=404, detail=f"未知层：{layer}")
    v = _project_store.version(pid, layer, seq) if seq else _project_store.current(pid, layer)
    return {
        "version": v.as_dict() if v else None,
        "history": [x.as_summary() for x in _project_store.history(pid, layer)],
    }


@app.post("/api/projects/{pid}/layers/{layer}")
def project_adopt(pid: str, layer: str, body: AdoptIn) -> dict:
    """采纳一层产出（生成结果人工编辑后入库，版本留痕）。"""
    _require_project(pid)
    if not body.y.strip():
        raise HTTPException(status_code=422, detail="采纳内容不能为空")
    v = _project_store.adopt(pid, layer, body.x_pairs, body.y, body.note)
    return v.as_dict()


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
                  keyword: str | None = None) -> dict:
    _require_project(pid)
    if type and type not in ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"未知实体类型：{type}")
    store = _entities_for(pid)
    found = store.list(type=type, status=status, keyword=keyword)
    return {
        "project": pid,
        "entities": [e.as_dict() for e in found],
        "counts": {t: sum(1 for e in store.entities if e.type == t)
                   for t in ENTITY_TYPES},
        "total": len(store.entities),
    }


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
    router: Router = get_router()

    class _Adapter:
        name = "router:chore"
        default_model = None

        @staticmethod
        def chat(messages, model=None, max_tokens=None, extra=None):
            return router.chat("chore", messages, model, max_tokens)

    try:
        result = extract_entities(_Adapter(), _entities_for(pid), _prompt_store,
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
                    or (REPO / "backend" / "data" / "exports" / pid))
    result = export_vault(_project_store, _entities_for(pid), pid, out_root)
    return {
        "out_dir": result.out_dir,
        "files": result.files,
        "chapters": result.chapters,
        "entities": result.entities,
        "layers_exported": result.layers_exported,
        "warnings": result.warnings,
    }
