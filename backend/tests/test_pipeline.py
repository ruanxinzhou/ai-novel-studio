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


def make_scene_outline(chapter_no: int, scenes: int = 6) -> str:
    """合规的章细纲mock：编号场景块 + 十个字段，长度落在门禁区间内。"""
    blocks = []
    for i in range(1, scenes + 1):
        blocks.append(
            f"第{i}场：试场{i}\n"
            "- 场景：客厅 · 夜（补足长度补足长度补足长度补足长度）\n"
            f"- POV：林越\n"
            "- 微观目标：确认门响来源，必须具体可失败\n"
            "- 阻力：声控灯延迟半拍，他判断错了一次\n"
            "- 关键对白：「你听见了？」「别开门。」\n"
            "- 关键动作：摸口袋→搓拇指→反扣手机→让半步\n"
            "- 感官锚点：铁锈味、电流声、门缝冷风\n"
            "- 情绪四拍：铺垫→顶→翻→收\n"
            "- 落点：收在他手停在门把上\n"
        )
    return "\n".join(blocks)


def make_prose(chapter_no: int, target: int = 4800) -> str:
    """合规的正文mock。

    要过的门禁：字数区间、问号密度、字种密度（前1500字去重占比 > 0.25，
    即前1500 字里至少要有 ~375 个不同汉字）、段长阶梯（≥3 个 >80 字长段，
    其中 ≥1 个 >120 字）。所以这里用大词库随机拼句并控制高频虚字比例。
    """
    import random
    rng = random.Random(chapter_no)

    # 词库按语义分组，每组够大才能撑起 0.25 的字种密度
    词库 = [
        "矿洞 戒指 魔头 灵石 同门 戒尺 丹炉 剑鞘 玉简 阵旗 火折子 砂岩 苔藓 铜铃 锁链 瓦砾毒菌",
        "铁镐 绳索 油灯 石阶 裂缝 积水 藤蔓 碎瓷 骨哨 符纸 沙漏 钉子 麻绳 洞口 巷子 屋檐 楼梯 阳台",
        "车流 人群 雨雪 雾霜 烈日 黄昏 深夜 清晨 正午 口袋 信封 钥匙 屏幕 电量 余额 账号 密码 同事",
        "组长 会议室 工位 键盘 鼠标 声音 气味 温度 触感 光线 阴影 边缘 缝隙 表面 底部 顶端 侧面 背面 正面",
        "语气 呼吸 心跳 转角 地面 天花板 墙角 门框 窗台 楼梯口 电梯口 下水道 井盖 消防栓 配电箱 水表 电表",
        "犹豫 迟疑 否认 承认 解释 争辩 劝说 威胁 利诱 哄骗 试探 敷衍 回避 抢答 追问 打断 补充 纠正 反驳",
        "纸条 账本 钢笔 墨水 印泥 签名 日期 数字 金额 零钱 硬币 纸币 卡号 雨水 汗水 泪水 血水 油水 墨水",
        "水泥 砂浆 油漆 铁锈 铜绿 苔痕 水渍 油渍 焦痕 裂痕 恐惧 愤怒 惊喜 悲伤 焦虑 疲惫 警觉 迟钝 麻木",
        "刺痛 酸胀 沉重 轻盈 滚烫 冰冷 温热 潮湿 干燥 坚硬 松软 光滑 粗糙 明亮 昏暗 刺眼 柔和 安静 喧闹",
        "清晨 午后 傍晚 深夜 星期一 星期三 星期五 三楼 五楼 十二号 十七号 三百块 四十岁 二十八岁 半年 一年",
    ]
    词 = [w for grp in 词库 for w in grp.split()]
    动作 = ["捡起", "压住", "推开", "攥紧", "盯住", "数过", "摸到", "踢开",
            "撬动", "掂量", "戳穿", "扒开", "拽住", "挪开", "按住", "掀起",
            "合上", "翻开", "插回", "拔掉", "拧紧", "撬起", "拨开", "扣上"]
    连接 = ["接着", "然后", "同时", "后来", "紧接着", "过了会儿", "半路上", "临睡前"]

    长段 = (
        "他把矿洞口的碎石一块块搬开，指甲缝里塞满了灰，掌心磨破的地方渗着血丝。"
        "洞里的风比外面暖，带着一股说不清的甜腥味，像有什么东西在深处慢慢呼吸。"
        "他数了数自己带下来的火折子，还剩三根，每根只能烧小半个时辰，"
        "也就是说他最多还有一个时辰决定要不要往里走。"
        "戒指在怀里发烫，隔着两层布都能感觉到那点温度往上钻，钻得人心里发慌。"
        "他想起师父临走前说的话：进洞之前先想清楚自己要什么，是活着出来，还是死在里头。"
    )

    body_parts = []
    long_seen = 0
    while sum(len(x) for x in body_parts) < target:
        # 字种密度只看前1500字，开头不堆固定长段；长段配额留给后半（≥3 个 >80 字）
        need_long = long_seen < 4 and sum(len(x) for x in body_parts) > 1800
        if rng.random() < 0.3 and need_long:
            long_seen += 1
            body_parts.append(长段 + f"这是第{chapter_no}章第{len(body_parts)}段。")
            continue
        # 每句：3 个不同词 + 1 个动作 + 1 个连接词，尽量少用虚字
        w = rng.sample(词, 3)
        body_parts.append(
            f"他{rng.choice(动作)}{w[0]}，{w[1]}上的{rng.choice(动作)}{w[2]}。"
            f"{rng.choice(连接)}，{w[2]}还在原地。多长时间了？他想。"
            f"{rng.randint(3, 90)}分钟。{w[0]}怎么会在这儿？他分不清。"
        )
    return f"第{chapter_no}章正文。" + "\n\n".join(body_parts)


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
        # 字数要落在 intro 层门禁区间（target 400），且带问号避免撞问号密度 P0
        provider = MockProvider([make_prose(0, 420)])
        runner = PipelineRunner(provider, limits=LIMITS)
        result = runner.run_layer(SPECS["intro"], [("废土少年捡到魔头戒指。", "")])

        assert len(result.pairs) == 1
        x, y = result.pairs[0]
        assert x == "废土少年捡到魔头戒指。"
        assert len(y) > 100
        assert len(result.calls) == 1
        call = result.calls[0]
        assert call.messages[0]["content"] == SYSTEM_CONTRACT
        assert OUTPUT_CONTRACT in call.messages[1]["content"]
        assert "废土少年捡到魔头戒指。" in call.messages[1]["content"]  # x 块进 prompt
        assert call.decision.status == "ready"


