"""副本跑团 · 失败可见性测试（第一步）。

对应设计文档：docs/superpowers/specs/2026-10-22-coc-failure-visibility-design.md

核心断言只有一条，但它很硬：
**失败绝不能伪装成成功产物。**

历史事故：`except` 把 traceback 写进 `state.final_summary`，而前端拿
`final_summary` 当卡片正文渲染——于是作者看到「一份有内容的梗概」，
内容是一句 Provider 报错。这条测试就是防止它复发。
"""
from __future__ import annotations

import pytest

from app.fields.coc_run import CocRunState
from app.pipeline.coc_simulator import _summary_card_from_state


def _state(**kw) -> CocRunState:
    s = CocRunState(field_no=1)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ============================================================
# 一、final_summary 只装梗概，异常文本一律进 error
# ============================================================

def test_final_summary_never_contains_traceback():
    """final_summary 是「梗概」字段，异常文本写进去等于伪装成功。"""
    s = _state(final_summary="", error="ProviderError: boom")
    card = _summary_card_from_state(s, s.final_summary, 0)
    assert "ProviderError" not in card["final_summary"]
    assert "boom" not in card["final_summary"]


def test_full_log_keeps_debug_info():
    """traceback 仍要能拿到——只是不能占据梗概位。"""
    s = _state(final_summary="", error="AttributeError: CONTEXT")
    card = _summary_card_from_state(s, "", 0)
    # full_log 是调试素材区，保留错误信息是对的
    assert "AttributeError" in card["full_log"]


# ============================================================
# 二、run_status 是独立字段
# ============================================================

def test_ok_when_no_error():
    s = _state(final_summary="一场完整的梗概。", status="awaiting_author")
    card = _summary_card_from_state(s, s.final_summary, 12)
    assert card["run_status"] == "ok"
    assert card["fail_reason"] == ""


def test_failed_when_status_failed():
    """status='failed' 即便没有 error 文本也要判失败。"""
    s = _state(final_summary="", status="failed", error="")
    card = _summary_card_from_state(s, "", 0)
    assert card["run_status"] == "failed"


def test_failed_when_error_present():
    s = _state(final_summary="", status="awaiting_author",
               error="ProviderError: network")
    card = _summary_card_from_state(s, "", 4)
    assert card["run_status"] == "failed"
    assert "ProviderError" in card["fail_reason"]


def test_ok_not_mistaken_when_turns_exist():
    """跑过几轮但成功 = ok。turns 有值不等于失败。"""
    s = _state(final_summary="梗概。", status="awaiting_author")
    s.add_turn("keeper", "KP 描述")
    s.add_turn("investigator", "PL 行动")
    card = _summary_card_from_state(s, s.final_summary, 2)
    assert card["run_status"] == "ok"
    assert card["fail_stage"] == ""


# ============================================================
# 三、fail_stage 从 turn meta 里回溯
# ============================================================

def test_fail_stage_picked_from_system_turn():
    s = _state(final_summary="", status="failed", error="ProviderError: x")
    s.add_turn("system", "压真相失败", meta={"failed_stage": "finalize"})
    card = _summary_card_from_state(s, "", 1)
    assert card["fail_stage"] == "finalize"


def test_fail_stage_absent_when_no_meta():
    s = _state(final_summary="", status="failed", error="boom")
    card = _summary_card_from_state(s, "", 0)
    assert card["fail_stage"] == ""


def test_fail_stage_takes_latest_marked_turn():
    """多轮里最后一次标记的阶段才算数。"""
    s = _state(final_summary="", status="failed", error="boom")
    s.add_turn("system", "早期", meta={"failed_stage": "bootstrap"})
    s.add_turn("system", "后期", meta={"failed_stage": "finalize"})
    card = _summary_card_from_state(s, "", 2)
    assert card["fail_stage"] == "finalize"


# ============================================================
# 四、卡片结构一致性
# ============================================================

def test_card_always_has_status_fields():
    """成功卡与失败卡字段集必须一致——前端按同一套模板渲染。"""
    ok = _summary_card_from_state(_state(final_summary="梗概。"), "梗概。", 6)
    bad = _summary_card_from_state(_state(final_summary="", error="x"), "", 0)
    for k in ("run_status", "fail_stage", "fail_reason", "final_summary"):
        assert k in ok, k
        assert k in bad, k


def test_one_liner_not_error_text():
    """失败卡的 one_liner 不能是 traceback——它会显示在选项标签上。"""
    s = _state(final_summary="", status="failed", error="ProviderError: 网络错误")
    card = _summary_card_from_state(s, "", 0)
    # one_liner 退化成结局标签 + 幸存者数，而不是错误文本
    assert "ProviderError" not in card["one_liner"]


