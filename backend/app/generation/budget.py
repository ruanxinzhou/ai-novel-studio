"""预算规划器（M1-T8，机制源自 Vela task-budget-planner）。

纯函数：语义估算输出需求，与物理上限逐项取 min，产出三态决策+reasons 审计链。

估算常数为保守上界（宁可提前拆批，不临时截断）：
- 正文：5 字 ≈ 12 token（Vela 口径）
- 输入：UTF-8 字节 × 0.35（中文 ≈3B/字 × ~0.6tok/字，保守上浮）；若调用方
  持有上次真实 usage（measured_prompt_tokens），优先用实测值。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---- 保守估算常数 ----
PROSE_TOKENS_PER_CHAR = 12 / 5
PROMPT_TOKENS_PER_BYTE = 0.35
SAFETY_MARGIN_TOKENS = 256

READY = "ready"
SPLIT_REQUIRED = "split_required"
CAPACITY_CONFLICT = "capacity_conflict"

SPLIT_SAFETY_RATIO = 0.8


@dataclass(frozen=True)
class ModelLimits:
    context_window_tokens: int
    max_output_tokens: int


@dataclass(frozen=True)
class BudgetRequest:
    prompt_bytes: int
    output_kind: str = "prose"  # prose | structured
    target_output_chars: int = 0  # prose：期望正文长度（中文字符）
    structured_output_tokens: int = 0  # structured：该项的输出需求估算
    measured_prompt_tokens: int | None = None
    user_output_cap_tokens: int | None = None
    parent_remaining_tokens: int | None = None
    reasoning_reserve_tokens: int = 0


@dataclass(frozen=True)
class BudgetDecision:
    status: str
    output_budget_tokens: int
    prompt_tokens: int
    output_need_tokens: int
    output_cap_tokens: int
    split_target_chars: int | None = None
    reasons: tuple[str, ...] = field(default=())


def estimate_prompt_tokens(request: BudgetRequest) -> int:
    if request.measured_prompt_tokens is not None:
        return request.measured_prompt_tokens
    return int(request.prompt_bytes * PROMPT_TOKENS_PER_BYTE)


def estimate_output_need_tokens(request: BudgetRequest) -> int:
    if request.output_kind == "structured":
        return request.structured_output_tokens
    return int(request.target_output_chars * PROSE_TOKENS_PER_CHAR)


def plan_budget(request: BudgetRequest, limits: ModelLimits) -> BudgetDecision:
    reasons: list[str] = []

    prompt_tokens = estimate_prompt_tokens(request)
    reasons.append(
        f"输入计量: {prompt_tokens}tok"
        + ("" if request.measured_prompt_tokens is None else "（实测优先）")
        + (f"（{request.prompt_bytes}B×{PROMPT_TOKENS_PER_BYTE} 估算）" if request.measured_prompt_tokens is None else "")
    )

    # 输入本身装不下上下文
    reserved = prompt_tokens + request.reasoning_reserve_tokens + SAFETY_MARGIN_TOKENS
    if reserved > limits.context_window_tokens:
        reasons.append(
            f"输入超上下文: {reserved}tok > {limits.context_window_tokens}tok"
            f"（含推理预留 {request.reasoning_reserve_tokens} + 安全余量 {SAFETY_MARGIN_TOKENS}）"
        )
        reasons.append("决策: capacity_conflict——缩减装配预算（ContextAssembler max_bytes）后重试")
        return BudgetDecision(
            status=CAPACITY_CONFLICT,
            output_budget_tokens=0,
            prompt_tokens=prompt_tokens,
            output_need_tokens=0,
            output_cap_tokens=0,
            reasons=tuple(reasons),
        )

    # 输出上限逐项取 min：模型上限 < 用户设置 < 父任务剩余额度 < 上下文剩余空间
    caps: list[tuple[str, int]] = [("模型输出上限", limits.max_output_tokens)]
    if request.user_output_cap_tokens is not None:
        caps.append(("用户设置", request.user_output_cap_tokens))
    if request.parent_remaining_tokens is not None:
        caps.append(("父任务剩余额度", request.parent_remaining_tokens))
    context_left = limits.context_window_tokens - prompt_tokens - request.reasoning_reserve_tokens - SAFETY_MARGIN_TOKENS
    caps.append(("上下文剩余空间", context_left))

    output_cap = min(v for _, v in caps)
    caps_desc = " < ".join(f"{name}={v}" for name, v in caps)
    reasons.append(f"输出上限取 min({caps_desc}) = {output_cap}tok")
    reasons.append(f"安全余量 {SAFETY_MARGIN_TOKENS}tok、推理预留 {request.reasoning_reserve_tokens}tok 已扣除")

    if output_cap <= 0:
        reasons.append("决策: capacity_conflict——输出空间不足")
        return BudgetDecision(
            status=CAPACITY_CONFLICT,
            output_budget_tokens=0,
            prompt_tokens=prompt_tokens,
            output_need_tokens=0,
            output_cap_tokens=output_cap,
            reasons=tuple(reasons),
        )

    output_need = estimate_output_need_tokens(request)
    if request.output_kind == "prose":
        reasons.append(f"输出需求: 正文 {request.target_output_chars} 字 × {PROSE_TOKENS_PER_CHAR} = {output_need}tok")
    else:
        reasons.append(f"输出需求: 结构化项估算 {output_need}tok")

    if output_need <= output_cap:
        reasons.append(f"决策: ready——预算 {output_need}tok")
        return BudgetDecision(
            status=READY,
            output_budget_tokens=output_need,
            prompt_tokens=prompt_tokens,
            output_need_tokens=output_need,
            output_cap_tokens=output_cap,
            reasons=tuple(reasons),
        )

    if request.output_kind == "structured":
        reasons.append("决策: capacity_conflict——结构化项过大，建议减小目标项数或分批拆项")
        return BudgetDecision(
            status=CAPACITY_CONFLICT,
            output_budget_tokens=0,
            prompt_tokens=prompt_tokens,
            output_need_tokens=output_need,
            output_cap_tokens=output_cap,
            reasons=tuple(reasons),
        )

    split_chars = int(output_cap / PROSE_TOKENS_PER_CHAR * SPLIT_SAFETY_RATIO)
    reasons.append(f"决策: split_required——建议首窗 {split_chars} 字（含 {SPLIT_SAFETY_RATIO} 拆批安全系数）")
    return BudgetDecision(
        status=SPLIT_REQUIRED,
        output_budget_tokens=output_cap,
        prompt_tokens=prompt_tokens,
        output_need_tokens=output_need,
        output_cap_tokens=output_cap,
        split_target_chars=split_chars,
        reasons=tuple(reasons),
    )
