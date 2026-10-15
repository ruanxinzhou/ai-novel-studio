"""回转寿司候选生成 + 编辑态 AI 建议（M2）。

走 chore 便宜通道，结构化 JSON 输出复用 complete_structured（词法修复+重试）。
两种入口：
  - generate_card_candidates(...)：根据 card_key 生成 3 条差异化候选
  - suggest_edit(...)：编辑态 AI 返回完整改后全文 + 理由（suggestion kind）

所有 prompt 都强制注入：
  1. 书级 premise（book.premise / book.world_premise）
  2. 已锁上游卡（core 已经锁→给 obituary 注入，core+obituary 锁了→给 entity 注入）
  3. 主角当前状态（protagonist snapshot，简短）
  4. 差异轴（3 个档位必须显式区分）

不做 COC 仿真——成品级 300-500 字文本由 LLM 直接写，比批仿真快、文本密度更高。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.generation import complete_structured
from app.pipeline.prompts import PromptStore
from app.providers.base import Provider


# ============================================================
# 世界观元规则（所有 prompt 共用；v3 对齐作者真实设定）
# ============================================================

WORLD_RULES = """==== 《第七日的亡者》世界观元规则（铁律，所有写作必须遵守）====

【题材与基调】
- 题材：克苏鲁神话恐怖 × 无限流单元剧长篇，男频悬疑向。
- 结构：一场副本一个独立世界（宇宙/时间线/题材可以是科幻、玄幻、志怪、现实怪谈、影视文学同人等），副本之间有主线暗线串联。
- 基调双轨：戏内（副本七天）= 高压恐怖；戏外（蓝星间章）= 轻松回血日常 + 爽点 + 长线伏笔。

【主角唐迟】
- 二十多岁，蓝星底层社畜；五年前从"地球"平行世界穿越来蓝星，无系统无金手指，唯一的本事是"不引人注目"。
- 性格：惜命、怂、肯记账、市井小聪明、不狠不酷；他不是英雄。
- 成长双线：人间尺度——每场借来的手艺/执念能力逐卷叠加成传奇；神话尺度——他在神眼里始终什么都不是，每场污染都让他更疯狂一点。

【核心机制：讣告 + 七日】
- 每场开场，他在蓝星这边收到一封无邮票无邮戳的公函讣告——上面写着一个陌生人的名字和七天后的死期（死因可能明写，也可能被 ■■■■ 涂黑）。
- 他在蓝星失神的一瞬间（外人看只是发呆/眨眼），意识投射到副本里，附身在讣告上那个死者身上，回到死者死期的七天前。副本里他真实地活了七天七夜，蓝星时间只过一瞬——咖啡还冒着热气，猫碗里粮一粒没少。
- 通关条件：活过第七天 = 回蓝星自己身体；第七天没扛过 = 真死。
- 副本主事件线严格按 Day1→Day7 推进，第七天=死期这根锚绝不打破；但叙事时间允许非线性（闪回/NPC视角/时间诡计/嵌套骗局），所有非线性必须服务于"七日死期"这根锚。
- 副本题材外壳不限，唯一共同硬特征：唐迟附体的这个人（或他最终发现自己真正附身的那个人）七天后会死。

【唐迟在副本里替换谁】
- 唐迟在副本里可能替换任意角色——可能是死者本人（最常见）、可能是某个 PL 调查员、可能是本地 NPC、可能是 Boss、甚至可能是幕后黑手；不同副本不一样。
- 他不一定开场就知道自己是谁：诡计副本里他可能被植入虚假记忆，以为自己是副本里的普通人，后来才发现身份错配、时间错配、讣告名字和身体名字不一样，需要自己揭穿虚假层重锚真相。
- 常规副本里他醒来就知道自己是唐迟、知道七天死期；诡计副本里他开场可能连自己是外来者都不知道。

【战利品与污染（跨界搬运）】
- 每场副本结束，他会带回三类东西——
  ①主动收获（爽点源）：死者的手艺/技能、副本里学到的知识、能揣在身上带回来的小物件（铜钱/黄符/戒指/纸条等体积小的物品，必须跟他结过因果或承载过神话触碰）。
  ②被动污染（疯狂源）：死者的执念残影、噩梦、幻觉、邪神标记/注视；这些永久叠加但慢慢减弱，叠多了分不出哪个是自己。
  ③跨界溢出（卷二起，中后期）：邪神仆从/邪物/异常现象可能顺着他漏到蓝星——这是蓝星被污染、最终"蓝星也是戏"伏笔被揭开的物理路径。
- 金手指=死者执念上身（技能+情绪合一），每次使用都有"执念相关的人间副作用"（不是抽象 SAN 值，是具体可写的人间代价）。
- 唐迟不能带回大体积物品、不能带回活人（中后期某些邪物/滞留派是例外，但那是禁忌事件，非常规机制）。

【克苏鲁神系】
- 幕后导演是奈亚拉托提普（Nyarlathotep，外神的信使/愚弄者），祂发讣告、搭布景、看戏、通过事件快慢表达喜怒（觉得有趣→怪物慢半拍；觉得无聊→杀意快三分）。
- 奈亚不显形、不开口、不与主角对话；全书前期不点明"奈亚"这个名字，主角通过副本里的只言片语、禁忌知识、其他扮演者之口、残页线索慢慢拼出真相。
- 克苏鲁原典神（哈斯塔、克苏鲁、约格·索托斯、莎布等）与原创神并存，诸神不是一伙的——他们之间有古老恩怨/契约/势力范围。主角中后期可以借力（向别的邪神换能力/情报），但每借力一次污染叠一层。
- 其他被选中者（"扮演者"）不止唐迟一个，分几派：逃离派（想脱离循环，主角潜在同路人）、皈依派（拜邪神，专门给主角上难度）、滞留派（借邪神力量滞留在别人身体里不老不死，是非常可怕的老怪物）。神和神之间通过扮演者在博弈。

【讣告的三层功能】
- ①邀请函：下场戏讣告送达即赴约；
- ②死期通知：姓名+死期+死因（或涂黑）；
- ③后日谈入口：散场时讣告自焚，背面残页烧不掉——残页留在唐迟蓝星家中，是查看"这场副本原本的命运被改变成什么样""副本世界观线索"的唯一入口。蓝星间章通常由读残页切入。