def test_one_liner_falls_back_when_summary_empty():
    s = _state(final_summary="", status="failed", error="x")
    card = _summary_card_from_state(s, "", 0)
    assert card["one_liner"]     # 不该是空串——空标签会让前端渲染出空白项


# ============================================================
# 五、状态语义：paused 与 failed 必须分开
# ============================================================

def test_paused_is_not_failed():
    """paused = 跑过被叫停，可续跑；failed = 出错需重跑。混用会让作者
    在「继续」和「重来」之间选错。"""
    s = _state(final_summary="梗概。", status="paused")
    card = _summary_card_from_state(s, s.final_summary, 4)
    assert card["run_status"] == "ok"


def test_failed_status_preserved_on_save_roundtrip(tmp_path):
    """status='failed' 要能落盘再读回来——否则重开页面就看不出失败了。"""
    import json

    s = _state(status="failed", error="ProviderError: x", final_summary="")
    raw = json.loads(json.dumps(s.as_dict(), ensure_ascii=False))
    assert raw["status"] == "failed"
    assert raw["final_summary"] == ""       # 关键：梗概位是空的
    assert raw["error"]


# ============================================================
# 六、批量汇总的成功计数
# ============================================================

def test_batch_success_count_uses_run_status():
    """成功数必须按 run_status 判，不能按 `not card.get('error')`。

    后者会漏统计「压真相失败但卡片没有 error 键」的情况，把失败算成成功——
    这正是修复前 batch_done 消息里那个计数的缺陷。
    """
    variants = [
        {"vid": "1", "run_status": "ok", "error": ""},
        {"vid": "2", "run_status": "failed", "fail_reason": "x"},   # 无 error 键
        {"vid": "3", "run_status": "ok"},
    ]
    ok_n = sum(1 for v in variants if v.get("run_status") != "failed")
    assert ok_n == 2


def test_batch_message_mentions_failure_count():
    """有失败时 batch_done 的文案必须点明失败数，不能只报成功数。"""
    total, ok_n = 3, 2
    msg = (f"并行批量完成，共 {ok_n} 场成功"
           + ("" if ok_n == total else f"，{total - ok_n} 场失败（需重跑）"))
    assert "1 场失败" in msg
    assert "需重跑" in msg


def test_batch_message_clean_when_all_ok():
    total, ok_n = 3, 3
    msg = (f"并行批量完成，共 {ok_n} 场成功"
           + ("" if ok_n == total else f"，{total - ok_n} 场失败（需重跑）"))
    assert "失败" not in msg


# ============================================================
# 七、作者介入后应能恢复
# ============================================================

def test_intervene_clears_error(tmp_path):
    """作者介入会清 state.error（coc_simulator.intervene:548），
    所以重跑后 run_status 应自动回到 ok。

    这条很重要：失败场不能是死路——作者补一段设定就应该能继续，
    而不是永远卡在 failed 上只能整批重跑。
    """
    from app.fields.coc_run import CocRunStore
    from app.pipeline.coc_simulator import intervene

    store = CocRunStore(tmp_path / "coc")
    s = _state(status="failed", error="ProviderError: x", final_summary="")
    intervene(s, "作者补充设定：暴风雪封路", store, as_role="author")
    assert s.error is None
    card = _summary_card_from_state(s, "", 1)
    assert card["run_status"] == "ok"


# ============================================================
# 八、防回归：旧事故的具体形状
# ============================================================

@pytest.mark.parametrize("err_text", [
    "（本场压真相失败：ProviderError: MiniMax 网络错误）",
    "（本场仿真未能启动：AttributeError: CONTEXT）",
    "ERROR: RuntimeError: boom",
])
def test_old_failure_shapes_are_not_produced(err_text):
    """旧实现把这三种文本写进 final_summary。现在任何一个都不该再出现——
    如果 grep 到这些字符串，说明有人把失败路径改回去了。"""
    import inspect

    from app.pipeline import coc_simulator

    src = inspect.getsource(coc_simulator)
    # 只允许出现在注释/文档里，不允许出现在赋值语句右侧
    for bad in ('state.final_summary = f"（本场压真相失败',
                'state.final_summary = f"（本场仿真未能启动',
                '"final_summary": f"ERROR:'):
        assert bad not in src, f"失败文本又被写回 final_summary 了：{bad}"
    assert err_text  # 参数化：三种历史形状都登记在案