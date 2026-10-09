from copy import deepcopy

from app.generation import ModelLimits
from app.pipeline import (
    OUTPUT_CONTRACT,
    SPECS,
    SYSTEM_CONTRACT,
    PipelineRunner,
    build_novel,
    proxy_finish_reason,
    split_outline_into_chapters,
)
from app.context import Material, MaterialFamily
from app.providers.base import Provider, StreamEvent, Usage


class MockProvider(Provider):
    name = "mock"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.default_model = "mock-1"

    def chat_stream(self, messages, model=None, max_tokens=None, extra=None):
        self.calls.append({"messages": deepcopy(messages), "max_tokens": max_tokens})
        text = self.responses.pop(0)
        out_tok = min(max_tokens or 2048, int(len(text) * 2.4))
        yield StreamEvent(kind="delta", text=text)
        yield StreamEvent(kind="usage", usage=Usage(input_tokens=500, output_tokens=out_tok))
        yield StreamEvent(kind="done")


def user_content(call: dict) -> str:
    return "\n".join(m["content"] for m in call["messages"])


LIMITS = ModelLimits(context_window_tokens=32_000, max_output_tokens=8_192)


class TestProxyFinishReason:
    def test_length_and_stop(self):
        assert proxy_finish_reason(300, 300) == "length"
        assert proxy_finish_reason(285, 300) == "length"
        assert proxy_finish_reason(284, 300) == "stop"


class TestIntroLayer:
    def test_intro_generation(self):
        provider = MockProvider(["少年林越在废矿里捡到一枚会说话的戒指，从此踏上修行路，但戒指里睡着一个魔头。" * 2])
        runner = PipelineRunner(provider, limits=LIMITS)
        result = runner.run_layer(SPECS["intro"], [("废土少年捡到魔头戒指。", "")])

        assert len(result.pairs) == 1
        x, y = result.pairs[0]
        assert x == "废土少年捡到魔头戒指。"
        assert "废矿" in y
        assert len(result.calls) == 1
        call = result.calls[0]
        assert call.messages[0]["content"] == SYSTEM_CONTRACT
        assert OUTPUT_CONTRACT in call.messages[1]["content"]
        assert "废土少年捡到魔头戒指。" in call.messages[1]["content"]  # x 块进 prompt
        assert call.decision.status == "ready"


class TestOutlineLayer:
    def test_labels_single_chunk(self):
        provider = MockProvider(["第一章 遇戒指，魔头苏醒。\n\n第二章 初修行，引灵入体。\n\n第三章 同门忌惮，暗中下套。"])
        runner = PipelineRunner(provider, limits=LIMITS)
        spec = __import__("app.pipeline", fromlist=["SPECS"]).SPECS["outline"]
        result = runner.run_layer(spec, [("第一章", ""), ("第二章", ""), ("第三章", "")], x_chunk_length=40)
        # 三个小标签合成一个窗口、一次调用产出整份大纲
        assert len(provider.calls) == 1
        assert result.y.count("第二章") >= 1


class TestSplitOutline:
    def test_split_into_n_chapters(self):
        text = "第一章内容事件甲。\n\n第二章内容事件乙。\n\n第三章内容事件丙。"
        chapters = split_outline_into_chapters(text, 3)
        assert len(chapters) == 3
        assert "".join(chapters) == text


