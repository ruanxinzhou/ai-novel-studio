from app.generation import (
    CAPACITY_CONFLICT,
    PROSE_TOKENS_PER_CHAR,
    READY,
    SPLIT_REQUIRED,
    BudgetRequest,
    ModelLimits,
    estimate_prompt_tokens,
    plan_budget,
)

BIG_MODEL = ModelLimits(context_window_tokens=128_000, max_output_tokens=8_192)
SMALL_OUTPUT_MODEL = ModelLimits(context_window_tokens=8_192, max_output_tokens=1_024)


class TestProseReady:
    def test_small_target_ready(self):
        request = BudgetRequest(prompt_bytes=3_000, target_output_chars=1_000)
        decision = plan_budget(request, BIG_MODEL)
        assert decision.status == READY
        assert decision.output_budget_tokens == int(1_000 * PROSE_TOKENS_PER_CHAR)
        assert decision.split_target_chars is None
        assert any("ready" in r for r in decision.reasons)
        assert len(decision.reasons) >= 3  # 审计链完整

    def test_semantic_estimate_formula(self):
        request = BudgetRequest(prompt_bytes=0, target_output_chars=500)
        assert plan_budget(request, BIG_MODEL).output_budget_tokens == 1_200


class TestSplitRequired:
    def test_big_target_split(self):
        request = BudgetRequest(prompt_bytes=1_000, target_output_chars=20_000)
        decision = plan_budget(request, SMALL_OUTPUT_MODEL)
        assert decision.status == SPLIT_REQUIRED
        assert decision.split_target_chars is not None
        assert 0 < decision.split_target_chars < 20_000
        # 建议首窗必须能装进输出上限
        assert decision.split_target_chars * PROSE_TOKENS_PER_CHAR <= decision.output_cap_tokens
        assert any("split_required" in r for r in decision.reasons)

    def test_user_cap_triggers_split(self):
        request = BudgetRequest(prompt_bytes=1_000, target_output_chars=10_000, user_output_cap_tokens=2_048)
        decision = plan_budget(request, BIG_MODEL)
        assert decision.status == SPLIT_REQUIRED
        assert decision.output_cap_tokens == 2_048
        assert any("用户设置" in r for r in decision.reasons)

    def test_parent_remaining_clamps(self):
        request = BudgetRequest(prompt_bytes=1_000, target_output_chars=10_000, parent_remaining_tokens=4_096)
        decision = plan_budget(request, BIG_MODEL)
        assert decision.output_cap_tokens == 4_096
        assert any("父任务剩余额度" in r for r in decision.reasons)


class TestCapacityConflict:
    def test_prompt_exceeds_context(self):
        # 100_000 B × 0.35 = 35_000tok ≈ 上下文 8192
        request = BudgetRequest(prompt_bytes=100_000, target_output_chars=100)
        decision = plan_budget(request, SMALL_OUTPUT_MODEL)
        assert decision.status == CAPACITY_CONFLICT
        assert any("输入超上下文" in r for r in decision.reasons)

    def test_measured_tokens_preferred_over_estimate(self):
        estimate = estimate_prompt_tokens(BudgetRequest(prompt_bytes=1_000))
        measured = estimate_prompt_tokens(BudgetRequest(prompt_bytes=1_000, measured_prompt_tokens=9_999))
        assert estimate == 350
        assert measured == 9_999
        # 小目标：估算下 ready，实测巨大输入 → conflict
        base = BudgetRequest(prompt_bytes=1_000, target_output_chars=200)
        assert plan_budget(base, SMALL_OUTPUT_MODEL).status == READY
        assert (
            plan_budget(
                BudgetRequest(prompt_bytes=1_000, target_output_chars=200, measured_prompt_tokens=9_999),
                SMALL_OUTPUT_MODEL,
            ).status
            == CAPACITY_CONFLICT
        )

    def test_no_room_for_output(self):
        # prompt 吃到只留 0 token 输出空间：7899tok 输入 + 256 余量 = 8192 恰好满
        request = BudgetRequest(prompt_bytes=22_675, target_output_chars=100)
        decision = plan_budget(request, SMALL_OUTPUT_MODEL)
        assert decision.status == CAPACITY_CONFLICT
        assert any("输出空间不足" in r for r in decision.reasons)

    def test_structured_too_large_conflicts(self):
        request = BudgetRequest(prompt_bytes=1_000, output_kind="structured", structured_output_tokens=50_000)
        decision = plan_budget(request, SMALL_OUTPUT_MODEL)
        assert decision.status == CAPACITY_CONFLICT
        assert any("结构化项过大" in r for r in decision.reasons)


class TestStructuredReady:
    def test_structured_ready(self):
        request = BudgetRequest(prompt_bytes=2_000, output_kind="structured", structured_output_tokens=800)
        decision = plan_budget(request, SMALL_OUTPUT_MODEL)
        assert decision.status == READY
        assert decision.output_budget_tokens == 800

    def test_reasoning_reserve_deducted(self):
        request = BudgetRequest(prompt_bytes=1_000, output_kind="structured", structured_output_tokens=900)
        tight = ModelLimits(context_window_tokens=2_048, max_output_tokens=2_048)
        assert plan_budget(request, tight).status == READY
        reserved = BudgetRequest(
            prompt_bytes=1_000, output_kind="structured", structured_output_tokens=900, reasoning_reserve_tokens=1_000
        )
        assert plan_budget(reserved, tight).status == CAPACITY_CONFLICT


class TestDeterminism:
    def test_same_input_same_audit_chain(self):
        request = BudgetRequest(prompt_bytes=5_000, target_output_chars=3_000)
        d1 = plan_budget(request, BIG_MODEL)
        d2 = plan_budget(request, BIG_MODEL)
        assert d1 == d2
        assert d1.reasons == d2.reasons