【蓝星现实线（主时间线）】
- 蓝星是他"回来喂猫"的家——有猫（社畜标配、没台词、好描写、卷一最稳的情绪锚），还有随卷推进慢慢出现的蓝星羁绊人物（同事/邻居/其他扮演者伪装成的普通人）。
- 蓝星时间完全线性连续、不受副本影响；他"眨眼"回来时猫碗还是满的，但他后背全是冷汗。
- 蓝星间章功能：压力释放（高潮后舒缓）、爽点兑现（带回来的手艺在日常里不小心用出来）、长线伏笔（蓝星在被污染，从猫哈气→邻居异常→最后揭示"蓝星也是戏"）。
- 每场副本后有 2-5 章蓝星间章，双层结构：既有单场收尾段（读残页+爽点+污染征兆），也有卷级主线板统筹整卷蓝星伏笔/羁绊进展/污染梯度。
- 卷十核心反转：蓝星本身也是奈亚搭的布景——他从未真正"回家"；他拼死拼活要喂的猫、要回来的生活，可能都是戏。

【终局方向（伏笔级，前期不明说）】
- 末卷唐迟意识到这是无尽表演循环→发现蓝星也是戏→卷十讣告上死者是他自己的名字→他主动选择"对神来说最无聊的死法"（第七日主动放弃挣扎）→奈亚觉得无趣，注意力消退→他凭前面卷积累的伏笔和伙伴（可能是之前信任过的一个扮演者附在猫身上）在神不看的地方复生。
- 核心讽刺：克苏鲁世界里"活下来"的方式不是战胜神，是让神觉得看你没意思。结局半疯狂活着，身上留诡异痕迹。