class TestBuildNovelE2E:
    def test_full_chain_with_mock(self):
        n = 3
        intro_text = "林越在废矿捡到魔头戒指，被迫踏上修行路。" * 3
        outline_text = "第一章林越得戒指，魔头低语。\n\n第二章引气入体遭同门嫉恨。\n\n第三章外门大比一鸣惊人。"
        plot_texts = [f"剧情第{i}章：冲突展开，" + "场景推进描写。" * 8 for i in range(1, n + 1)]
        draft_texts = [
            f"正文第{i}章开场，" + "具体场面与对白交替出现，" * 6 + f"章末钩子{i}。" for i in range(1, n + 1)
        ]
        polish_texts = [f"润色第{i}章：" + "更干净的短句场景。" * 8 for i in range(1, n + 1)]

        provider = MockProvider([intro_text, outline_text] + plot_texts + draft_texts + polish_texts)
        runner = PipelineRunner(provider, limits=LIMITS)
        results = build_novel(runner, "废土少年捡到魔头戒指", n_chapters=n)

        assert set(results) == {"intro", "outline", "plot", "draft", "polish"}
        # 层间继承：剧情.x == 大纲拆章，正文.x == 剧情.y，润色.x == 正文.y
        chapters = split_outline_into_chapters(results["outline"].y, n)
        assert [x for x, _ in results["plot"].pairs] == chapters
        assert [x for x, _ in results["draft"].pairs] == [y for _, y in results["plot"].pairs]
        assert [x for x, _ in results["polish"].pairs] == [y for _, y in results["draft"].pairs]
        # 全链路每层产出了文本
        for layer in results.values():
            assert layer.y.strip()
            assert all(c.decision is not None for c in layer.calls)
        # 调用计数：简介1 + 大纲1 + 剧情3 + 正文3 + 润色3
        assert len(provider.calls) == 11

    def test_draft_rolling_anchor(self):
        n = 3
        plot_texts = [f"剧情第{i}章，" + "推进。" * 10 for i in range(1, n + 1)]
        draft_texts = [
            f"正文第{i}章：" + "情节文字。" * 10 + f"章末悬念句第{i}章完结。" for i in range(1, n + 1)
        ]
        provider = MockProvider(
            ["简介。" * 30, "第一章大纲。\n\n第二章大纲。\n\n第三章大纲。"] + plot_texts + draft_texts + ["润色。" * 20] * 3
        )
        runner = PipelineRunner(provider, limits=LIMITS)
        results = build_novel(runner, "点子", n_chapters=n)

        draft = results["draft"]
        assert len(draft.calls) == 3
        # 调用序：简介(0)、大纲(1)、剧情(2-4)、正文(5-7)；第二章 prompt 必须携带第一章结尾锚点
        assert "章末悬念句第1章完结" not in user_content(provider.calls[5])  # 首章无锚点
        assert "章末悬念句第1章完结" in user_content(provider.calls[6])
        assert "章末悬念句第2章完结" in user_content(provider.calls[7])

    def test_materials_rendered_into_prompt(self):
        provider = MockProvider(["正文。"])
        runner = PipelineRunner(provider, limits=LIMITS)
        outline_mat = Material(
            source_id="outline:ch1", family=MaterialFamily.FINALIZED,
            content="第一章大纲：林越获得戒指。", title="第1章大纲", keywords=("林越",),
        )
        runner.run_layer(SPECS["draft"], [("剧情：林越捡到戒指。", "")], materials=[outline_mat])
        content = user_content(provider.calls[0])
        assert "第1章大纲" in content
        assert "林越获得戒指" in content

    def test_tight_budget_omits_optional_material(self):
        provider = MockProvider(["正文。"])
        runner = PipelineRunner(provider, limits=LIMITS, assembler_max_bytes=1_200)
        spec = __import__("app.pipeline", fromlist=["SPECS"]).SPECS["draft"]
        big = Material(source_id="big", family=MaterialFamily.REFERENCE, content="参考资料" * 300)
        result = runner.run_layer(spec, [("剧情。", "")], materials=[big])
        assert result.snapshots[0].omissions[0].source_id == "big"
        assert "参考资料" * 10 not in user_content(provider.calls[0])


class TestTruncationContinuation:
    def test_length_triggers_bounded_continuation(self):
        provider = MockProvider(
            [
                "第一段情节" * 25 + "，仍未收束。",   # 触发 length（usage 顶满 max_tokens）
                "续写内容后自然收尾。",
            ]
        )
        tight = ModelLimits(context_window_tokens=32_000, max_output_tokens=300)
        runner = PipelineRunner(provider, limits=tight)
        result = runner.run_layer(SPECS["plot"], [("第一章大纲内容。" * 5, "")])
        assert len(provider.calls) == 2
        # 续写调用带了断点前文
        second = user_content(provider.calls[1])
        assert "从断点处续写" in second
        assert result.calls[0].completion_status == "complete"
        assert result.y.endswith("续写内容后自然收尾。")
