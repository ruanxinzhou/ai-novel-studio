"""五条流水线引擎（M1-T2/T5）。

简介 → 大纲 → 剧情 → 正文 → 润色，全部共享 xy_pairs 骨架与同一套
装配→预算→有界生成→回填流程。层间继承规则：
- 下一层的 x = 上一层 y 的对应展开源（大纲→剧情→正文逐层具体化）
- 全局上下文（简介/大纲）作为定稿材料进入后续层的装配快照
- 正文层启用滚动锚点：本层上一章结尾（句边界截断）作为时点锚点必进

T5：层指令来自 PromptStore（工坊可编辑），渲染走隐藏合同管线；
dry_run 提供只渲染不调用的干跑调试。

单块顺序生成；并发壳与批量断点在 T9 接入。
Provider 无 finish_reason，用 output_tokens ≥ 0.95×max_tokens 代理判定截断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.chunking import XYWriter
from app.context import Material, MaterialAssembler, MaterialFamily
from app.generation import (
    COMPLETE,
    BudgetDecision,
    BudgetRequest,
    ModelLimits,
    ProseCompletion,
    complete_prose,
    plan_budget,
)
from app.pipeline.journal import ChunkReceipt, RunJournal, x_fingerprint
from app.pipeline.prompts import PromptStore, RenderedPrompt, render_generation_messages
from app.providers.base import Provider

FINISH_LENGTH_RATIO = 0.95
CONTINUATION_TAIL_CHARS = 500


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    x_chunk_length: int
    y_chunk_length: int
    target_chars: int
    anchor_enabled: bool = False


SPECS: dict[str, PipelineSpec] = {
    "intro": PipelineSpec("intro", x_chunk_length=200, y_chunk_length=800, target_chars=400),
    "outline": PipelineSpec("outline", x_chunk_length=40, y_chunk_length=4_000, target_chars=1_500),
    "plot": PipelineSpec("plot", x_chunk_length=2_000, y_chunk_length=4_000, target_chars=1_200),
    "draft": PipelineSpec("draft", x_chunk_length=4_000, y_chunk_length=6_000, target_chars=2_000, anchor_enabled=True),
    "polish": PipelineSpec("polish", x_chunk_length=6_000, y_chunk_length=6_000, target_chars=2_000),
}


def proxy_finish_reason(response_usage_output_tokens: int, max_tokens: int) -> str:
    return "length" if response_usage_output_tokens >= int(max_tokens * FINISH_LENGTH_RATIO) else "stop"


@dataclass
class CallLog:
    messages: list[dict]
    decision: BudgetDecision
    completion_status: str
    text: str
    attempts: int


@dataclass
class LayerRunResult:
    layer: str
    pairs: list[tuple[str, str]]
    calls: list[CallLog] = field(default_factory=list)
    snapshots: list = field(default_factory=list)
    resumed_indices: list[int] = field(default_factory=list)  # 从收据复用、未调 LLM 的块

    @property
    def y(self) -> str:
        return "".join(y for _, y in self.pairs)

    @property
    def total_attempts(self) -> int:
        return sum(c.attempts for c in self.calls)


@dataclass
class PreparedChunk:
    chunk: object
    rendered: RenderedPrompt
    decision: BudgetDecision
    max_tokens: int


class PipelineRunner:
    def __init__(
        self,
        provider: Provider,
        prompt_store: PromptStore | None = None,
        model: str | None = None,
        limits: ModelLimits | None = None,
        assembler_max_bytes: int = 200_000,
    ):
        self.provider = provider
        self.prompts = prompt_store or PromptStore()
        self.model = model or getattr(provider, "default_model", None) or ""
        self.limits = limits or ModelLimits(context_window_tokens=32_000, max_output_tokens=8_192)
        self.assembler = MaterialAssembler(max_bytes=assembler_max_bytes)

    def _prepare_one(
        self,
        spec: PipelineSpec,
        writer: XYWriter,
        chunk,
        base_materials: list[Material],
        user_directive: str | None,
        anchor_text: str,
    ) -> PreparedChunk:
        """装配+渲染+预算，产出待生成的窗口（run_layer 与 dry_run 共用）。"""
        snapshot = self.assembler.assemble(
            base_materials, anchor_text=anchor_text if spec.anchor_enabled else ""
        )
        rendered = render_generation_messages(
            self.prompts, spec.name, chunk.x_chunk, snapshot.render_sections(),
            user_directive, omissions=snapshot.omissions,
        )
        decision = plan_budget(
            BudgetRequest(prompt_bytes=rendered.prompt_bytes, target_output_chars=spec.target_chars),
            self.limits,
        )
        return PreparedChunk(
            chunk=chunk,
            rendered=rendered,
            decision=decision,
            max_tokens=max(256, decision.output_budget_tokens or 2_048),
        )

    def _base_materials(self, spec: PipelineSpec, materials: list[Material] | None) -> list[Material]:
        layer_instruction_mat = Material(
            source_id=f"layer:{spec.name}",
            family=MaterialFamily.REQUIRED,
            content=self.prompts.effective(spec.name),
            title=f"{spec.name} 写作指令",
        )
        return [layer_instruction_mat] + list(materials or [])

    def dry_run(
        self,
        spec: PipelineSpec,
        x_pairs: list[tuple[str, str]],
        materials: list[Material] | None = None,
        initial_anchor: str = "",
        user_directive: str | None = None,
        x_chunk_length: int | None = None,
    ) -> list[PreparedChunk]:
        """只渲染不调用：工坊调试用，返回每个窗口最终发出的 messages。

        干跑没有真实生成，滚动锚点一律用 initial_anchor（首个窗口语义）。
        """
        writer = XYWriter(
            [(x, "") for x, _ in x_pairs],
            x_chunk_length=x_chunk_length or spec.x_chunk_length,
            y_chunk_length=spec.y_chunk_length,
        )
        base_materials = self._base_materials(spec, materials)
        return [
            self._prepare_one(spec, writer, chunk, base_materials, user_directive, initial_anchor)
            for chunk in writer.get_chunks()
        ]

    def run_layer(
        self,
        spec: PipelineSpec,
        x_pairs: list[tuple[str, str]],
        materials: list[Material] | None = None,
        initial_anchor: str = "",
        user_directive: str | None = None,
        x_chunk_length: int | None = None,
        journal: RunJournal | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> LayerRunResult:
        """on_event(kind, payload) 实时回调：thinking/delta（含 index）、
        resumed（收据复用）、block_done（单块完成）、notice（failover）。"""
        writer = XYWriter(
            [(x, "") for x, _ in x_pairs],
            x_chunk_length=x_chunk_length or spec.x_chunk_length,
            y_chunk_length=spec.y_chunk_length,
        )
        chunks = writer.get_chunks()
        base_materials = self._base_materials(spec, materials)

        calls: list[CallLog] = []
        snapshots: list = []
        edited: list = []
        resumed: list[int] = []
        anchor_text = initial_anchor

        for i, chunk in enumerate(chunks):
            # 逐块准备：滚动锚点依赖上一块的生成结果，无法预渲染
            prep = self._prepare_one(spec, writer, chunk, base_materials, user_directive, anchor_text)
            chunk, rendered, decision, max_tokens = prep.chunk, prep.rendered, prep.decision, prep.max_tokens
            snapshots.append(rendered)
            x_hash = x_fingerprint(chunk.x_chunk)

            receipt = journal.match(i, x_hash) if journal else None
            if receipt is not None:
                # 断点复用：输入一致且已完成，不再调用 LLM
                completion = ProseCompletion(
                    text=receipt.y, status=receipt.completion_status or COMPLETE,
                    attempts=0, dedup_overlaps=0,
                )
                resumed.append(i)
                if on_event:
                    on_event("resumed", {"index": i})
            else:
                def generate(acc: str | None, _messages=rendered.messages, _max_tokens=max_tokens,
                             _index=i) -> tuple[str, str]:
                    msgs = list(_messages)
                    if acc:
                        tail = acc[-CONTINUATION_TAIL_CHARS:]
                        msgs.append({"role": "user", "content": f"从断点处续写，不要重复已写内容：\n{tail}"})
                    if hasattr(self.provider, "chat_stream"):
                        return self._generate_stream(msgs, _max_tokens, _index, on_event)
                    resp = self.provider.chat(msgs, self.model, max_tokens=_max_tokens)
                    if on_event:
                        on_event("delta", {"index": _index, "text": resp.text})
                    return resp.text, proxy_finish_reason(resp.usage.output_tokens, _max_tokens)

                try:
                    completion = complete_prose(generate)
                except Exception as e:
                    # 失败也落收据：重试时知道此块曾失败，completed 块不受影响
                    if journal:
                        journal.append(ChunkReceipt(index=i, x_hash=x_hash, y="",
                                                    status="failed", error=str(e)[:300]))
                    raise
                if journal:
                    journal.append(ChunkReceipt(
                        index=i, x_hash=x_hash, y=completion.text, status="completed",
                        completion_status=completion.status, attempts=completion.attempts,
                    ))

            if on_event and receipt is None:
                on_event("block_done", {"index": i, "status": completion.status,
                                        "chars": len(completion.text)})
            edited.append(chunk.edit(y_chunk=completion.text))
            if spec.anchor_enabled:
                anchor_text = completion.text
            calls.append(
                CallLog(messages=rendered.messages, decision=decision, completion_status=completion.status,
                        text=completion.text, attempts=completion.attempts)
            )

        writer.apply_chunks(chunks, edited)
        return LayerRunResult(layer=spec.name, pairs=list(writer.xy_pairs), calls=calls,
                              snapshots=snapshots, resumed_indices=resumed)

    def _generate_stream(self, msgs: list[dict], max_tokens: int, index: int,
                         on_event: Callable[[str, dict[str, Any]], None] | None) -> tuple[str, str]:
        """流式调用：thinking/delta 实时回调，同时聚合出正文与截断判定。"""
        parts: list[str] = []
        usage_total = 0
        for ev in self.provider.chat_stream(msgs, self.model, max_tokens=max_tokens):
            if ev.kind == "delta":
                parts.append(ev.text)
                if on_event:
                    on_event("delta", {"index": index, "text": ev.text})
            elif ev.kind == "thinking":
                if on_event:
                    on_event("thinking", {"index": index, "text": ev.text})
            elif ev.kind == "notice":
                if on_event:
                    on_event("notice", {"text": ev.text})
            elif ev.kind == "usage" and ev.usage:
                usage_total = ev.usage.output_tokens
        return "".join(parts), proxy_finish_reason(usage_total, max_tokens)


def _finalized(source_id: str, content: str, title: str, keywords: tuple[str, ...] = ()) -> Material:
    return Material(source_id=source_id, family=MaterialFamily.FINALIZED, content=content,
                    title=title, keywords=keywords)


def split_outline_into_chapters(outline_text: str, n_chapters: int) -> list[str]:
    from app.chunking import split_text_into_chunks

    return split_text_into_chunks(
        outline_text,
        max_chunk_size=10**9,
        min_chunk_n=n_chapters,
        max_chunk_n=n_chapters,
        min_chunk_size=1,
    )


def build_novel(
    runner: PipelineRunner,
    idea: str,
    n_chapters: int = 3,
    user_directive: str | None = None,
) -> dict[str, LayerRunResult]:
    """完整链路：简介 → 大纲（拆章）→ 剧情 → 正文 → 润色。"""
    intro = runner.run_layer(SPECS["intro"], [(idea, "")], user_directive=user_directive)
    intro_text = intro.y
    intro_mat = _finalized("intro", intro_text, "故事简介")

    # 大纲层 x 用章节标签（LNGPT 链式设计）：给模型待填充的骨架，
    # 简介作为定稿材料进上下文——直接把成文当 x 会导致模型复读而非扩写
    outline = runner.run_layer(
        SPECS["outline"],
        [(f"第{i + 1}章\n", "") for i in range(n_chapters)],
        materials=[intro_mat],
        x_chunk_length=40,
        user_directive=user_directive or f"共 {n_chapters} 章。",
    )
    outline_chapters = split_outline_into_chapters(outline.y, n_chapters)
    outline_mats = [
        _finalized(f"outline:ch{i + 1}", text, f"第{i + 1}章大纲") for i, text in enumerate(outline_chapters)
    ]

    # 每章一块：x_chunk_length 取最短章长，保证 0.8×步长不越过任何一章的边界
    def per_chapter_length(texts: list[str]) -> int:
        return max(1, min(len(t) for t in texts))

    plot = runner.run_layer(
        SPECS["plot"],
        [(text, "") for text in outline_chapters],
        materials=[intro_mat],
        x_chunk_length=per_chapter_length(outline_chapters),
        user_directive=user_directive,
    )

    draft = runner.run_layer(
        SPECS["draft"],
        [(y, "") for _, y in plot.pairs],
        materials=outline_mats,
        x_chunk_length=per_chapter_length([y for _, y in plot.pairs]),
        user_directive=user_directive,
    )

    polish = runner.run_layer(
        SPECS["polish"],
        [(y, "") for _, y in draft.pairs],
        x_chunk_length=per_chapter_length([y for _, y in draft.pairs]),
        user_directive=user_directive,
    )

    return {"intro": intro, "outline": outline, "plot": plot, "draft": draft, "polish": polish}
