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

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

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
from app.generation.humanize import postprocess as humanize_postprocess
from app.generation.quality import (
    HardGateResult,
    SoftReviewResult,
    build_soft_review_messages,
    hard_gate,
    parse_soft_review,
    rewrite_prompt_with_hints,
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
    # 大纲分段生成必须滚动衔接：否则每段只看简介自说自话，跨段事件重复、情节断裂
    "outline": PipelineSpec("outline", x_chunk_length=40, y_chunk_length=4_000, target_chars=1_500, anchor_enabled=True),
    # 作者统一字数要求：每章正文 4000-5000 字。
    # plot（剧情骨架）给 1800 字目标，draft（正文）给 4800 字目标（±400 浮动正好落在 4000-5000 区间），
    # polish 跟 draft 同长做全量润色，不缩不扩。
    "plot": PipelineSpec("plot", x_chunk_length=2_000, y_chunk_length=5_000, target_chars=1_800),
    "draft": PipelineSpec("draft", x_chunk_length=4_500, y_chunk_length=12_000, target_chars=4_800, anchor_enabled=True),
    "polish": PipelineSpec("polish", x_chunk_length=6_000, y_chunk_length=12_000, target_chars=5_000),
    # 《第七日的亡者》场工作流步骤（Phase 1 书化）
    "adapt_scenario":     PipelineSpec("adapt_scenario",     x_chunk_length=3_000, y_chunk_length=6_000, target_chars=2_500),
    "coc_keeper":         PipelineSpec("coc_keeper",         x_chunk_length=3_000, y_chunk_length=4_000, target_chars=1_500),
    "coc_investigator":   PipelineSpec("coc_investigator",   x_chunk_length=3_000, y_chunk_length=4_000, target_chars=1_500),
    "coc_outcome":        PipelineSpec("coc_outcome",        x_chunk_length=4_000, y_chunk_length=4_000, target_chars=2_000),
    "tang_outline":       PipelineSpec("tang_outline",       x_chunk_length=3_000, y_chunk_length=5_000, target_chars=2_500),
    "split_chapters":     PipelineSpec("split_chapters",     x_chunk_length=4_000, y_chunk_length=4_000, target_chars=2_000),
    "distill_world":      PipelineSpec("distill_world",      x_chunk_length=3_000, y_chunk_length=2_000, target_chars=1_200),
    "distill_obsession":  PipelineSpec("distill_obsession",  x_chunk_length=3_000, y_chunk_length=2_000, target_chars=1_200),
    "distill_entity":     PipelineSpec("distill_entity",     x_chunk_length=3_000, y_chunk_length=2_000, target_chars=1_200),
    "epilogue_ash":       PipelineSpec("epilogue_ash",       x_chunk_length=3_000, y_chunk_length=2_000, target_chars=1_000),
    "interlude_expected": PipelineSpec("interlude_expected", x_chunk_length=2_000, y_chunk_length=2_000, target_chars=1_000),
    "interlude_outline":  PipelineSpec("interlude_outline",  x_chunk_length=3_000, y_chunk_length=4_000, target_chars=2_000),
}