【创作自由度硬约束】
- 副本题材外壳自由：科幻、玄幻、志怪、影视/文学同人、虚拟游戏、纯现实怪谈都可以，不要局限在"乡土民俗克苏鲁"。
- 副本可以嵌套叙事诡计（虚假记忆/虚假世界/身份错配/时间诡计），唐迟不一定开场就知道真相。
- 叙事可以非线性（闪回/NPC视角/插章），但 Day1-Day7 的七日锚必须始终清晰。
- 不要每本都套"地点/日常/异常/规则/钩子+替位点一句话"的五段模板——那只是常规副本的推荐写法，诡计副本/科幻副本/玄幻副本有各自合适的结构。
- 唐迟绝不能被写成副本里的本地员工/居民/本来就在这里生活的人——除非那是诡计副本的"虚假记忆层"，后面要揭穿。
"""

# 给聊天/编辑/COC 模拟等非候选场景用的简短版世界观（省 token）
WORLD_RULES_SHORT = """【世界观铁律】
- 题材：克苏鲁神话恐怖 × 无限流单元剧长篇，男频悬疑；副本题材不限（科幻/玄幻/志怪/同人/怪谈皆可），底层永远是克苏鲁。
- 主角唐迟：怂、惜命、社畜、不英雄；每封讣告让他在蓝星失神一瞬穿入副本，附身一个七天后会死的人（可能是 PL/NPC/Boss，诡计副本里他开场可能不知道自己是谁），活过七天回蓝星，死了就真死。
- 幕后神是奈亚拉托提普（前期不点名字），诸神不和，主角可借力但付疯狂代价；其他扮演者分逃离/皈依/滞留三派。
- 带回物分三类：手艺/小物件（爽点）；执念残影/邪神标记（永久污染）；中后期邪物/仆从可能漏到蓝星。
- 蓝星是日常回血+爽点+长线伏笔；蓝星时间线性连续（副本七天=蓝星一瞬）；猫是最早的情绪锚，后面会加羁绊人物。
- 终局："蓝星也是戏"，主角靠"让神觉得无聊"脱身。
- 写作基调：戏内高压恐怖，戏外轻松回血；可以非线性叙事，但 Day1-Day7 七日锚必清晰。
- 直接给正文/答案，不要"收到/好的/我理解了"这类客套开场。"""


# 【蓝星间章】基调提示：供 daily_write / 回转寿司 在 is_interlude=true 或 phase=settle_up 时拼入系统提示
INTERLUDE_PREAMBLE = """==== 【这是蓝星间章】====
基调轻松回血、有生活气/爽点/羁绊互动，**不渲染七日倒计时**，不出现副本内的高压恐怖。
重点渲染：
- **蓝星羁绊**（猫、邻居、同事、其他伪装者）日常互动的温度；
- **猫**的小细节（猫碗、蹭手、哈气、异常行为）——它是最早最稳的情绪锚；
- **带回物件/手艺**在日常里不小心用出来的爽点兑现（爽而不霸，市井小聪明式）；
- **污染征兆**的细小异样（猫盯空、冰箱里多了不该有的东西、倒影里多了个人影），不要升级成恐怖大场面，点到即止留伏笔。
结尾留一个让读者会心一笑或者后背一凉的小钩子，但绝不发下一封讣告（下一场讣告由 settle_up 阶段末送达）。"""


# ============================================================
# 卡片差异轴配置
# ============================================================

# 设计原则（v2.9 改版）：
# - 不再用"三档硬锁"把 AI 卡在单一维度上。以前的"民俗阴冷/规则冷峻/诡异日常"这种
#   三档只是【参考种子】，不是必须严格匹配的分类盒。
# - 每张卡给 AI 一张【差异维度菜单】，列出多个常见的差异化维度，让 AI 自由挑选、组合、
#   甚至自己发明新的差异点。核心要求只有一个：N 张卡必须让作者一眼看出差别。
# - 每张卡用 `twist`（一句话差异点自陈）替代档位名，AI 自己写"这一版独到在哪"。
# - 实体卡（entity_card）特殊：它不是"挑一个角色类型"，而是"一次出 N 个功能互补的角色"。
#
# 字段约定：
#   mode: "diverge"（N 版分歧候选，作者选 1）| "ensemble"（一组互补角色/元素，作者全收/挑/改）
#   default_n: 默认出几版
#   inspiration_seeds: 参考种子（给 AI 灵感，非硬分类）
#   dimensions_menu: 可以拉开差异的维度清单（告诉 AI 可以从这些角度做差异化，非必填）
#   length_guide: 字数/内容/格式硬性要求
#   axis_label: 前端展示用一句话提示

CARD_AXES: dict[str, dict] = {
    "core": {
        "mode": "diverge",
        "default_n": 5,
        "axis_label": "五版副本骨架：题材/诡计/替换角色/恐怖来源/节奏自由组合",
        # 这些只是灵感种子，AI 不必局限于此
        "inspiration_seeds": [
            {"label": "基调参考", "options": ["民俗阴冷（乡土/祠堂/旧俗/仪式错位）",
                                              "规则冷峻（明文禁忌/制度恶意/试错活命）",
                                              "诡异日常（当代秩序里长出不对劲）",
                                              "科幻深空（封闭空间站/外星遗迹/观测悖论）",
                                              "古风玄幻（修仙宗门/古国王朝/志怪山泽）",
                                              "同人改设（影视/文学/游戏世界的克苏鲁化重写）"]},
            {"label": "诡计类型", "options": ["无诡计·线性七日",
                                              "身份错配（唐迟开场不知自己是谁/被植入虚假记忆）",
                                              "时间诡计（循环/倒带/Day7 早就在 Day1 发生过）",
                                              "认知层诡计（副本本身是某人的梦境/遗物/脑内世界）",
                                              "嵌套戏中戏（蓝星也可能在被观测）"]},
            {"label": "唐迟替换谁", "options": ["死者本人（最常见，将死本地角色）",
                                                "外来 PL 调查员",
                                                "本地 NPC（店主/警察/殡葬人等）",
                                                "Boss/反派本人",
                                                "幕后黑手（开场以为自己是普通人）"]},
            {"label": "恐怖来源", "options": ["外神/古神注视",
                                              "本地精怪/邪物",
                                              "规则本身（人写的规则被什么东西利用）",
                                              "人心（其他扮演者/本地人做局，诡异是包装）",
                                              "混合型（诡异+人心互相喂食）"]},
        ],
        "length_guide": (
            "400-700 字成品可读的副本骨架文本（小说化笔调，不是提纲）。副本核是这个世界本身的故事，唐迟入场前的氛围和规则都要能让作者看得心痒痒想写。"
            "结构不强制套固定模板，但需要自然地写出以下信息（可以融合、可以打乱顺序、可以按题材灵活取舍）："
            "①【副本外壳】题材（科幻/玄幻/志怪/同人/日常怪谈等）、地点、时间、封闭感从哪来；"
            "②【日常风貌】当地人怎么活、表面秩序是什么、什么让读者觉得'这个地方有点怪'；"
            "③【异常征兆】开场的诡异画面（不一定是唐迟视角，也可以是副本本地人日常里早就习以为常的一个瘆人细节）；"
            "④【规则/禁忌】这个副本里那种不可言说的东西如何运作、违反什么会死、规则如何触发（要自洽、有代价）；"
            "⑤【认知差钩子】最后背发凉的错位/反常识点（即'真正吓到人'的那个点）；"
            "⑥【唐迟入场锚】（一句话）这场里唐迟最终替换的是谁——给出姓名/身份/他为什么七天后会死。如果是诡计副本、唐迟开场不知道自己替换的是谁，可以写'表层身份+可疑疑点'，为后续揭穿留钩子。"
            "注意：副本核描述的是副本本身，不是唐迟的冒险传记；副本里的人物都是独立 NPC，有自己的命运。唐迟穿入瞬间的慌乱、认知、穿入感受留给章节正文写。"
        ),
    },
    "obituary_card": {
        "mode": "diverge",
        "default_n": 3,
        "axis_label": "三版讣告：送达方式/死者自知/死因明写涂黑/进场钩子自由组合",
        "inspiration_seeds": [
            {"label": "死亡性质参考", "options": ["离奇横死（死状反常/细节对不上）",
                                                  "宿命应验（多年前的诅咒/诺言第七天履约）",
                                                  "人为局中局（有人利用诡异传说包装凶杀/阴谋）",
                                                  "被当成别人（唐迟替换的人其实不是'那个人'，是替身）",
                                                  "自杀疑云（表面自杀，实为被规则/邪物操控）"]},
            {"label": "送达方式", "options": ["实体公函（贴门/夹报纸/办公桌）",
                                              "猫叼来/动物衔来",
                                              "电子（手机短信/APP 推送/监控画面里出现）",
                                              "口头（邻居笑着递来一句'你讣告到了'）",
                                              "自己写的（唐迟发现自己笔迹的讣告）"]},
            {"label": "死因写法", "options": ["明写具体死法",
                                              "■■■■ 涂黑留白",
                                              "半遮半露（写一半被污渍盖住）",
                                              "写的是别人/写错名字"]},
            {"label": "进场第一眼", "options": ["死者视角醒来（不认识自己）",
                                                "第三人称旁观（看见自己的尸体/名字牌）",
                                                "正在死的瞬间（从死法中途切入）",
                                                "日常切片（在上班/吃饭，突然意识到不对）"]},
        ],
        "length_guide": (
            "200-350 字讣告文（就是唐迟在蓝星打开那封公函读到的文字 + 作者补充的注解）。"
            "需要包含：①封面感——送达场景（这封讣告在什么情境下落到唐迟面前）；"
            "②死者信息——姓名（或化名）、年龄、生前身份（可能模糊）、死期（第七天的具体日期/时辰）、死因（明写或 ■■■■ 涂黑）；"
            "③旁人视角——邻里/同事/家人对这个人的印象、关于他死亡的一句传言/未解疑点；"
            "④进场钩子——唐迟读完这封讣告后失神穿入，醒来第一眼看到的画面（一句话即可）。"
            "不要写成警方报告，要有温度有留白，要让唐迟（和读者）读完心一沉。"
        ),
    },
    "entity_card": {
        "mode": "ensemble",
        "default_n": 3,
        "axis_label": "一次出 3 个功能互补的角色/邪物（可以反复点继续加）",
        # ensemble 模式：一次出 3 个，每个承担不同叙事功能，作者可以全收/挑/自己改
        "role_menu": [
            {"value": "关键证人", "guide": "死者/事件身边一个不起眼但知道关键真相的人，他不主动说，被问到时会漏出让事翻盘的信息；他自己也因知道得太多处在危险中。"},
            {"value": "误导者", "guide": "表面嫌疑最大/最像反派，气场诡异行迹可疑，把读者往错方向引；他的怪异其实有合理的私人原因（隐疾/私情/自己也在查/他也是受害者），真正的诡异在他身后。"},
            {"value": "暗线伏笔人", "guide": "看似无关的边缘人/小人物，但身上带跨场伏笔——他认识上一场的死者、他持有下一场才会用到的物件、他一句闲聊 Day7 才回响、他身上有某神的印记；存在感低但标签鲜明。"},
            {"value": "其他扮演者", "guide": "另一个被神选中的戏子——逃离派同路人/皈依派反派/滞留派老怪物；进场必须有'不协调感'（反应不像本地人/知道不该知道的事/看唐迟眼神不对）。"},
            {"value": "本地权力者", "guide": "制定规则/执行规则的人（镇长/院长/站长/班主任/保安队长），他既可能是诡异的帮凶、也可能是在烂规则里尽力保人的普通人、更可能是半知情半糊涂的棋子。"},
            {"value": "情感锚点", "guide": "让唐迟（读者）动情绪的人——一个小孩、一个老人、一个像蓝星某人的本地人、一个对唐迟真诚的人；他/她的结局牵动读者的情绪（不一定得救，不一定死，刀糖都可以）。"},
            {"value": "邪物/污染现象", "guide": "不是人——邪神仆从、被污染的物品、异常现象载体（镜子/录音/半夜响的电话/跟着主角的动物）；承载某神注视或规则，接触有代价，可能带回蓝星。"},
        ],
        "length_guide": (
            "一张实体卡。每张卡必须先写清楚【叙事作用】（这一角色/邪物在本场承担什么功能）。"
            "如果是人物：姓名、身份年龄、外貌辨识点（一句话让作者写的时候能想起脸）、与唐迟/死者的关系、表层性格、隐藏秘密/他知道什么、当前状态、有没有认出唐迟不是本地人。"
            "如果是邪物/污染现象：名称/外观、出现规律/触发条件、它承载什么力量或诅咒、接触代价、可能不可能带回蓝星。"
            "N 张卡之间叙事功能必须互补，不能重复（不要三个都是关键证人）。"
            "用紧凑卡片文本写，不分小标题，单张控制在 200-350 字。"
        ),
    },
    # 杀青/残页卡：散场后填；回转寿司可选支持
    "wrapup": {
        "mode": "diverge",
        "default_n": 3,
        "axis_label": "三版杀青：冷收/余味/刀糖（可混合），带回物/污染/蓝星钩子自由组合",
        "inspiration_seeds": [
            {"label": "情绪定调参考", "options": ["冷收（以为救了其实没救、以为逃了其实被标记，不甜不暖）",
                                                  "余味（残页里一个让人合上书想十分钟的后日谈细节）",
                                                  "刀糖（有人替他死/动了真心，蓝星日常细节触发回忆，有爽点也有刀）",
                                                  "荒诞笑（黑色幽默收束，用一个日常荒诞画面缓释恐怖）"]},
            {"label": "杀青账的核心债", "options": ["欠一个人一条命",
                                                    "弄坏了蓝星一件事（某人/某物/某关系被污染）",
                                                    "手艺赚到了但代价是身上多一个印记",
                                                    "以为还清了其实被某神记上名字"]},
            {"label": "蓝星小钩子", "options": ["猫反常（哈气/盯着某处/叼来东西）",
                                                "邻居/熟人一句怪话（被污染初兆）",
                                                "带回物件在蓝星触发小事（爽点兑现）",
                                                "下一封讣告预兆（但不正式送达）"]},
        ],
        "length_guide": (
            "400-600 字杀青段：①唐迟回蓝星瞬间的身体感受（冷汗/手抖/某样小物件还在口袋里发烫）；②读残页（残页上写了什么——这个副本原本的命运是什么样的、唐迟改了什么、哪些人活下来了/没活下来、留下什么世界观线索）；"
            "③本场杀青记录一句话（小本本上多一行'第X场：xxx，借了XX手艺，欠了XX债'）；④本场带回物品/手艺/污染（明确列清楚，便于作者后续用）；⑤蓝星日常一个小钩子（猫哈气、邻居一句怪话、爽点兑现、下一封讣告可能送达的预兆——但不正式送达，要等下一场）。"
        ),
    },
}


def supported_card_keys() -> tuple[str, ...]:
    """M2 支持回转寿司的卡（其余卡继续走旧 textarea）。"""
    return tuple(CARD_AXES.keys())


# ============================================================
# 上下文拼装
# ============================================================

def _build_context_block(
    *,
    book_store,            # BookStore
    fs,                    # FieldStore
    es,                    # EntityStore
    field_no: int,
    card_key: str,
    extra_context: str = "",
    volume_store=None,     # VolumeStore（可选）
    volume_no: int | None = None,   # 当前卷号
    phase: str = "",       # 当前阶段：settle_up/kindle/...
    is_interlude: bool = False,     # 是否蓝星间章
) -> str:
    """把上游已锁内容 + 主角状态拼成 prompt 上下文块。
    支持卷规划板注入、间章/settle_up 阶段前置蓝星信息。
    """
    sections: list[str] = []

    # 书级 premise（用 premise 卡 render，不用 render_all 以免 context 过长）
    try:
        premise_text = ""
        if hasattr(book_store, "premise"):
            prof = book_store.premise()
            if prof and hasattr(prof, "render"):
                premise_text = prof.render().strip()
        if premise_text:
            sections.append(f"【书级设定 premise】\n{premise_text}")
    except Exception:
        pass

    # 作者上传的跑团剧本改编稿（如果有）
    if extra_context.strip():
        sections.append(f"【作者提供的素材（跑团剧本版权规避改编稿，作为灵感来源，不要照抄专名）】\n{extra_context.strip()}")

    # ---- 主角状态（当前副本 + 长期累积） ----
    # 若当前是间章或 settle_up 阶段，把蓝星羁绊/猫/带回物件/污染度**前置加粗**
    blue_star_focus = bool(is_interlude or phase == "settle_up")

    try:
        p = es.protagonist
        loc = p.current_location or "（未设定位置）"
        body = p.current_body_name or "唐迟"
        identity = p.current_body_identity or "（身份未明）"
        ero = p.erosion_level
        sanity = p.sanity_hint or "状态稳定"
        contam = p.contamination_level
        held = "、".join([eid for eid in p.held_items][:5]) or "无特殊持有物"

        # 【蓝星羁绊 / 猫 / 带回物件 / 污染度】——间章/settle_up 前置加粗
        if blue_star_focus:
            front_lines = ["【★★★ 当前蓝星状态（重点渲染） ★★★】",
                           f"**跨卷累积污染度：{contam}/100**（已完成副本 {p.field_count} 场）"]
            # 蓝星羁绊（猫优先）
            if p.blue_star_bonds:
                bond_desc = "、".join(
                    f"**{b.get('name','?')}**（{b.get('relation','?')}，羁绊{b.get('depth','浅')}）"
                    for b in p.blue_star_bonds
                )
                front_lines.append(f"**蓝星羁绊：{bond_desc}**")
            else:
                front_lines.append("**蓝星羁绊：目前只有猫（煤球/没名字——作者可随时取名）**")
            # 带回物品
            if p.brought_items:
                bi_desc = "、".join(
                    f"{b.get('name','?')}（污染{b.get('contam',0)}，{b.get('effect','?')}）"
                    for b in p.brought_items[-6:]
                )
                front_lines.append(f"**带回复本物品：{bi_desc}**")
            else:
                front_lines.append("带回复本物品：暂无")
            # 手艺（在日常里可以爽点兑现）
            if p.active_crafts:
                craft_desc = "；".join(
                    f"{c.get('name','?')}（熟练度{c.get('proficiency','初窥')}，{c.get('from_field','?')}得）"
                    for c in p.active_crafts[-4:]
                )
                front_lines.append(f"已掌握手艺（可在日常自然用出）：{craft_desc}")
            # 残影 / 邪神标记（污染征兆来源）
            if p.dead_echoes:
                echo_desc = "、".join(
                    f"{e.get('name','?')}（{e.get('obsession','执念未明')}，消退{e.get('fade_stage','?')}）"
                    for e in p.dead_echoes[-3:]
                )
                front_lines.append(f"死者残影（夜里会做梦/闪回）：{echo_desc}")
            if p.god_marks:
                mark_desc = "、".join(
                    f"{m.get('god','?')}·{m.get('seal_form','?')}（风险：{m.get('risk','?')}）"
                    for m in p.god_marks[-3:]
                )
                front_lines.append(f"邪神标记：{mark_desc}")
            sections.append("\n".join(front_lines))

        # 副本身份（默认顺序；间章阶段省略副本内位置，只说"刚回蓝星"）
        lines = ["【主角唐迟当前状态】"]
        if not blue_star_focus:
            lines += [
                f"本场身份：{body}（{identity}）",
                f"本场位置/天数：{loc}" + (f" · 第 {p.current_day} 天" if p.current_day else ""),
                f"本场侵蚀度：{ero}（{sanity}）",
                f"跨卷累积污染度：{contam}/100（已完成副本 {p.field_count} 场）",
                f"本场持有物：{held}",
            ]
        else:
            lines += [
                "当前位置：**蓝星·出租屋**（刚从上一场副本失神归来，咖啡还热，猫碗粮没少）",
                f"本场身份：**唐迟本人**（不是任何副本里的人）",
                f"跨卷累积污染度：{contam}/100（已完成副本 {p.field_count} 场）",
            ]
        # 手艺 / 残影 / 邪神标记 / 带回物品 / 蓝星羁绊：非间章按常规顺序补全
        if not blue_star_focus:
            if p.active_crafts:
                craft_desc = "；".join(
                    f"{c.get('name','?')}（熟练度{c.get('proficiency','初窥')}，{c.get('from_field','?')}得）"
                    for c in p.active_crafts[-3:]
                )
                lines.append(f"已掌握手艺：{craft_desc}")
            if p.dead_echoes:
                echo_desc = "、".join(
                    f"{e.get('name','?')}（{e.get('obsession','执念未明')}，消退{e.get('fade_stage','?')}）"
                    for e in p.dead_echoes[-3:]
                )
                lines.append(f"死者残影：{echo_desc}")
            if p.god_marks:
                mark_desc = "、".join(
                    f"{m.get('god','?')}·{m.get('seal_form','?')}（风险：{m.get('risk','?')}）"
                    for m in p.god_marks[-3:]
                )
                lines.append(f"邪神标记：{mark_desc}")
            if p.brought_items:
                bi_desc = "、".join(
                    f"{b.get('name','?')}（污染{b.get('contam',0)}，{b.get('effect','?')}）"
                    for b in p.brought_items[-4:]
                )
                lines.append(f"带回复本物品：{bi_desc}")
            if p.blue_star_bonds:
                bond_desc = "、".join(
                    f"{b.get('name','?')}（{b.get('relation','?')}，羁绊{b.get('depth','浅')}）"
                    for b in p.blue_star_bonds
                )
                lines.append(f"蓝星羁绊：{bond_desc}")
        # 已知真相
        if p.known_truths:
            truth_desc = "；".join(p.known_truths[-5:])
            lines.append(f"已知真相碎片：{truth_desc}")
        sections.append("\n".join(lines))
    except Exception:
        pass

    # ---- 本卷规划板（M3：如果绑定了卷号且能拿到 VolumeStore，则注入） ----
    if volume_store is not None and volume_no is not None:
        try:
            vol = volume_store.get(volume_no)
            if vol is not None:
                vol_lines = [f"【本卷规划板·卷{vol.volume_no}·{vol.title or '未命名'}】",
                             f"基调弧线：{vol.tone_arc or '（未设定）'}",
                             f"计划蓝星间章数：{vol.planned_interludes}",
                             f"本卷结束预期污染度：{vol.contamination_goal}/100"]
                if vol.foreshadows_to_plant:
                    vol_lines.append("本卷要埋的长线伏笔：" + "；".join(vol.foreshadows_to_plant))
                if vol.bonds_to_deepen:
                    vol_lines.append("本卷要推进的蓝星羁绊：" + "、".join(vol.bonds_to_deepen))
                if vol.gods_to_hint:
                    vol_lines.append("本卷要隐晦出现的神明/势力：" + "、".join(vol.gods_to_hint))
                if vol.satisfaction_beats:
                    vol_lines.append("本卷计划爽点：" + "；".join(vol.satisfaction_beats))
                # 七层反转进度（只提示已铺垫/已到位的层）
                active_layers = [f"第{k}层={v}" for k, v in vol.endgame_layer_hints.items() if v != "未开始"]
                if active_layers:
                    vol_lines.append("终局七层反转进度：" + "；".join(active_layers))
                if vol.post_field_settle:
                    # 上一场归账（settle_up 阶段刚进时用）
                    ps = vol.post_field_settle
                    vol_lines.append("【上一场杀青账】" + (ps.get("kill_record_line") or ""))
                sections.append("\n".join(vol_lines))
        except Exception:
            pass

    # ---- 间章提示段落（is_interlude 或 phase=settle_up 时直接作为写作基调约束注入） ----
    if blue_star_focus:
        sections.append(INTERLUDE_PREAMBLE)

    # 上游已锁卡（按依赖顺序：core -> obituary_card -> entity_card -> wrapup）
    upstream_order = ["core", "obituary_card", "entity_card", "wrapup"]
    labels = {
        "core": "副本核（已锁）",
        "obituary_card": "讣告卡（已锁）",
        "entity_card": "出场角色/邪物（已锁）",
        "wrapup": "杀青/残页（已锁）",
    }
    idx = upstream_order.index(card_key) if card_key in upstream_order else 0
    for up_key in upstream_order[:idx]:
        text = ""
        try:
            if up_key == "core":
                fw = fs.get(field_no)
                text = (fw.core if fw else "") or ""
            else:
                text = fs.get_card_text(field_no, up_key) or ""
        except Exception:
            text = ""
        if text.strip():
            sections.append(f"【{labels[up_key]}】\n{text.strip()}")

    return "\n\n".join(s for s in sections if s.strip())


# ============================================================
# 候选生成
# ============================================================

CANDIDATE_SYSTEM = ("你是《第七日的亡者》（克苏鲁×无限流×男频长篇连载）专职卡片写手。"
"你的任务是给作者一次性产出 N 张差异化候选卡，每张都是成品级可读文本，不是提纲或要点列表。\n\n"
+ WORLD_RULES + "\n\n"
"==== 写卡硬性要求 ====\n"
"- 副本核（core）：写的是副本本身（独立故事世界），不是唐迟传记；副本人物都是独立 NPC，有自己的命运；唐迟穿入瞬间留给正文写。\n"
"- 讣告卡（obituary_card）：是唐迟在蓝星打开那封公函读到的文字+注解；要有送达场景、死因可涂黑、要有进场第一眼画面。\n"
"- 实体卡（entity_card）：一次出 N 张功能互补的角色/邪物，每张承担不同叙事功能（不要重复），可以是人（关键证人/误导者/暗线伏笔/其他扮演者/本地权力者/情感锚点等）也可以是邪物污染；注意其他扮演者进场要有'不协调感'、邪物要写接触代价。每张卡开头先点明【叙事作用】。\n"
"- 杀青/残页卡（wrapup）：散场后写——回蓝星瞬间+读残页（后日谈）+本场杀青账+带回物品/手艺/污染+蓝星小钩子（不正式发下一封讣告）。\n\n"
"==== 差异化要求（核心！）====\n"
"1. 作者给出的'参考种子/维度菜单'只是灵感库，【不是】必须严格匹配的分类盒。你可以从中选、可以组合、可以混合、也可以自己创造新方向——唯一硬要求是：N 张卡放在一起必须让作者一眼能看出差别，每张都有自己独到的切入角度。\n"
"2. 不要为了凑差异化而故意写烂版/极端版；每一版单独看都必须是作者愿意直接锁定用的成品水准。\n"
"3. 差异化可以体现在任何维度上：基调/诡计类型/题材外壳/唐迟替换谁/恐怖来源/节奏/情绪定调/钩子类型/人物关系结构……你自己判断哪几个维度组合起来能让 N 版真正拉开差距。\n\n"
"==== 通用硬性规则 ====\n"
"1. 只输出一个 JSON 数组，数组里每个元素是一张候选卡，结构：\n"
"   {\"twist\": \"一句话自陈这一版的核心差异点（给作者判断用，20字内，比如'身份错配诡计+唐迟替换幕后'/'猫叼来的手写讣告+死因全涂黑'/'冷收+猫反常钩子'）\",\n"
"    \"rationale\": \"一句话解释这一版的写法和独到之处（给作者判断用，30字内）\",\n"
"    \"content\": \"候选成品文本\"}\n"
"2. twist 字段是'差异点标签'，你自己写，不要套任何固定档位名；标签要让作者扫一眼就知道这一版和其他版差在哪。\n"
"3. content 必须是成品可读小说化文字，不是要点罗列，不带 markdown 标题/编号/说明。\n"
"4. 不要复述【上下文】里的内容，把它当素材直接消化进文字。\n"
"5. 严格遵守每张卡各自的字数/格式要求。\n"
"6. 不要输出数组以外的任何文字，不要加解释前后缀。\n"
"7. 绝对禁止写'好的/收到/以下是/我为你准备了'这类客套话——content 直接是正文。")


@dataclass
class CardCandidatesResult:
    candidates: list[dict]       # [{twist, rationale, content}]
    attempts: int


def generate_card_candidates(
    provider: Provider,
    prompt_store: PromptStore,
    *,
    card_key: str,
    context_block: str,
    user_hint: str = "",
    n: int | None = None,
) -> CardCandidatesResult:
    """生成 N 张回转寿司候选卡。card_key 必须在 CARD_AXES 里。"""
    if card_key not in CARD_AXES:
        raise ValueError(f"M2 暂不支持为 {card_key} 生成候选（仅支持：{sorted(CARD_AXES.keys())}）")
    axis = CARD_AXES[card_key]
    target_n = n if n is not None else axis.get("default_n", 3)
    card_labels = {
        "core": "副本核（副本自身故事骨架）",
        "obituary_card": "讣告卡（公函讣告+进场钩子）",
        "entity_card": "实体卡（功能互补的角色/邪物组合）",
        "wrapup": "杀青/残页卡（回蓝星+读残页+杀青账+污染/爽点）",
    }
    card_label = card_labels.get(card_key, card_key)
    mode = axis.get("mode", "diverge")

    # 拼装灵感菜单文本
    seeds_parts: list[str] = []
    if mode == "ensemble" and axis.get("role_menu"):
        seeds_parts.append(f"==== 可用叙事功能菜单（{target_n} 张卡必须从中选 {target_n} 个不重复的功能，每个功能出一张）====\n")
        for r in axis["role_menu"]:
            seeds_parts.append(f"- 【{r['value']}】：{r['guide']}\n")
    if axis.get("inspiration_seeds"):
        seeds_parts.append("\n==== 参考灵感维度（仅作灵感，不是硬分类，可以任意组合/混合/自创）====\n")
        for dim in axis["inspiration_seeds"]:
            seeds_parts.append(f"- {dim['label']}：{' / '.join(dim['options'])}\n")
    seeds_text = "".join(seeds_parts)

    mode_instr = (
        f"这是【ensemble 组合模式】：一次产出 {target_n} 张功能互补的卡，每张承担不同叙事功能，作者可以全收/挑/继续加。"
        if mode == "ensemble"
        else f"这是【diverge 分歧模式】：产出 {target_n} 张互相独立的候选版本，作者从中挑最对味的一版锁定。"
    )

    user_block = (
        f"请为【{card_label}】产出 {target_n} 张卡。\n\n"
        f"{mode_instr}\n"
        f"创作导向提示：{axis['axis_label']}\n\n"
        f"{seeds_text}\n"
        f"字数/格式要求：{axis['length_guide']}\n"
    )
    if context_block:
        user_block += f"\n{context_block}\n"
    if user_hint.strip():
        user_block += f"\n【作者额外要求（优先级最高，必须严格兑现）】\n{user_hint.strip()}\n"

    messages = [
        {"role": "system", "content": CANDIDATE_SYSTEM},
        {"role": "user", "content": user_block},
    ]

    def generate() -> str:
        return provider.chat(messages).text

    completion = complete_structured(generate, max_regenerations=2)
    data = completion.data
    cands = _normalize_candidates(data, target_n)
    if not cands:
        raise ValueError(f"候选卡输出连续 {completion.attempts} 次无法解析")
    # 如果解析出的条数比要求的少，用占位补齐（保证前端能渲染 N 格）
    while len(cands) < target_n:
        idx = len(cands) + 1
        cands.append({
            "twist": f"第{idx}版（占位）",
            "rationale": f"第{idx}版 AI 输出不完整，已兜底占位",
            "content": f"（第{idx}版候选生成失败，可点击「换一拨」重试，或自己在编辑框里写。）",
        })
    return CardCandidatesResult(candidates=cands, attempts=completion.attempts)


def _normalize_candidates(data: object, target_n: int) -> list[dict]:
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    seen_twists: set[str] = set()
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        # 兼容旧字段 axis_value → 新字段 twist
        twist = str(item.get("twist", "") or item.get("axis_value", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        rationale = str(item.get("rationale", "") or "").strip()
        if not content or len(content) < 30:
            continue
        if not twist:
            twist = f"第{len(out)+1}版"
        # twist 去重（防止 AI 给出重复标签）
        base_twist = twist
        suffix = 2
        while twist in seen_twists:
            twist = f"{base_twist}·{suffix}"
            suffix += 1
        seen_twists.add(twist)
        out.append({
            "twist": twist[:24],           # 标签不超过 24 字，避免标签区挤爆
            "rationale": rationale or twist,
            "content": content,
        })
        if len(out) >= target_n:
            break
    return out


# ============================================================
# 编辑态 AI 建议（suggestion 类型：返回完整改后全文）
# ============================================================

EDIT_SYSTEM = ("你是《第七日的亡者》专职改稿编辑（对话编辑态）。\n"
"作者给你一张卡片当前版本 + 一句具体修改指令，你要：\n"
"1. 返回修改后的【完整全文】（从第一个字到最后一个字，不能只输出改了的片段，不能用省略号）；\n"
"2. 给出一句 30 字内的修改理由（告诉作者你为什么这么改）；\n"
"3. 未被作者指令涉及的部分必须原样保留——不要自作主张润色没被要求改的段落；\n"
"4. 不要输出'好的/收到/以下是修改后版本'这类客套话，也不要输出数组以外的文字。\n\n"
+ WORLD_RULES_SHORT + "\n\n"
"输出严格为一个 JSON 对象：\n"
"{\"content\": \"修改后的完整全文\", \"reason\": \"一句话修改理由\"}")

EDIT_CONTRACT = "未被指令涉及的内容一字不改；输出必须是完整全文，长度与原文量级相当；遵守《第七日的亡者》世界观铁律，不要写出违反设定的内容。"


@dataclass
class EditSuggestionResult:
    content: str
    reason: str
    attempts: int


def suggest_edit(
    provider: Provider,
    prompt_store: PromptStore,
    *,
    card_key: str,
    current_text: str,
    instruction: str,
    context_block: str = "",
) -> EditSuggestionResult:
    """编辑态 AI 返回一版完整改后全文。"""
    card_label = {
        "core": "副本核",
        "obituary_card": "讣告卡",
        "entity_card": "实体卡",
        "wrapup": "杀青/残页卡",
    }.get(card_key, card_key)

    user_block = (
        f"正在修改【{card_label}】。\n\n"
        f"【当前版本】\n{current_text.strip() or '（空白）'}\n\n"
        f"【作者指令】\n{instruction.strip()}\n\n"
        f"{EDIT_CONTRACT}\n"
    )
    if context_block:
        user_block += f"\n{context_block}\n"

    messages = [
        {"role": "system", "content": EDIT_SYSTEM},
        {"role": "user", "content": user_block},
    ]

    def generate() -> str:
        return provider.chat(messages).text

    completion = complete_structured(generate, max_regenerations=2)
    data = completion.data
    content = ""
    reason = ""
    if isinstance(data, dict):
        content = str(data.get("content", "") or "").strip()
        reason = str(data.get("reason", "") or "").strip()
    if not content or len(content) < 20:
        raise ValueError(f"编辑建议输出连续 {completion.attempts} 次无法解析")
    return EditSuggestionResult(content=content, reason=reason, attempts=completion.attempts)


# ============================================================
# 跑团剧本 PDF → 规避版权改编 → 注入回转寿司上下文
# ============================================================

PDF_ADAPT_SYSTEM = ("你是一位克苏鲁无限流小说的「去痕改编师」。作者会把一段跑团剧本/模组原文（可能是科幻/玄幻/志怪/现实怪谈/影视文学同人等任意题材）贴给你，你需要产出一份**版权安全的副本骨架素材**——不是小说正文，是供后续回转寿司生成「副本核」用的素材。\n\n"
+ WORLD_RULES + "\n\n"
"==== 改编硬性规则 ====\n"
"1. 所有专名（人名/地名/组织名/职业/道具名/章节名/神明名/作品名）必须全部替换为原创名，不要保留原模组的任何专有名词；\n"
"2. 原模组的核心事件顺序可以打乱、时间地点可以重置、题材外壳可以迁移（比如把一个美式小镇模组改造成中式老县城/未来空间站/古代志怪），但必须保留'让人后背发凉的那个钩子'——即真正吓到人的错位/反噬/认知差；\n"
"3. 必须新增至少 2 处原模组没有的原创细节（一个环境细节 + 一个人物行为反差点），确保整体是二次创作而非复述；\n"
"4. 如果原模组主角是'出版社校对员/警察/记者/学生'这类调查员身份，请把他改造成副本里的一个独立角色（可以是本地将死 NPC、可以是外来调查员、可以是幕后人物）——他不是唐迟，唐迟会在后续步骤替换他或另一个角色；\n"
"5. 如果原模组自带人物卡（PL/KPC），保留其身份/技能/性格骨架作为后续跑团模拟的PL参照，但名字和背景必须替换；\n"
"6. 不要把唐迟写进改编稿——唐迟是后面才入场的；改编稿里所有人物都是副本独立 NPC/PL；\n"
"7. 如果作者加了特定情节/伏笔要求（要兑现某条跨场伏笔/要安排某个其他扮演者出场/要给主角留什么手艺/要什么题材外壳），必须严格兑现。\n\n"
"==== 输出 JSON 结构 ====\n"
"{\n"
"  \"adapted_premise\": \"500-800 字的原创副本骨架，小说化笔调。自然写出：题材外壳+地点+时间+封闭感、当地人日常与表面秩序、开场异常征兆、核心规则/禁忌（违反会死、如何触发、代价）、后背发凉的认知差钩子、可供唐迟替换的候选角色（至少 2 个：一个将死本地角色 + 一个其他角色——可能是PL/NPC/Boss/幕后），每个候选给出姓名/身份/他为什么七天后死/他有什么执念/继承什么手艺/社会关系/未了的债。允许诡计副本设计（身份错配/虚假记忆/时间诡计），可以标注'表层 vs 真实层'。不要提原模组任何名字。\",\n"
"  \"player_cards\": [\n"
"    {\"name\":\"原创名\",\"role\":\"在副本里的身份定位（PL调查员/KPC关键NPC/本地普通人/可能被唐迟替换的候选）\",\"occupation\":\"职业\",\"san_max\":数字,\"hp_max\":数字,\"key_skills\":{\"技能名\":数字值},\"personality\":\"一句话性格\",\"secret\":\"他隐藏的秘密/未了的执念\"}\n"
"    // 如果原模组自带人物卡则按原卡骨架改写，否则原创 3-4 张人物卡（包含不同身份/性格/技能组合，为后续跑团模拟做准备）\n"
"  ],\n"
"  \"renamed_cast\": [{\"original\":\"原名（空则填'未知'）\",\"new_name\":\"新名\",\"role\":\"角色定位\"}],\n"
"  \"changed_elements\": [\"与原模组相比的关键改动 1\",\"关键改动 2\",\"关键改动 3\"],\n"
"  \"risk_flags\": [\"若原模组里有知名到能一眼认出来的桥段，这里列出来提醒作者再次规避\"]\n"
"}\n"
"只输出 JSON，不要输出任何解释文字。")


@dataclass
class PdfAdaptResult:
    adapted_premise: str
    player_cards: list[dict]
    renamed_cast: list[dict]
    changed_elements: list[str]
    risk_flags: list[str]
    original_excerpt: str       # 原始文本前 1500 字（供作者核对）
    original_chars: int
    attempts: int


def extract_pdf_text(pdf_bytes: bytes, *, max_chars: int = 12000) -> tuple[str, int]:
    """从 PDF 字节中提取纯文本；限制最大字符数避免撑爆上下文。失败抛 ValueError。"""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ValueError(f"pypdf 未安装：{e}")
    import io
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        raise ValueError(f"PDF 解析失败：{e}")
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        t = t.strip()
        if not t:
            continue
        if total + len(t) > max_chars:
            parts.append(t[: max_chars - total])
            total = max_chars
            break
        parts.append(t)
        total += len(t)
    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError("PDF 没有可提取的文本层（可能是扫描件，需要 OCR 后再传）")
    return text, total


def adapt_pdf_source(provider: Provider, ps: PromptStore, pdf_text: str,
                     *, user_hint: str = "") -> PdfAdaptResult:
    """把一段 PDF 跑团剧本做规避版权改编，返回原创副本核雏形。"""
    excerpt = pdf_text[:1500]
    # 若文本过长，切前 9000 字（覆盖开场 + 关键事件 + 结局即可）
    body = pdf_text[:9000]
    user_block = (
        "【原跑团剧本文本】（可能包含 OCR 错字，请忽略排版问题，抓住剧情骨架）\n"
        f"{body}\n\n"
        f"【作者额外要求】\n{user_hint.strip() or '无'}\n\n"
        "请严格按照系统提示输出 JSON。"
    )
    def generate() -> object:
        # 直接使用模块内内置的去痕改编 system prompt；PromptStore 无 render 方法，
        # 且 sushi_pdf_adapt 未注册进 DEFAULT_INSTRUCTIONS，不走 ps 层。
        messages = [
            {"role": "system", "content": PDF_ADAPT_SYSTEM},
            {"role": "user", "content": user_block},
        ]
        return provider.chat(messages).text
    completion = complete_structured(generate, max_regenerations=2)
    data = completion.data
    if not isinstance(data, dict):
        raise ValueError(f"PDF 改编输出连续 {completion.attempts} 次无法解析")
    premise = str(data.get("adapted_premise", "") or "").strip()
    if not premise or len(premise) < 100:
        raise ValueError("PDF 改编未产出足够长度的副本骨架")
    cards = data.get("player_cards") or []
    if not isinstance(cards, list):
        cards = []
    cast = data.get("renamed_cast") or []
    if not isinstance(cast, list):
        cast = []
    changes = data.get("changed_elements") or []
    if not isinstance(changes, list):
        changes = []
    risks = data.get("risk_flags") or []
    if not isinstance(risks, list):
        risks = []
    return PdfAdaptResult(
        adapted_premise=premise,
        player_cards=[c for c in cards if isinstance(c, dict)],
        renamed_cast=cast,
        changed_elements=[str(x) for x in changes],
        risk_flags=[str(x) for x in risks],
        original_excerpt=excerpt,
        original_chars=len(pdf_text),
        attempts=completion.attempts,
    )
