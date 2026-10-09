"""Novel Studio API 骨架（M0）：健康检查 + 通道状态 + 路由生成测试。

M1-T5：Prompt 工坊（查看/编辑/重置/干跑）。
"""
from __future__ import annotations

import json
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import REPO
from ..context import Material, MaterialFamily
from ..corpus import list_tags, list_types, search as corpus_search
from ..pipeline import (
    SPECS,
    PipelineRunner,
    PromptStore,
    RunJournal,
    compute_run_key,
    render_generation_messages,
)
from ..providers import Router, get_router
from ..skills import MODULES, available_modules, module_rules, skill_materials

app = FastAPI(title="Novel Studio API", version="0.2.0")

_prompt_store = PromptStore(persist_path=REPO / "backend" / "data" / "prompt_store.json")


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


_FAMILIES = {"required": MaterialFamily.REQUIRED, "finalized": MaterialFamily.FINALIZED,
             "candidate": MaterialFamily.CANDIDATE, "reference": MaterialFamily.REFERENCE}


def _journal_path(run_id: str):
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
    all_mats = injected_skills + mats + corpus_mats
    x_list = [x for x, _ in body.x_pairs]
    run_key = compute_run_key(layer, x_list,
                              [(m.source_id, m.family.value, m.content) for m in all_mats],
                              body.user_directive)
    journal = RunJournal(_journal_path(run_key), layer, run_key)
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
