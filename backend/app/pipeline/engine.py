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
from app.generation.guard import guarded_postprocess
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
    # 跳过硬门禁重试（第三步）：跑团层用它把单拍调用数从最坏 3× 压回 1×。
    #
    # 为什么跑团层可以跳：硬门禁（字数/重复率/AI 句式/burstiness）在正文层
    # 是质量保证，但在跑团层是**纯浪费**——这一层的产物是决策记录，
    # 后面还要过梗概和通稿两道重写，门禁在这里判 P0 重写毫无意义，
    # 却让每次拍的成本翻三倍（实测是慢的主要来源）。
    #
    # 跳过后仍然保留：hard_gate 的**检测**（供作者看到「这一拍有问题」），
    # 只是不再触发重写。
    skip_quality_retry: bool = False


SPECS: dict[str, PipelineSpec] = {
    "intro": PipelineSpec("intro", x_chunk_length=200, y_chunk_length=800, target_chars=400),
    # 大纲分段生成必须滚动衔接：否则每段只看简介自说自话，跨段事件重复、情节断裂
    "outline": PipelineSpec("outline", x_chunk_length=40, y_chunk_length=4_000, target_chars=1_500, anchor_enabled=True),
    # 作者统一字数要求：每章正文 4000-5000 字。
    # plot（剧情骨架）给 1800 字目标，draft（正文）给 4800 字目标（±400 浮动正好落在 4000-5000 区间），
    # polish 跟 draft 同长做全量润色，不缩不扩。
    # plot层是**章细纲**（编号场景块施工图），不是正文。
    # y_chunk_length 给到 5000 是错的——那个预算本意是容纳正文，
    # 但它会诱导模型把细纲写长（实测第2章写出了 5446 字正文）。
    # 细纲上限 2600 字门禁，给 3200 的输出预算留足余量又不会诱发长文。
    "plot": PipelineSpec("plot", x_chunk_length=2_000, y_chunk_length=3_200, target_chars=1_800),
    "draft": PipelineSpec("draft", x_chunk_length=4_500, y_chunk_length=12_000, target_chars=4_800, anchor_enabled=True),
    "polish": PipelineSpec("polish", x_chunk_length=6_000, y_chunk_length=12_000, target_chars=5_000),
    # 《第七日的亡者》场工作流步骤（Phase 1 书化）
    "adapt_scenario":     PipelineSpec("adapt_scenario",     x_chunk_length=3_000, y_chunk_length=6_000, target_chars=2_500),
    "coc_keeper":         PipelineSpec("coc_keeper",         x_chunk_length=3_000, y_chunk_length=2_000, target_chars=600, skip_quality_retry=True),
    "coc_investigator":   PipelineSpec("coc_investigator",   x_chunk_length=3_000, y_chunk_length=1_200, target_chars=300, skip_quality_retry=True),
    # coc_beat = KP 裁决 + PL 动作声明，**一次调用出一拍**（第四步合并）。
    #
    # 为什么合并：旧实现里 PL 拍是在已知判定结果的前提下补一句「他为什么这么做」
    # ——这不是决策，是事后合理化。两次调用之间还可能自相矛盾（KP 说 A、
    # PL 当作 B 来反应）。合并后因果在同一上下文里想，更连贯。
    #
    # 为什么仍保留逐拍：一次性生成 8-10 拍会让模型跳过中段、跳掉伏笔回收。
    # 逐拍强制推进才是「每拍都必须有信息增量」的保证。
    "coc_beat":           PipelineSpec("coc_beat",           x_chunk_length=8_000, y_chunk_length=2_400, target_chars=700, skip_quality_retry=True),
    # coc_seed = 一次性生成三条支线的差异化设定（方案B）。
    #
    # 为什么需要这一层：旧实现从**硬编码的通用池子**（TWIST_POOL /
    # FORESHADOW_POOL / 五种结局标签）里随机抽，素材写死在代码里，
    # 与当前副本核毫无关系——作者会看到「黑暗结局：巷子里多了一把伞」
    # 这种与剧本无关的硬编码标签。更糟的是它**限制 KP**：让 KP 去凑一个
    # 预设结局，而不是让故事自己长出来。
    #
    # 这一层改为：读副本核 + NPC 卡司，由 LLM 现场生成三份差异化设定。
    # 素材从剧本长出来，不从代码池子里抽。
    "coc_seed":           PipelineSpec("coc_seed",           x_chunk_length=8_000, y_chunk_length=3_000, target_chars=1_600, skip_quality_retry=True),
    "coc_outcome":        PipelineSpec("coc_outcome",        x_chunk_length=4_000, y_chunk_length=4_000, target_chars=2_000),
    # 通稿层：一篇讲完的连续故事，不是分段梗概。x 要喂全部 beat 结构化
    # 记录 + 全量叙事描写（远大于 coc_outcome 的单段），y 给足一次成稿的量。
    # 8000-12000 字是硬要求，target_chars 设 10_000，y_chunk_length 给 14_000
    # 留冗余，避免被截断后拉一个半截稿进 full_narrative。
    "coc_narrative":      PipelineSpec("coc_narrative",      x_chunk_length=32_000, y_chunk_length=14_000, target_chars=10_000, anchor_enabled=True),
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


# 空产出后的重试引导语，按层给。
# 关键：不能统一写「输出小说正文」——plot 层收到这句会被明确要求写正文，
# 直接盖掉【产物格式】里「第N场 + 字段行」的形态要求。
#
# 还要明确说「上一轮全程在思考、没落笔」：推理模型看不到自己上轮把 max_tokens
# 花在哪，不点破它会从头再推敲一遍，几个来回下来一个 token 都没出。
_EMPTY_OUTPUT_DIRECTIVE: dict[str, str] = {
    "plot": (
        "上一轮你把全部推理额度都用完了，一个字的产物都没输出。\n"
        "**不要再继续推敲，直接落笔。**现在开始输出章细纲，"
        "第一个字符就是「第1场：」，随后按「- 场景：」「- POV：」「- 微观目标：」"
        "「- 阻力：」「- 信息差：」「- 关键对白：」「- 关键动作：」「- 感官锚点：」"
        "「- 情绪四拍：」「- 落点：」「- 转场桥：」「- 本场变化：」逐行填写。\n"
        "全章 2-8 场，场数由内容决定；同一时空的连续事件用「转场桥：同场续」+「本场变化」区分。\n"
        "不要输出任何正文段落、不要写场景描述、不要加解释或前后缀。"
    ),
    "outline": (
        "上一轮你把全部推理额度都用完了，一个字的产物都没输出。\n"
        "**不要再继续推敲，直接落笔。**现在输出章节大纲，"
        "第一段以章节标签原文开头（如「第1章」），逐章往下写。\n"
        "不要输出解释、不要写成连续故事正文。"
    ),
    "draft": (
        "上一轮你把全部推理额度都用完了，一个字的正文都没输出。\n"
        "**不要再继续推敲，直接开始写正文。**"
    ),
    "polish": (
        "上一轮你把全部推理额度都用完了，一个字的正文都没输出。\n"
        "**不要再继续推敲，直接输出润色后的完整正文。**"
    ),
}


# reasoning 预留按层给（token）。缺省走 provider 的 reasoning_reserve_tokens。
# 结构层产物只有一两千字，给正文层那套24576 预留等于放任模型反复推敲：
# 实测 MiniMax 会花掉全部额度、finish_reason=length，正文一个 token 都没吐出，
# 门禁只能报「正文为空」；下一轮又从头思考一遍，反复几分钟。
_REASONING_RESERVE_BY_LAYER: dict[str, int] = {
    "plot": 8_192,       # 章细纲：结构清晰、不需要长链锁推理
    "outline": 8_192,    # 章纲：同上
    "import_outline": 4_096,
    "entities": 4_096,
    "foreshadow": 4_096,
    "review": 8_192,
    "refine": 8_192,
    "fix": 8_192,
    # draft / polish 未单独配置 → 使用 provider.reasoning_reserve_tokens 默认值（正文需要完整推敲）
}


def _layer_extra(layer: str) -> dict | None:
    """透传层名给 provider，用于按层选 reasoning_effort。"""
    return {"_layer": layer} if layer else None


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
    # spec 4：重试用尽后仍未通过的 P0。**不静默放行**——把决定权交回作者。
    # 这里只记录不阻断：引擎跑在后台线程里，挂起等确认需要 job 状态机 +
    # 超时 + 断线恢复，成本远大于收益；而「结果先给、决定后补」在 UX 上等价。
    gate_pending: list[str] = field(default_factory=list)
    gate_demoted: list[str] = field(default_factory=list)

    @property
    def y(self) -> str:
        return "".join(y for _, y in self.pairs)

    @property
    def total_attempts(self) -> int:
        return sum(c.attempts for c in self.calls)

    @property
    def gate_required(self) -> bool:
        """是否需要作者表态（spec 4）。"""
        return bool(self.gate_pending)


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
            max_tokens=self._budget_max_tokens(decision, spec),
        )

    def _budget_max_tokens(self, decision, spec: PipelineSpec | None = None) -> int:
        """正文预算 + reasoning 预留。

        推理模型（M3.1-Flash / DeepSeek 等）会把链锁思考写入 reasoning_tokens，
        这部分与正文共享 max_tokens 配额。若按"正文 chars × 系数"直传，reasoning
        很容易把全部额度吃完、正文一字不出（之前的空 y bug 即此）。因此：
        - 基础预算走 plan_budget 算的 output_budget_tokens；
        - 若 provider 带 reasoning_reserve 属性，额外加上。

        **预留按层给**（见 _REASONING_RESERVE_BY_LAYER）：结构层产物只有一两千字，
        给 24576 的预留等于给它 30 倍正文的思考额度——实测 MiniMax 会把这额度
        花在反复推敲上，几分钟后 finish_reason=length，正文一个 token 都没吐出。
        结构层不需要长思考，8k 足够。
        """
        base = max(256, decision.output_budget_tokens or 2_048)
        if spec is not None:
            reserve = _REASONING_RESERVE_BY_LAYER.get(
                spec.name, int(getattr(self.provider, "reasoning_reserve_tokens", 0) or 0))
        else:
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
        gate_pending: list[str] = []      # spec 4：重试用尽仍存在的非格式 P0
        gate_demoted: list[str] = []      # spec 4：被护栏降级的项（让作者知道发生过降级）
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
                        # 上一轮返回空产物（模型只吐 reasoning 就 stop，或被上游过载吞掉）。
                        # 用 assistant 前缀引导它直接吐产物，再用 user 明确指令，避免再次陷入纯 reasoning。
                        # **按层给引导语**：原来这句硬编码成「输出小说正文」，
                        # plot 层收到等于被明确要求写正文，会直接盖掉【产物格式】的场块要求。
                        msgs.append({"role": "assistant", "content": ""})
                        msgs.append({"role": "user", "content": _EMPTY_OUTPUT_DIRECTIVE.get(
                            spec.name, "请直接开始输出本层要求的产物，不要额外思考或解释。")})
                    if hasattr(self.provider, "chat_stream"):
                        return self._generate_stream(msgs, _max_tokens, _index, on_event,
                                                    layer=spec.name)
                    resp = self.provider.chat(msgs, self.model, max_tokens=_max_tokens)
                    if on_event:
                        on_event("delta", {"index": _index, "text": resp.text})
                    return resp.text, proxy_finish_reason(resp.usage.output_tokens, _max_tokens)

                # === 生成→后处理→硬门禁→（正文层）软评审，P0 触发带 hints 重写（最多 2 轮）===
                quality_rounds = 0
                max_quality_rounds = 3  # 首轮 + 最多 2 次重写
                # P1 达到几项才触发重写（2026-10-24）。
                # 1-2 项是吹毛求疵（字数偏长、逗号偏多），不值得多烧一次模型调用；
                # ≥3 项说明这一版整体偏了（数字/时间/长段/字种密度同时不及格），该修。
                P1_REWRITE_THRESHOLD = 3
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

                # 格式类 P0 绝不可降级：它判的是「输出形态错了」（细纲写成了正文），
                # 不是「写得好不好」。子串匹配会误伤——例如「章细纲格式错误：…不能写成
                # 连续散文…」里的「连续」会命中下面的节奏键，把格式错误降级成建议项，
                # 于是 1654 字的散文细纲就能绕过门禁落库。
                #
                # 刻意**不含**「正文为空」：空响应是上游过载/纯 reasoning 造成的传输问题，
                # 不是形态问题，混进来会让空响应被当成格式错误抛错、
                # 报出误导性的「请检查 plot 层 prompt」。
                _FORMAT_P0_NEEDLES = ("章细纲格式错误", "章细纲字段不全",
                                      "章细纲过长", "章细纲过短")

                def _is_format_p0(msg: str) -> bool:
                    return any(n in msg for n in _FORMAT_P0_NEEDLES)

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
                        if _is_format_p0(msg):
                            # 格式错 = 形态错，不进降级通道，原样留在 P0
                            remaining_p0.append(msg)
                            continue
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
                    # 活人感硬规则后处理（带护栏：崩了就退回原文，spec 2）
                    cleaned_text, guard_res = guarded_postprocess(completion.text)
                    if guard_res.rolled_back and on_event:
                        # 静默回滚会让人以为规则生效了其实没有——必须让作者看见
                        on_event("notice", {
                            "text": f"[去AI味护栏] 后处理产生崩坏，已退回原文：{guard_res.reason}"})
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

                    # 质量账本：块级门禁后落库 status=draft（趋势用，spec 1）。
                    # 必须落在 _apply_burstiness_guard 之后，这样 verdict.p0 是
                    # 降级后的真实值而不是初判值。
                    # 静默失败：账本写失败绝不能阻断生成（照抄 lexicon 的做法）。
                    if project_id and chapter_no:
                        try:
                            from ..generation.ledger import record_quality
                            record_quality(
                                project_id, chapter_no, spec.name,
                                hard_review, len(completion.text),
                                run_key=x_hash,
                                notes=f"round{quality_rounds}",
                                guard=guard_res.as_dict(),
                            )
                        except Exception as led_e:
                            log.debug("quality ledger record failed: %s", led_e)

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
                    # 是否需要重写：
                    #   · 有 P0 —— 必须重写（硬门禁）
                    #   · 无 P0 但 P1 达到阈值 —— 也重写（2026-10-24）
                    #     旧行为是 P1 只提示不重写，于是作者看到「P1 8 项」却什么都没发生，
                    #     而这些问题（数字密度、时间锚点、长段不足、字种密度…）
                    #     恰恰是正文质量的大头——不修就等于没检查。
                    #     阈值取 3 项：1-2 项是吹毛求疵，3 项以上说明这一版整体偏了。
                    _p1_rewrite = (not hard_review.p0
                                   and len(hard_review.p1) >= P1_REWRITE_THRESHOLD)
                    if hard_review.passed and not _p1_rewrite:
                        break
                    quality_rounds += 1
                    if quality_rounds >= max_quality_rounds:
                        # 重试用尽：**格式类 P0 不能靠"重写"修复**（模型会继续写散文），
                        # 保留的是最后一版，所以这里必须挡住坏结果落库。
                        _fmt_p0 = [m for m in hard_review.p0
                                   if "章细纲格式错误" in m or "章细纲字段不全" in m
                                   or "章细纲过长" in m or "章细纲过短" in m
                                   or "连续场缺" in m]
                        if _fmt_p0:
                            raise ValueError(
                                f"章细纲连续 {max_quality_rounds} 轮未通过格式门禁，已中止以免把正文当细纲落库："
                                + "；".join(_fmt_p0)[:300]
                                + "。请检查 plot 层 prompt，或调高 max_quality_rounds。"
                            )
                        # 非格式 P0：不再静默 break（spec 4）。
                        # 旧行为是直接落库，作者永远不知道这章被扣过分。
                        # 现在把 P0 带出引擎，由前端要求作者表态（收下/重写/手改）。
                        if hard_review.p0:
                            gate_pending.extend(hard_review.p0)
                        if demoted_msgs:
                            gate_demoted.extend(demoted_msgs)
                        if hard_review.p0 and on_event:
                            on_event("notice", {
                                "text": f"[质量门禁] 已重写 {max_quality_rounds} 轮，"
                                        f"仍有 {len(hard_review.p0)} 项未达标——正文已生成，"
                                        "但需要你确认是否收下（可选择再重写一次或自己改）"})
                        elif _p1_rewrite and on_event:
                            # P1 重写用尽：不算「未达标」，只是建议项没改干净。
                            # 不弹「收下/重写/手改」——那会让作者以为这一版坏了。
                            on_event("notice", {
                                "text": f"[质量门禁] 已按 {len(hard_review.p1)} 项建议重写 {max_quality_rounds} 轮，"
                                        "正文已生成（建议项未全部改净，可在正文里手改）"})
                        break
                    # 把 P0 问题拼成 extra_directive 下一轮注入
                    #
                    # 跑团层跳过重试（第三步）：hard_gate 照常检测，但**不再触发重写**。
                    # 这一步是解决「本来就慢」的主要手段——最坏 3× 调用压回 1×。
                    # 检测结果仍然要透出给作者（gate_pending），不能静默吞掉。
                    if spec.skip_quality_retry:
                        if hard_review.p0:
                            gate_pending.extend(hard_review.p0)
                            if on_event:
                                on_event("notice", {
                                    "text": f"[跑团层质量提示] 检出 {len(hard_review.p0)} 项问题"
                                            f"（已跳过重写，跑团层不因门禁重试）："
                                            + "；".join(hard_review.p0)[:200]})
                        break
                    extra_directive = "【质量门禁问题】\n" + "\n".join(f"- {x}" for x in hard_review.p0)
                    # P1 也要带进重写指令（2026-10-24）。
                    # 旧行为：P1 只是显示给作者看，重写时模型完全不知道这些问题。
                    # 于是「P1 8 项」看得见却改不掉——作者看到一串问题，
                    # 点了重写，结果问题原封不动（因为模型没收到任何 P1 信息）。
                    if hard_review.p1:
                        extra_directive += (
                            "\n【建议级问题（同样要修）】\n"
                            + "\n".join(f"- {x}" for x in hard_review.p1))
                    # 格式类 P0（写成正文而非细纲）需要**换输出形态**，
                    # 不能只说「重写这一段」——那会让模型继续写散文。
                    if any("章细纲格式错误" in m or "章细纲过长" in m
                           or "章细纲字段不全" in m or "连续场缺" in m
                           for m in hard_review.p0):
                        extra_directive += (
                            "\n\n【输出形态纠正（最高优先级，覆盖其他要求）】"
                            "\n你上一轮把章细纲写成了连续正文散文，这是**错误的输出形态**。"
                            "\n章细纲不是正文，是给写作者用的结构化施工图。"
                            "\n现在必须输出编号场景块，每场严格用下列字段格式："
                            "\n第1场：<场名>\n"
                            "- 场景：<地点+时间，一句话>\n"
                            "- 微观目标：<POV角色这一场想达成什么>\n"
                            "- 阻力：<谁/什么在挡他>\n"
                            "- 信息差：<读者知道而角色不知道的，或反之>\n"
                            "- 关键对白：「…」\n"
                            "- 关键动作：A→B→C\n"
                            "- 感官锚点：<触觉/听觉/嗅觉/视觉里最强的一种>\n"
                            "- 情绪四拍：铺垫→顶→翻→收\n"
                            "- 落点：<这一场结束时的状态>\n"
                            "- 转场桥：<Q1-Q5 锚点=…；同一时空中下一拍写「同场续」；第1场写「首场无桥」>\n"
                            "- 本场变化：<相比上一场推进了什么>\n"
                            "\n硬要求：2-8 场（场数由内容决定，不要凑数）；每场 6-11 行；总长 1200-1900 字；"
                            "不要写正文、不要写对白全文、不要写叙述段落。"
                            "\n同一时空的连续事件写成「同场续」的相邻两场，不要为凑场数硬切。"
                            "\n如果内容写不下，说明该拆成更多场，而不是写得更长。"
                        )
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
                                cleaned2, guard2 = guarded_postprocess(completion2.text)
                                if guard2.rolled_back and on_event:
                                    on_event("notice", {
                                        "text": f"[去AI味护栏] 重写稿后处理崩坏，保留原版本：{guard2.reason}"})
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
                              snapshots=snapshots, resumed_indices=resumed,
                              gate_pending=gate_pending, gate_demoted=gate_demoted)

    def _generate_stream(self, msgs: list[dict], max_tokens: int, index: int,
                         on_event: Callable[[str, dict[str, Any]], None] | None = None,
                         layer: str = "") -> tuple[str, str]:
        """流式调用：thinking/delta 实时回调，同时聚合正文与真实 finish_reason。

        layer 透传给 provider，用于按层选 reasoning_effort（结构层不能 high）。
        """
        parts: list[str] = []
        usage_total = 0
        finish_reason: str | None = None
        for ev in self.provider.chat_stream(msgs, self.model, max_tokens=max_tokens,
                                            extra=_layer_extra(layer)):
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