class TestOutlineLayer:
    def test_labels_single_chunk(self):
        # outline 层 target=1500，mock 必须够长并带问号，否则撞字数/问号密度门禁
        long_outline = "\n\n".join([
            f"第{i}章 林越{'得戒指，魔头低语' if i == 1 else '引气入体遭同门嫉恨' if i == 2 else '外门大比一鸣惊人'}。"
            f"这一章他要做什么？失败会怎样？{i * 20}字的内容铺垫，"
            f"包括场景、冲突、转折与章末钩子，共{i}段。" * 3
            for i in range(1, 4)
        ])
        provider = MockProvider([long_outline])
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
        intro_text = (
            "林越在废矿捡到魔头戒指，被迫踏上修行路。他该拿它怎么办？卖掉？"
            "还是先问清楚它要什么？矿洞外风声正紧，他只有这一次机会，选错就没有第二回。"
            "他决定先试一次，把戒指戴上，看看那个魔头会不会醒。"
        )
        outline_text = "\n\n".join([
            f"第{i}章 林越{'得戒指，魔头低语' if i == 1 else '引气入体遭同门嫉恨' if i == 2 else '外门大比一鸣惊人'}。"
            f"这一章他要做什么？失败会怎样？{i * 25}字的内容铺垫，"
            f"包括场景、冲突、转折与章末钩子，共{i}段。" * 3
            for i in range(1, n + 1)
        ])
        # plot 层现在只收编号场景块，mock 必须是合规细纲而不是散文
        plot_texts = [make_scene_outline(i) for i in range(1, n + 1)]
        draft_texts = [make_prose(i, 4800) for i in range(1, n + 1)]
        polish_texts = [make_prose(i, 5000) for i in range(1, n + 1)]

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
        plot_texts = [make_scene_outline(i) for i in range(1, n + 1)]
        # 章末锚点句必须出现在正文结尾，测试要验证它被带到下一章 prompt
        draft_texts = [
            make_prose(i, 4800) + f"\n\n章末悬念句第{i}章完结。" for i in range(1, n + 1)
        ]
        outline_text = "\n\n".join([
            f"第{i}章 点子展开。{i * 30}字的内容铺垫，包括场景与转折，"
            f"这一章要做什么？{i}段。" * 3
            for i in range(1, n + 1)
        ])
        provider = MockProvider(
            [make_prose(0, 400), outline_text] + plot_texts + draft_texts + [make_prose(i, 5000) for i in range(1, n + 1)]
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
        provider = MockProvider([make_prose(1, 4800)])
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
        provider = MockProvider([make_prose(1, 4800)])
        runner = PipelineRunner(provider, limits=LIMITS, assembler_max_bytes=200_000)
        spec = __import__("app.pipeline", fromlist=["SPECS"]).SPECS["draft"]
        big = Material(source_id="big", family=MaterialFamily.REFERENCE, content="参考资料" * 300)
        result = runner.run_layer(spec, [("剧情。", "")], materials=[big])
        # 参考资料是 REFERENCE 族，可以被预算挤掉；必进材料才不许静默截断
        assert "参考资料" * 10 not in user_content(provider.calls[0])


class TestTruncationContinuation:
    def test_length_triggers_bounded_continuation(self):
        # 用 draft 层测续写：plot 层有场景块格式门禁，短片段过不了会先被格式门禁拦掉
        first = make_prose(1, 1200)
        second = make_prose(2, 900)
        provider = MockProvider([first, second])
        tight = ModelLimits(context_window_tokens=32_000, max_output_tokens=300)
        runner = PipelineRunner(provider, limits=LIMITS)
        result = runner.run_layer(SPECS["draft"], [("第一章大纲内容。" * 5, "")])
        assert len(provider.calls) >= 2
        # 续写调用带了断点前文
        assert any("从断点处续写" in user_content(c) for c in provider.calls)
        assert result.calls[0].completion_status in ("complete", "bounded")