def _lexicon_root():
    """backend/data/projects 根目录（lexicon.json 与 entities.json 同级）。"""
    try:
        from .. import _config
        return _config.REPO / "backend" / "data" / "projects"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parents[2] / "data" / "projects"


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
        self.limits = limits or ModelLimits(context_window_tokens=204_800, max_output_tokens=131_072)
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
            max_tokens=self._budget_max_tokens(decision),
        )

    def _budget_max_tokens(self, decision) -> int:
        """正文预算 + reasoning 预留。

        推理模型（M3.1-Flash / DeepSeek 等）会把链锁思考写入 reasoning_tokens，
        这部分与正文共享 max_tokens 配额。若按"正文 chars × 系数"直传，reasoning
        很容易把全部额度吃完、正文一字不出（之前的空 y bug 即此）。因此：
        - 基础预算走 plan_budget 算的 output_budget_tokens；
        - 若 provider 带 reasoning_reserve 属性，额外加上。
        """
        base = max(256, decision.output_budget_tokens or 2_048)
        reserve = int(getattr(self.provider, "reasoning_reserve_tokens", 0) or 0)
        return base + reserve

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
        project_id: str | None = None,
        chapter_no: int | None = None,
    ) -> LayerRunResult:
        """on_event(kind, payload) 实时回调：thinking/delta（含 index）、
        resumed（收据复用）、block_done（单块完成）、notice（failover）、
        quality（硬门禁/软评审）、lexicon（描写重复 Top6 提示）。"""
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
                             _index=i, _extra_directive: str | None = None) -> tuple[str, str]:
                    msgs = list(_messages)
                    # 质量门禁反馈注入：如果带了 extra_directive（来自上一轮 P0/fix_hints），
                    # 追加到最后一个 user 消息末尾
                    if _extra_directive:
                        for j in range(len(msgs) - 1, -1, -1):
                            if msgs[j].get("role") == "user":
                                msgs[j] = dict(msgs[j])
                                msgs[j]["content"] = msgs[j]["content"] + "\n\n" + _extra_directive
                                break
                    if acc:
                        tail = acc[-CONTINUATION_TAIL_CHARS:]
                        msgs.append({"role": "user", "content": f"从断点处续写，不要重复已写内容：\n{tail}"})
                    elif acc is not None:
                        # 上一轮返回空正文（模型只吐 reasoning 就 stop）。用 assistant 前缀引导它直接吐正文，
                        # 再用 user 明确指令，避免再次陷入纯 reasoning。
                        msgs.append({"role": "assistant", "content": ""})
                        msgs.append({"role": "user",
                                     "content": "请直接开始输出小说正文，不要额外思考或解释。"})
                    if hasattr(self.provider, "chat_stream"):
                        return self._generate_stream(msgs, _max_tokens, _index, on_event)
                    resp = self.provider.chat(msgs, self.model, max_tokens=_max_tokens)
                    if on_event:
                        on_event("delta", {"index": _index, "text": resp.text})
                    return resp.text, proxy_finish_reason(resp.usage.output_tokens, _max_tokens)

                # === 生成→后处理→硬门禁→（正文层）软评审，P0 触发带 hints 重写（最多 2 轮）===
                quality_rounds = 0
                max_quality_rounds = 3  # 首轮 + 最多 2 次重写
                hard_review: HardGateResult | None = None
                soft_review: SoftReviewResult | None = None
                extra_directive: str | None = None
                completion = None
                # burstiness 重试护栏：单项指标连续 2 轮 P0 且无改善，则降级为 P1，避免死循环
                _burst_streak: dict[str, int] = {}
                _burst_prev: dict[str, float] = {}
                _BURST_P0_KEYS = {  # p0_msg 子串 -> stats key -> 改善方向（越大越好/越小越好）
                    # 注意：句长 CV 已撤回 P0（lieflat 实证 R=0.87 人机差异不显著），
                    # 只剩问号密度、字种密度两项真正的 P0 burstiness 指标。
                    "问号密度过低": ("question_density", "up"),
                    "字种密度过低": ("char_type_density", "up"),
                }
                # 节奏类 P0 护栏：段长阶梯/单句段瀑布。
                # 与 burstiness 不同，这类调整幅度大（76%→30%），模型常常改不动，
                # 所以只给 2 次机会（首轮+1轮重写），未改善即降级 P1 交给作者。
                _rhythm_streak: dict[str, int] = {}
                _rhythm_prev: dict[str, float] = {}
                _RHYTHM_P0_KEYS = {  # p0_msg 子串 -> rhythm stats key -> 改善方向
                    "全章只有": ("长段数(>80字)", "up"),
                    "长段但没有": ("超长段数(>120字)", "up"),
                    "连续": ("最长连续单句段", "down"),
                    "单句成段占比": ("单句成段占比", "down"),
                }

                demoted_msgs: list[str] = []

                def _apply_burstiness_guard(review: HardGateResult) -> HardGateResult:
                    """把连续未改善的 burstiness / 节奏类 P0 降级为 P1。

                    burstiness（问号/字种）：3 次机会未改善降级
                    节奏（长段/单句段）：2 次机会未改善降级（调整幅度大，模型常改不动）
                    """
                    nonlocal _burst_streak, _burst_prev, _rhythm_streak, _rhythm_prev, demoted_msgs
                    demoted_msgs.clear()   # 只保留本轮新增的降级项
                    burst = (review.stats or {}).get("burstiness") or {}
                    rhythm = (review.stats or {}).get("rhythm") or {}
                    remaining_p0: list[str] = []
                    demoted_p1: list[str] = []
                    for msg in review.p0:
                        matched = False
                        for needle, (key, direction) in _BURST_P0_KEYS.items():
                            if needle in msg:
                                cur = float(burst.get(key, 0) or 0)
                                prev = _burst_prev.get(key)
                                improved = (
                                    prev is None
                                    or (direction == "up" and cur > prev + 0.01)
                                    or (direction == "down" and cur < prev - 0.01)
                                )
                                if improved:
                                    _burst_streak[key] = 1
                                else:
                                    _burst_streak[key] = _burst_streak.get(key, 0) + 1
                                _burst_prev[key] = cur
                                if _burst_streak[key] >= 3:  # 首轮+2轮重写共3次机会
                                    _msg = (
                                        f"{msg}（已重写 {_burst_streak[key]-1} 轮未改善，降级为建议项）"
                                    )
                                    demoted_p1.append(_msg)
                                    demoted_msgs.append(_msg)
                                    matched = True
                                break
                        if not matched:
                            # 节奏类 P0（段长阶梯/单句段瀑布）：连续 2 轮未改善即降级 P1。
                            # 理由：76%→30% 是很大的调整量，模型常常改不动；
                            # 与其死循环，不如放行并把建议给作者。
                            for needle, (key, better) in _RHYTHM_P0_KEYS.items():
                                if needle in msg:
                                    cur = float(rhythm.get(key, 0) or 0)
                                    prev = _rhythm_prev.get(key)
                                    improved = (
                                        prev is None
                                        or (better == "up" and cur > prev + 0.5)
                                        or (better == "down" and cur < prev - 0.5)
                                    )
                                    if improved:
                                        _rhythm_streak[key] = 1
                                    else:
                                        _rhythm_streak[key] = _rhythm_streak.get(key, 0) + 1
                                    _rhythm_prev[key] = cur
                                    if _rhythm_streak[key] >= 2:  # 首轮+1轮重写
                                        _msg = (
                                            f"{msg}（已重写 {_rhythm_streak[key]-1} 轮未改善，"
                                            f"当前 {key}={cur}，降级为建议项——建议作者手工调整段落划分）"
                                        )
                                        demoted_p1.append(_msg)
                                        demoted_msgs.append(_msg)
                                        matched = True
                                    break
                        if not matched:
                            remaining_p0.append(msg)
                    if demoted_p1:
                        review = HardGateResult(
                            p0=remaining_p0,
                            p1=list(review.p1) + demoted_p1,
                            stats=review.stats,
                        )
                    return review

                while quality_rounds < max_quality_rounds:
                    try:
                        completion = complete_prose(
                            lambda acc, _d=extra_directive: generate(acc, _extra_directive=_d)
                        )
                    except Exception as e:
                        if journal:
                            journal.append(ChunkReceipt(index=i, x_hash=x_hash, y="",
                                                        status="failed", error=str(e)[:300]))
                        raise
                    # 活人感硬规则后处理
                    cleaned_text = humanize_postprocess(completion.text)
                    completion = ProseCompletion(
                        text=cleaned_text,
                        status=completion.status,
                        attempts=completion.attempts,
                        dedup_overlaps=completion.dedup_overlaps,
                    )
                    # 硬门禁（字数/重复率/AI 句式密度/残留标点/burstiness 等，零 token）
                    prev_tail = anchor_text[-200:] if anchor_text else ""
                    hard_review = hard_gate(
                        completion.text, prev_tail=prev_tail,
                        target_chars=spec.target_chars,
                        stage=spec.name,
                    )
                    # burstiness 重试护栏：单项连续未改善则降级，防止死循环
                    hard_review = _apply_burstiness_guard(hard_review)
                    burst_stats = (hard_review.stats or {}).get("burstiness") or {}

                    # 描写轮换账本：本轮正文记入账本（供下一章生成前注入 + 本章 Top6 提示）
                    if project_id and chapter_no:
                        try:
                            from ..generation.lexicon import load_lexicon
                            lex = load_lexicon(project_id, _lexicon_root())
                            rec = lex.record_chapter(completion.text, chapter_no)
                            if rec.get("recorded"):
                                tops = lex.top_n_for_chapter(chapter_no)
                                if on_event:
                                    if tops:
                                        on_event("lexicon", {
                                            "index": i, "chapter": chapter_no,
                                            "top": tops,
                                            "report": lex.report_block(chapter_no),
                                        })
                                    else:
                                        on_event("notice", {
                                            "text": f"[描写账本] 第{chapter_no}章记入 {rec['recorded']} 个高频表达"})
                        except Exception as lex_e:
                            log.debug("lexicon record failed: %s", lex_e)

                    if on_event:
                        # retry 状态透传：让前端能显示「第 N/M 轮重写」+ 各护栏计数
                        on_event("quality", {
                            "index": i, "round": quality_rounds,
                            "max_rounds": max_quality_rounds,
                            "kind": "hard", "p0": hard_review.p0, "p1": hard_review.p1,
                            "stats": hard_review.stats,
                            "burstiness": burst_stats,
                            "rhythm": (hard_review.stats or {}).get("rhythm") or {},
                            "retry": {
                                "round": quality_rounds,
                                "max_rounds": max_quality_rounds,
                                "passed": hard_review.passed,
                                "burst_streak": dict(_burst_streak),
                                "rhythm_streak": dict(_rhythm_streak),
                                "burst_current": dict(_burst_prev),
                                "rhythm_current": dict(_rhythm_prev),
                            },
                        })
                    if hard_review.passed:
                        break
                    quality_rounds += 1
                    if quality_rounds >= max_quality_rounds:
                        break
                    # 把 P0 问题拼成 extra_directive 下一轮注入
                    extra_directive = "【质量门禁问题】\n" + "\n".join(f"- {x}" for x in hard_review.p0)
                    # 如果本轮有 burstiness 相关 P0/P1，额外注入具体修复方向
                    burst_hints: list[str] = []
                    for msg in hard_review.p0 + hard_review.p1:
                        if "问号密度" in msg or "问号偏少" in msg:
                            burst_hints.append("在内心戏/对白处加 3-5 个反问句或问句（自然出现，不要每段都问；人类稿问号是 AI 的 17 倍）")
                        elif "字种密度" in msg:
                            burst_hints.append("用具体名词/动词替换套话（不要用「仿佛/似乎/某种」模糊词，直接写具体物件与动作）")
                        elif "具体数字偏少" in msg:
                            burst_hints.append("每 500 字加 1-2 个具体数字（房号/楼层/年龄/时间/价格/次数/公里数），替换「很久/不少/很多」")
                        elif "时间锚点偏少" in msg:
                            burst_hints.append("加入具体时间词（三点钟/第二天/上周/秒/分钟/小时），不要全用「一会儿/后来/不久」")
                        elif "拟人化" in msg:
                            burst_hints.append("删掉「像一位智慧的导师」这类带褒义拟人化喻体，改写为具体动作或感官")
                        elif "零回指" in msg:
                            burst_hints.append("段首评论句前补「但/可/这」等指示代词，不要让评价直接悬空起笔")
                        elif "全章只有" in msg and "长段" in msg:
                            burst_hints.append(
                                "补写 2-3 段 >80 字的长段（其中至少 1 段 >120 字）承担描写/心理/环境——"
                                "把相邻的短段合并扩写成长段，不要靠继续拆短句")
                        elif "长段但没有" in msg:
                            burst_hints.append("把最长的那段扩到 120 字以上，真正承担环境描写或心理展开")
                        elif "连续" in msg and "单句成段" in msg:
                            burst_hints.append(
                                "把连续的单句段合并——每 2-3 个短句并成一段，"
                                "单句成段只保留在情绪顶点/打断/转场锚点处（最多连续 2 段）")
                        elif "单句成段占比" in msg:
                            burst_hints.append(
                                "全章至少有 70% 的段落要包含 2 句以上——"
                                "把拆散的短句合回去，让段落有内部层次")
                    if burst_hints:
                        extra_directive += "\n\n【burstiness 具体修复方向】\n" + "\n".join(f"- {x}" for x in burst_hints)
                    extra_directive += "\n\n请重写这一段，修正以上问题，严格保留剧情骨架。"
                    if on_event:
                        _p0_joined = "；".join(hard_review.p0)[:80]
                        on_event("notice", {
                            "text": f"[质量门禁] 第 {i} 块重写第 {quality_rounds}/{max_quality_rounds - 1} 轮：{_p0_joined}",
                            "retry_round": quality_rounds,
                            "max_rounds": max_quality_rounds - 1,
                            "p0": hard_review.p0,
                            "burst_streak": dict(_burst_streak),
                            "rhythm_streak": dict(_rhythm_streak),
                        })
                        # 护栏降级提示（明确告诉用户「已放弃自动重试，改为建议」）
                        for _d in demoted_msgs:
                            on_event("notice", {"text": f"[护栏降级] {_d}"})

                # 正文/润色层：软评审（LLM 13 维打分，1 次非流式调用）。
                # 仅在硬门禁通过后才做；P0 问题再触发一轮带 fix_hints 的重写。
                if (not hard_review or hard_review.passed) and spec.name in ("draft", "polish") and completion:
                    try:
                        plot_brief = chunk.x_chunk  # x_chunk 即本块的剧情输入
                        sr_msgs = build_soft_review_messages(
                            plot_brief=plot_brief, prev_tail=anchor_text or "",
                            text=completion.text,
                        )
                        # 关闭 reasoning（评审用 JSON 模式不需要深思考），用短输出
                        sr_resp = self.provider.chat(
                            sr_msgs, model=self.model, max_tokens=1200,
                            extra={"reasoning_effort": "low"},
                        )
                        soft_review = parse_soft_review(sr_resp.text)
                        if on_event:
                            on_event("quality", {
                                "index": i, "kind": "soft",
                                "total": soft_review.total,
                                "scores": soft_review.scores,
                                "p0": soft_review.p0, "p1": soft_review.p1,
                                "burstiness": burst_stats,
                            })
                        if (not soft_review.passed) and quality_rounds < max_quality_rounds:
                            quality_rounds += 1
                            extra_directive = rewrite_prompt_with_hints(
                                extra_directive or "", soft_review
                            )
                            if on_event:
                                on_event("notice", {"text": f"[质量评审] 第 {i} 块 {soft_review.total} 分"
                                                           f"（P0 {len(soft_review.p0)} 项），带提示重写"})
                            try:
                                completion2 = complete_prose(
                                    lambda acc, _d=extra_directive: generate(acc, _extra_directive=_d)
                                )
                                cleaned2 = humanize_postprocess(completion2.text)
                                # 重写后再跑一次硬门禁，仍 P0 就保留原版本
                                hard2 = hard_gate(cleaned2, prev_tail=anchor_text[-200:] if anchor_text else "",
                                                  target_chars=spec.target_chars, stage=spec.name)
                                if hard2.passed and len(cleaned2.strip()) > len(completion.text) * 0.5:
                                    completion = ProseCompletion(
                                        text=cleaned2, status=completion2.status,
                                        attempts=completion.attempts + completion2.attempts,
                                        dedup_overlaps=completion.dedup_overlaps,
                                    )
                                    burst_stats2 = (hard2.stats or {}).get("burstiness") or {}
                                    if on_event:
                                        on_event("quality", {
                                            "index": i, "kind": "soft_rewrite",
                                            "burstiness": burst_stats2,
                                        })
                                        on_event("notice", {"text": f"[质量评审] 重写完成"})
                            except Exception as re_e:
                                log.warning("soft-review rewrite failed: %s", re_e)
                    except Exception as e:
                        log.warning("soft review skipped for block %d: %s", i, e)
                        soft_review = SoftReviewResult(error=str(e))

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
        """流式调用：thinking/delta 实时回调，同时聚合正文与真实 finish_reason。"""
        parts: list[str] = []
        usage_total = 0
        finish_reason: str | None = None
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
                # MiniMax 等通道在 usage/done 的 meta 里带真实 finish_reason
                fr = (ev.meta or {}).get("finish_reason")
                if fr:
                    finish_reason = fr
            elif ev.kind == "done":
                fr = (ev.meta or {}).get("finish_reason")
                if fr:
                    finish_reason = fr
        # 优先用通道真实 finish_reason；通道未提供时回退到 token 比例代理判断
        if finish_reason:
            return "".join(parts), finish_reason
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
