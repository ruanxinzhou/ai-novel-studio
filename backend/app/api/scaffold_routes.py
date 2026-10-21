"""副本详设 6 卡路由：AI 预填 + 列表/订阅 + 选版锁定 + 落锤。"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..jobs import job_store, run_in_background

log = logging.getLogger("api.scaffold")

_SCAFFOLD_SYSTEM = """你是一名克苏鲁风格生存副本的资深模组设计师，正在为一部"七日无限流"小说做副本详设。
前序产物（改编稿、跑团走向、宿主角色、信息差、姿态、大纲）已由作者确认，你需要在此基础上生成**6 张详设卡**，每张卡**3 个差异显著的候选版本**。

严格以 JSON 返回，不要输出 JSON 之外的任何文字，不要写 Markdown 代码块包裹。返回格式：

{
  "death_nodes":  [[ {day,who,how,tease,cost,fate_layer}, ...3~5个死亡事件... ], [...第2版...], [...第3版...]],
  "climax_nodes": [[ {day,title,beat,weight,fate_layer}, ...2~3个高潮节点... ], [...第2版...], [...第3版...]],
  "clue_nodes":   [[ {day,where,what,leads_to,is_red_herring}, ...5条线索... ], [...第2版...], [...第3版...]],
  "core_twist":   [ "第1版核心反转文本，约80-120字", "第2版…", "第3版…" ],
  "tangchi_start":[ "第1版唐迟开篇文本，约80-120字", "第2版…", "第3版…" ],
  "cost_cap":     [ "第1版代价上限文本，约80-120字", "第2版…", "第3版…" ]
}

字段语义：
- death_nodes：死亡事件清单。day=1~7（数字），who=死者身份，how=死法（要带克苏鲁味的惊悚/诡异细节，40-80字），tease=死亡伏笔（正文里事先埋的细微异常，20-40字），cost=该死亡给唐迟/团队带来的代价（理智值/道具/关系/信息损失，20-40字），**fate_layer**（必填，字符串，三选一）：
    * "doomed"=原剧本死劫——唐迟在跑团/讣告里**已经知道**这个人本该死，他带着预警进场但大概率救不下来（这是信息差/先知感的核心来源）
    * "diverted"=唐迟改命成功——唐迟介入救下/转移了死者，但要付代价或产生连锁反应
    * "escalated"=改命反致更惨——唐迟救了反而触发更严重的污染/更多死亡/更难收场
  每版 3-5 个死亡事件中，doomed/diverted/escalated 三类必须**全部出现**（至少各 1 个），其余可自由分配。
- climax_nodes：高潮/转折节点。day=1~7（数字），title=节点标题（5-12字），beat=高潮动作&反转（60-100字），weight=1~3 的整数（1=小反转、2=中高潮、3=终极高潮，每版恰好 1 个 weight=3），**fate_layer**（必填，字符串，三选一）：
    * "doomed"=按原跑团走向唐迟本该被碾死的节点
    * "diverted"=唐迟主动破局的节点
    * "escalated"=破局失败/真相揭露触发反转升级
  每版 climax 必须三类 fate_layer 都覆盖到（对应小反转/中高潮/终极高潮的分布感）。
- clue_nodes：线索/红鲱鱼。day=1~7（数字），where=发现地点，what=线索内容（40-60字），leads_to=指向哪个后续节点/角色/地点（20-30字），is_red_herring=布尔值（true=这是一条误导/假线索；每版至少 1 条红鲱鱼）。
- core_twist：副本底层核心反转，解释整个副本"真正在发生什么"。必须是读者恍然大悟级别的反常识真相，克苏鲁基调。
- tangchi_start：唐迟进入副本的开场第一个场景——他以什么身份醒来/出现、第一感知是什么、眼前第一个异常是什么。要有沉浸感与画面感。
- cost_cap：本副本唐迟需要付出的代价上限（理智、肉体、关系、道德底线的哪一条被触碰/突破），用一句有重量的话写出来，约 100 字。

差异要求：三版必须在节奏、反转类型、死亡密度、线索引导方式上明显不同（如：①慢热解谜型 ②生存紧逼型 ③诡计误导型），让作者有真实选择空间。死亡节点的死者不要 3 版全用同一批人；至少有 2 个新死法差异。
严格遵守 JSON 格式，所有字段必填，字符串中不要出现未转义的引号或换行（可用\\n）。
"""


@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable
    chore_provider: Callable


class _ScaffoldPrefillIn(BaseModel):
    force: bool = False
    directive: str = ""


class _LockCardIn(BaseModel):
    card_key: str
    pick_index: int = -1
    custom_value: list | str | None = None  # 自定义值（存在则跳过 variants 索引，直接写入）


class _FinalizeIn(BaseModel):
    pass


class _DeriveFromEmbodyIn(BaseModel):
    directive: str = ""
    force: bool = False


class _RerollCardIn(BaseModel):
    directive: str = ""


def _session_id(pid: str, vol_no: int) -> str:
    return f"scaff:{pid}:{vol_no}"


def _is_mock(vol) -> bool:
    try:
        from ..book.volume import adapted_premise_of
        premise = adapted_premise_of(vol.adapted_module)
        if isinstance(premise, str) and premise.startswith("测试副本核文本"):
            return True
        hc = vol.host_character or {}
        if isinstance(hc, dict) and "林薇" in (hc.get("name") or ""):
            return True
    except Exception:
        return False
    return False


def _mock_payload() -> dict:
    """6卡×3版 克苏鲁基调硬编码数据。每个 death/climax 节点都带 fate_layer。"""
    return {
        "death_nodes": [
            # 版 A：慢热解谜·尸体逐个说话
            [
                {"day": 2, "who": "宿管阿姨王桂芬", "how": "凌晨三点被发现悬挂在宿舍楼外的晾衣绳上，脖颈缠绕着一团湿滑的黑发，头发末端探出细小的吸盘在月光下一张一合，像某种深海软体动物的触手。唐迟赶到时她已经断气——他在跑团里预见过这个死法，提前十分钟冲下楼却还是晚了一步，吸盘已经钻进颈动脉。", "tease": "前一晚她曾念叨'水龙头里流出咸水'。", "cost": "失去唯一能查民国旧档的内线；唐迟第一次明确意识到'知道剧本也救不下来'。", "fate_layer": "doomed"},
                {"day": 3, "who": "历史系研究生周凯", "how": "在校史陈列室里，唐迟冲进去时旧报纸已经开始吞噬周凯——油墨像黑色焦油从七窍往里灌。周凯用最后一口气把拼到一半的'1927海难'那页报道塞进唐迟手里，随即整个人被吸成一张扁平的报纸人，嘴还张成'O'型。", "tease": "他之前在微信群里发过'报纸上的人在看我'。", "cost": "拼出的'1927海难'线索页被救下，但周凯人没了；唐迟左手被油墨灼伤留下黑色印记。", "fate_layer": "diverted"},
                {"day": 5, "who": "林薇（小报记者）", "how": "第七教学楼天台，唐迟冲过去拉她时影子抢先一步缠上她的脚踝——林薇没坠楼，但被影子触碰过的右眼开始渗出海水，当晚起她开始替影子'说话'，说出的都是唐迟自己没说出口的秘密。", "tease": "唐迟在前一夜看见她在镜子前练习那个微笑。", "cost": "林薇未死但被污染成影子传声筒，唐迟身边多了一个随时会泄露底牌的'内鬼'，San -2；相机落入校工之手。", "fate_layer": "escalated"},
            ],
            # 版 B：生存紧逼·每天都死人
            [
                {"day": 1, "who": "宿管阿姨王桂芬", "how": "唐迟刚进宿舍的当晚，她在查房时被楼道里渗出的海水灌进肺里淹死——楼道里没一滴水，但她口鼻中源源不断涌出带着海腥气的咸水，整个人像在空气里溺亡。唐迟拍门时海水已经漫过她的下巴，他眼睁睁看着她瞳孔放大。", "tease": "她开门时嘀咕了一句'今天走廊怎么这么潮'。", "cost": "开局即建立'空气溺亡'惊悚规则；唐迟带着跑团预警仍没救下第一人，San -1。", "fate_layer": "doomed"},
                {"day": 2, "who": "保安老刘", "how": "在巡逻至游泳馆废墟时被湿漉漉的校服拖拽，唐迟提前在泳池边撒了盐线想拉他回来，但盐水只烧断了校服的一半——老刘被拖进干涸池底，第二天打捞上来全身布满藤壶，右手却死死攥着一枚刻着陌生女生名字的校徽。", "tease": "他胸口别着的校徽背面刻着一个陌生女生的名字。", "cost": "老刘人没救回，但校徽物证攥在手里——盐线规则由此建立；失去夜间唯一武力支援。", "fate_layer": "diverted"},
                {"day": 3, "who": "历史系研究生周凯", "how": "在校史室唐迟堵门不让他碰报纸堆，但那张1927年毕业照已经让他七窍流血——油墨从眼眶耳朵往外渗。他靠着门完整说出'你不是第一次来'七个字后咽气，手里毕业照最角落多出了一张和唐迟一模一样的脸。", "tease": "他死前给唐迟发了半条微信'你不是第一次——'。", "cost": "周凯没被报纸吞掉但还是死了，轮回线索由他亲口说出而非微信残句；唐迟被卷入更深层疑云，San -1。", "fate_layer": "diverted"},
                {"day": 4, "who": "中文系学姐苏雅", "how": "在图书馆翻到无字书开始用血写墙，唐迟赶到时她写到第七十三字——他一把抽走无字书，血字戛然而止停在留白处，苏雅没被蒸干。但当晚那缺了四个字的血字被合唱队用来'补完'召唤，宿舍楼多死了两个无名学生。", "tease": "前一天她反复哼着一段不成调的海妖童谣。", "cost": "苏雅没死但血字留白成了召唤缺口，合唱提前启动，两个无辜学生陪葬；童谣线索被迫中断。", "fate_layer": "escalated"},
                {"day": 5, "who": "林薇（小报记者）", "how": "在天台唐迟抓住她手腕硬拽回来，但影子顺着她手臂爬上来、钻进唐迟脚下的阴影里——林薇被救下没坠楼，可唐迟低头看自己的影子，它正在缓慢地、独立地转身。", "tease": "她相机里最后一张照片拍的是唐迟的脚下，那里本该没有影子。", "cost": "林薇活着回来，但唐迟的影子被寄生，从此他无法完全相信自己脚下的黑暗；San -2。", "fate_layer": "escalated"},
            ],
            # 版 C：诡计误导·身份错配
            [
                {"day": 3, "who": "宿管王桂芬（伪）", "how": "唐迟已经识破'王桂芬'是陈阿妹借壳的皮囊但没有当场揭穿——他想顺着线索放长线。结果那具皮囊在锅炉房里主动融化灭口，融成咸水前留下两条指向辅导员和周凯的假线索，故意把唐迟往错路上引。", "tease": "唐迟此前注意到她右手的烫伤疤换了位置。", "cost": "信任崩塌——不仅NPC可能被替换，连识破后不揭穿都会被反将一军，两条假线索将浪费唐迟接下来一整天。", "fate_layer": "doomed"},
                {"day": 4, "who": "历史系研究生周凯", "how": "在意识到'整个校园都在演一出1927年的戏'后被倒影拖向校史馆落地镜，唐迟扑上去死死拉住他的手拽回了上半身——但下半身永远留在了镜中。周凯用仅剩的上半身说完'我们都不是第一次来'后化为纸灰，镜中他下半身朝唐迟鞠了一躬。", "tease": "他最后一句话是'我们都不是第一次来'。", "cost": "周凯半个人被救回来还是死了，但'我们都不是第一次来'这句关键证词亲口落下；镜子成了新的污染点。", "fate_layer": "diverted"},
                {"day": 5, "who": "林薇（小报记者）", "how": "在天台试图拍下'真正的幕后'时镜头里伸出一只布满鳞片的手勒向她脖子，唐迟扑过去按住相机快门猛拽——那只鳞片手被硬生生拽断，林薇没被拖进相机，但断手落地后变成一张沾着陈阿妹血迹的1927年学生证，照片上是唐迟自己的脸。林薇当场崩溃。", "tease": "她相机前一晚拍到的唐迟，眼睛里有另一双眼睛。", "cost": "林薇被救下但精神崩溃无法再提供线索；沾血学生证让唐迟被标记为'容器候选人'，San -3。", "fate_layer": "escalated"},
                {"day": 6, "who": "中文系学姐苏雅", "how": "发现校歌倒放是召唤咒后冲上钟楼想毁铜钟，唐迟紧随其后抓住她左手腕拼命往下拽，但铜钟里涌出的海藻力道太大没拽动——苏雅被拖进铜钟前从钟缝里塞出一张纸条'别信合唱队的指挥'，随即铜钟自鸣七下归于沉寂。唐迟左手被海藻灼伤留下藤壶疤。", "tease": "她给唐迟留了一张纸条'别信合唱队的指挥'。", "cost": "苏雅被铜钟吞入未能救回，但关键纸条'别信指挥'到手；唐迟左手藤壶疤将持续作痛作为污染标记。", "fate_layer": "diverted"},
            ],
        ],
        "climax_nodes": [
            # 版 A
            [
                {"day": 3, "title": "报纸睁眼", "beat": "周凯死后唐迟检查校史馆旧报纸，发现1927年学生照上的人在对他眨眼——这些纸不是记录历史而是在窥视当下。他按跑团攻略点火烧报纸，火里第一次传出女声惨叫，纸灰拼成名字'陈阿妹'。唐迟以为自己抢先破局，却没发现纸灰里少了一个字。", "weight": 1, "fate_layer": "diverted"},
                {"day": 5, "title": "影子先行", "beat": "林薇在天台被影子污染右眼后，唐迟在水渍里看见自己影子也在缓慢转身。他按盐圈困住自己影子争取半天，以为掌握主动——可回头看见林薇的影子已经脱离她的脚，在楼道尽头和辅导员的影子牵上了手。改命没救下林薇，反而让污染提前对接。", "weight": 2, "fate_layer": "escalated"},
                {"day": 7, "title": "海床点名", "beat": "午夜游泳馆废墟被海水灌满——和唐迟跑团阶段预览过的终局一模一样。他在水下看见1927年溺亡的47名学生坐在池底举手'点名'，讲台后站着七天里一直温柔照顾他的辅导员，她露出了鳃。他以为提前知道剧本就能改，但第七个名字还是从他自己嘴里念了出来。", "weight": 3, "fate_layer": "doomed"},
            ],
            # 版 B
            [
                {"day": 2, "title": "空气溺亡", "beat": "刘保安死时楼道完全干燥却涌出咸水，唐迟用布蘸水尝了一口——是真海水，'海被折进校园'的规则由此建立。老刘虽然没救回来，但盐线能灼伤校服/溺亡灵体的规则在这一次失败中被试了出来，为后续改命留了刀。", "weight": 1, "fate_layer": "diverted"},
                {"day": 4, "title": "血字童谣", "beat": "唐迟抽走苏雅的无字书中断了血字蒸干，以为救下苏雅破了童谣点名——可第七十七个字没写完、留了四字空白。当晚合唱队方向用两个无名学生的命补完了那四个字，第一声齐唱从大礼堂方向飘出，比原剧本提前了整整两天。", "weight": 2, "fate_layer": "escalated"},
                {"day": 7, "title": "合唱终章", "beat": "暴雨夜全校广播响起校歌倒放，幸存NPC眼神空洞向大礼堂汇集——和唐迟跑团预言的全场合唱终局完全一致。他冲进礼堂看见指挥台上是47张湿脸拼成的聚合体，它抬起手准备收最后一个音节。他以为能靠前两天的改命错开这一幕，但合唱还是准时开始。", "weight": 3, "fate_layer": "doomed"},
            ],
            # 版 C
            [
                {"day": 3, "title": "皮囊融化", "beat": "'王桂芬'皮囊融成咸水后，唐迟强行撕下另一个宿管手腕上的皮确认替换规则——人皮像蜡剥落露出底下发绿鳞片，他终于坐实'NPC大部分已被替换'。但撕皮的瞬间锅炉房里其他几个'宿管'同时转过头看他，绿鳞片在袖口下隐隐反光。", "weight": 1, "fate_layer": "diverted"},
                {"day": 5, "title": "镜中周凯", "beat": "唐迟对着校史馆镜子喊周凯，镜面浮出那张被压平的脸说'你就是陈阿妹等的人'，他看见自己穿着1927年学生装——他以为拉出周凯上半身是改命成功，可镜面里他自己的倒影比他本人多笑了一下，镜子本身也已经被污染。", "weight": 2, "fate_layer": "diverted"},
                {"day": 7, "title": "第七次迭代", "beat": "唐迟在钟楼顶层发现铜钟内壁'自己的名字'刻了六次被划掉——和跑团预览一致：他来过这个副本六次、死了六次。林薇坠楼时的微笑是她保留前次记忆的证明，而唐迟自己也开始记起不属于这七天的事。这是原剧本的必到节点，他必须做出和前六次不同的选择。", "weight": 3, "fate_layer": "doomed"},
            ],
        ],
        "clue_nodes": [
            # 版 A
            [
                {"day": 1, "where": "宿舍枕头下", "what": "一张边角泛黄的1927年毕业照，最前排中间女生的脸被墨水涂掉，背面用铅笔写着'别让她数到七'。", "leads_to": "指向校史馆查阅1927年失踪名单", "is_red_herring": False},
                {"day": 2, "where": "王桂芬值班室抽屉", "what": "一本登记薄上，近三个月每晚十点都有同一个女生签字回宿，但该宿舍早已空置三十年。", "leads_to": "指向那间废弃的 304 宿舍", "is_red_herring": False},
                {"day": 3, "where": "校史陈列室碎报纸堆", "what": "周凯死后满地碎报纸拼出一篇1927年'校庆海难'报道，47名师生乘船出游全部失踪，报道最后一句是'指挥者安全返回'。", "leads_to": "指向当日的带队老师/辅导员身份", "is_red_herring": False},
                {"day": 4, "where": "图书馆B区旧书堆", "what": "一本被翻烂的民国日记本，里面反复写'它通过影子找替身，盐能困住影子'，但日记主人落款处被撕掉了。", "leads_to": "盐的使用方法（为高潮2服务）", "is_red_herring": False},
                {"day": 4, "where": "食堂门口公告栏", "what": "一张当天新贴的社团招新海报，'深海合唱队'招新人，落款联系人写着唐迟的名字和手机号——但他从未贴过。", "leads_to": "看似指向合唱队，其实是干扰项", "is_red_herring": True},
            ],
            # 版 B
            [
                {"day": 1, "where": "宿舍水龙头", "what": "清晨流出的水带着明显咸味，凑近听能隐约听见潮汐声和极远的女声合唱，持续约30秒后恢复正常。", "leads_to": "指向'海被折进校园'的规则认知", "is_red_herring": False},
                {"day": 2, "where": "刘保安巡逻亭", "what": "他的执勤本上最后一行字是'游泳馆方向灯亮了，我去看看'，但游泳馆已废弃十年、根本没有通电。", "leads_to": "指向游泳馆废墟", "is_red_herring": False},
                {"day": 3, "where": "微信聊天记录", "what": "周凯死前发出的半条消息'你不是第一次——'，撤回记录里显示原本还有'来'字。", "leads_to": "指向轮回/迭代真相的伏笔（版C核心）", "is_red_herring": True},
                {"day": 4, "where": "苏雅的随身听", "what": "里面存着一段校歌，她在A面结尾偷偷录了倒放版本，倒放第37秒能听见一个女声在数'一、二、三…'。", "leads_to": "指向校歌倒放是召唤仪式", "is_red_herring": False},
                {"day": 5, "where": "林薇相机SD卡", "what": "最后一张照片在坠楼前一秒拍下，画面里是天台边缘空无一人，但唐迟的脚下——没有影子。", "leads_to": "指向唐迟自身异常（为终局留钩子）", "is_red_herring": False},
            ],
            # 版 C
            [
                {"day": 1, "where": "辅导员办公室", "what": "她办公桌玻璃板下压着一张她的全家福，照片里的她穿着1927年款旗袍站在一群梳辫子的女生中间，而照片是上周拍的。", "leads_to": "看似指向辅导员是幕后，实际是误导", "is_red_herring": True},
                {"day": 2, "where": "宿管值班室", "what": "王桂芬（伪）桌上放着一盒未拆封的烫伤膏，有效期写着民国十九年（1930年）。", "leads_to": "指向 NPC 被替换的事实", "is_red_herring": False},
                {"day": 4, "where": "校史馆落地镜背面", "what": "镜子背面用口红写满了名字，同一个名字出现六次都被划掉，第七次只写了一半——是唐迟的姓。", "leads_to": "指向唐迟是第七次迭代", "is_red_herring": False},
                {"day": 5, "where": "林薇相机最后一张", "what": "照片里唐迟回头看向镜头，瞳孔里映出另一个人的脸——一张1927年女学生的脸。", "leads_to": "指向'唐迟是陈阿妹容器'的可能", "is_red_herring": False},
                {"day": 6, "where": "钟楼铜钟内壁", "what": "铜钟内壁除了六次唐迟的名字，还有一行极小的字：'第七次别数到七'，字迹是唐迟自己的笔迹。", "leads_to": "直接指向终局破局关键", "is_red_herring": False},
            ],
        ],
        "core_twist": [
            "这所大学建在1927年海难点上方的'折痕'上，47名溺亡学生的怨念被辅导员（当年唯一幸存的带队老师，已用仪式延续生命近百年）每年以'校庆合唱'的方式献祭活人维系。所谓副本，是47个亡魂每年一次的'点名'，被点到第七次的人会顶替当年的陈阿妹沉入海床成为新的'锚'，而唐迟是这一届被选中的'第七人'。",
            "整所大学在副本开启时已被彻底拉入深海'投影'，陆地其实是海床的幻觉，所有NPC里人类已不足三成，剩下的都是1927年溺亡亡魂借的'皮囊'。副本的真正出口不是'活到第七天'而是'在合唱终章拒绝开口唱最后一个音节'——唱了就永远留在合唱队成为新声部，不唱则折痕关闭、所有亡魂超度，但唐迟自己再也回不到岸上。",
            "唐迟不是第一次进入这个副本。铜钟内壁刻着他的名字六次、每一次都死在第七天——这是他的第七次迭代。林薇之所以微笑是因为她保留着第六次的记忆，而辅导员并不是最终反派，真正在循环的是唐迟自己：他是1927年那个因懦弱没有救下沉船同学的'陈阿妹的未婚夫'，因负罪感被古神判罚永世轮回救她；唯一破局方式是这一次主动选择'不救她'。",
        ],
        "tangchi_start": [
            "唐迟在一阵潮湿的咸腥味里醒来，发现自己躺在某大学四人间宿舍的下铺，床头贴着陌生的军训合影，窗外是灰压压的天。他坐起身时枕头边的手机亮了——屏幕上没有任何App，只有一行血红的倒计时：'距点名结束还有167:59:42'。走廊尽头传来宿管阿姨压低声音的念叨：'又来一个……这是今年第七个了。'",
            "唐迟是被一阵呛人的海水味淹醒的——他明明躺在宿舍床上，鼻腔和喉咙里却全是咸水，咳了好一阵才喘上气。窗外传来女生合唱的声音，不成调、湿漉漉的，像是从水底下传上来。他低头看自己的手，指甲缝里嵌着深绿色海藻。门被轻轻敲响三下，外面是林薇的声音：'同学，校报采访，请问你昨晚听到潮声了吗？'",
            "唐迟是在一堂大学语文课上惊醒的，讲台上老师正在讲1927年新诗，但所有同学都背对着他坐——包括他自己。他低下头，看见桌板倒影里的自己穿着1927年的灰布学生装，而讲台上的'老师'突然停下，直直看向他，微笑着说：'这位同学，你迟到了六次了，这一次请一定要数到七。'他摸口袋，里面是一张属于陈阿妹的1927年学生证，照片上的脸和他一模一样。",
        ],
        "cost_cap": [
            "本副本唐迟付出的代价上限触达'道德底线'：为了打破'第七人顶替'的循环，他必须在最后时刻亲手把已经被亡魂替换了皮囊的辅导员按入仪式阵眼，而在那一瞬间她会短暂恢复人性、用他熟悉的温柔目光看着他求他放手；这一推，他要背负的是'杀死一个还残留着人性的人'的罪感，出本后长期处于轻度情感解离状态。",
            "本副本唐迟付出的代价上限触达'存在'本身：拒绝合唱意味着他主动选择留在折痕里送亡魂走，他将独自在已经是海床的废墟里下沉七天七夜，理智值在漫长的深海幻觉里几乎清零；上岸后他的影子永远无法完全贴合身体，在月光下会比本人慢半拍，那是古神在他身上留下的'已收过路费'的戳记。",
            "本副本唐迟付出的代价上限触达'情感与记忆'：第七次破局要求他主动放弃救林薇和苏雅、接受'不救陈阿妹'这个选择——这意味着他不仅要看着两个他在六次轮回里反复失去又反复相遇的女人再次死在面前，还要亲手撕毁那个支撑他轮回了六次的'我一定要救她'的执念；出本后他能记起她们的脸，却再也记不起她们的名字。",
        ],
    }


def _build_context(vol, directive: str = "") -> str:
    """复用：从 vol 上提取魂穿阶段所有产物拼成 LLM 上下文。"""
    from ..book.volume import adapted_premise_of, adapted_cast_of
    parts: list[str] = []
    premise = adapted_premise_of(vol.adapted_module)
    cast_cards = adapted_cast_of(vol.adapted_module)
    parts.append("===== 副本骨架（改编稿·副本核文本）=====")
    parts.append(premise or "")
    if cast_cards:
        parts.append("\n===== 剧本 NPC/KPC 卡司 =====")
        for c in cast_cards:
            parts.append(f"- {c.get('name','?')}（{c.get('role','')}；{c.get('occupation','')}）｜秘密：{c.get('secret','')}")
    sr = vol.simulated_run or {}
    if isinstance(sr, dict):
        parts.append("\n===== 选定跑团走向 =====")
        parts.append(json.dumps(sr, ensure_ascii=False)[:1500])
    hc = vol.host_character or {}
    if hc:
        parts.append("\n===== 宿主角色 =====")
        parts.append(json.dumps(hc, ensure_ascii=False))
    parts.append("\n===== 切入视角 =====")
    parts.append(f"切入点：{vol.entry_point or ''}")
    parts.append(f"信息差：{vol.info_gap or ''}")
    parts.append(f"宿主姿态：{vol.stance or ''}")
    tso = vol.tangchi_story_outline
    if isinstance(tso, dict):
        parts.append("\n===== 唐迟七日历 =====")
        parts.append(tso.get("outline_text") or json.dumps(tso, ensure_ascii=False)[:2000])
    elif isinstance(tso, str) and tso:
        parts.append(f"\n===== 唐迟七日历 =====\n{tso}")
    d = (directive or "").strip()
    if d:
        parts.append(f"\n===== 作者额外指令 =====\n{d}")
    return "\n".join(parts)


def _normalize_list_card(raw, fallback_intent: str = "") -> list[dict]:
    """把 list 类卡的版本值归一化成 [{rhythm_intent, nodes}]。

    模型可能返回两种形状：
    · 新格式 [{"rhythm_intent": "...", "nodes": [...]}, ...]
    · 旧格式 [[{...节点}], [{...节点}]]（改prompt 前的历史数据/mock）
    统一成新格式，并保留 nodes 里的原始节点。
    节奏意图缺失时给一句兜底说明，不让下游拿不到意图。
    """
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for i, ver in enumerate(raw):
        if isinstance(ver, dict) and "nodes" in ver:
            nodes = ver.get("nodes")
            out.append({
                "rhythm_intent": str(ver.get("rhythm_intent") or fallback_intent or "").strip(),
                "nodes": list(nodes) if isinstance(nodes, list) else [],
            })
        elif isinstance(ver, list):
            out.append({"rhythm_intent": fallback_intent, "nodes": list(ver)})
        elif ver is None:
            out.append({"rhythm_intent": fallback_intent, "nodes": []})
    return out


def _nodes_of(version) -> list:
    """取某一版的节点数组；兼容 dict(新) 与 list(旧) 两种形状。"""
    if isinstance(version, dict):
        n = version.get("nodes")
        return list(n) if isinstance(n, list) else []
    if isinstance(version, list):
        return version
    return []


def _intent_of(version) -> str:
    if isinstance(version, dict):
        return str(version.get("rhythm_intent") or "").strip()
    return ""


# 单卡重抽/derive：只出 2 版差异化变体（不再一次 18 卡），响应体是 {<card_key>: [v1, v2]}
#
# 【节奏意图原则】三版候选必须各自声明一个**节奏意图**，节点数量与分布由该意图推导，
# 不再套用统一的硬数字（不再要求「必3~5 个死亡」「必2~3 个高潮」「恰好 1 个 weight=3」）。
# 意图由模型自由发明，无固定模式列表——这是为了让每卷的节奏形状真正不同，
# 而不是三版只换「谁死、怎么死」。
#
# 唯一保留的硬底线：**因果张力不能全丢**。doomed/diverted/escalated 三类
# 是这本书的差异化内核（先知感 / 改命 / 反噬），但它是**下限参考**而非配额——
# 允许某版只侧重其中一类（比如「零死」版可以没有 escalated），不允许三类全空。
_RHYTHM_INTENT_BLOCK = """
【节奏意图（每版必写，且三版必须彼此不同）】
每版开头先给出一句节奏意图，说明这一版的**节奏形状**，节点数量与分布由它推导。
意图由你根据副本类型自由发明，不要套固定模板，但必须具体到「读者感受到什么」，
不能写「节奏紧张」这种空话。参考方向（不必照抄，可自由创造）：
  · 零死·代价递进：无人死亡，压力全靠环境/规则/关系侵蚀
  · 慢热·单点爆破：前半程零死亡，后段集中收割
  · 高密度·连环倒：每 1-2 天一个节点，中段有一次结构反转
  · 全程倒叙：从结果倒推，读者先知道结局再看过程
  · 他人视角推进：主角缺席或昏迷，由 NPC 视角承担叙事
三版意图不同是硬要求——三版节奏骨架一样就等于没给作者选择。
"""

_SCAFFOLD_SINGLE_CARD_SYSTEMS: dict[str, str] = {
    "death_nodes": """你是克苏鲁风格生存副本的资深模组设计师。请基于给定的副本上下文，只生成【死亡节点 death_nodes】的 2 个差异显著的候选版本（不要生成其他卡）。
每版结构：{"rhythm_intent": "本版节奏意图（一句话，具体到读者感受）", "nodes": [死亡事件数组]}
nodes 里每个元素 {day,who,how,tease,cost,fate_layer}，字段语义与 6 卡预填保持一致：
doomed=原剧本死劫（唐迟已预见但救不下来）/diverted=唐迟改命成功但付代价/escalated=改命反致更惨。

【数量不设上限】节点几个由你的节奏意图决定：可以是 0（零死卷）、1（单点爆破）、可以是 8（连环倒）。
不要为了凑数硬塞，也不要为了显得克制而删到只剩 1 个。
fate_layer 是**下限参考不是配额**：三类都不允许全空，但允许某一版只侧重其中一两类。
""" + _RHYTHM_INTENT_BLOCK + """
严格以 JSON 返回，不输出任何其他文字，不写 Markdown 包裹。返回格式：
{"death_nodes": [ {"rhythm_intent": "...", "nodes": [...]}, {"rhythm_intent": "...", "nodes": [...]} ]}""",
    "climax_nodes": """你是克苏鲁风格生存副本的资深模组设计师。请基于给定的副本上下文，只生成【高潮/转折 climax_nodes】的 2 个差异显著的候选版本。
每版结构：{"rhythm_intent": "本版节奏意图（一句话）", "nodes": [节点数组]}
nodes 里每个元素 {day,title,beat,weight,fate_layer}，weight=1~3 整数表示轻重。

【数量与权重不设上限】节点几个、weight 怎么分布，由你的节奏意图决定。
不再要求「恰好 1 个 weight=3」——如果这一版的重头戏在别处（比如第1天就炸），
那 weight=3 就该落在第 1 天；也可能整卷只有 1 个高潮，甚至没有 weight=3（用 2 和 1 层层递进）。
fate_layer 是下限参考不是配额，三类都不允许全空。
""" + _RHYTHM_INTENT_BLOCK + """
严格以 JSON 返回：{"climax_nodes": [ {"rhythm_intent": "...", "nodes": [...]}, {"rhythm_intent": "...", "nodes": [...]} ]}""",
    "clue_nodes": """你是克苏鲁风格生存副本的资深模组设计师。请基于给定的副本上下文，只生成【线索与红鲱鱼 clue_nodes】的 2 个差异显著的候选版本。
每版结构：{"rhythm_intent": "本版节奏意图（一句话，说明这一版线索怎么铺）", "nodes": [线索数组]}
nodes 里每个元素 {day,where,what,leads_to,is_red_herring}。

【数量不设上限】线索几条由节奏意图决定，可以是 2 条精线索，也可以是 12 条铺满。
红鲱鱼同理——不是每卷都必须有红鲱鱼；如果这一版的张力来自「线索本身都指向真结论」，
那就 is_red_herring 全填 false 也是合法的。
""" + _RHYTHM_INTENT_BLOCK + """
严格以 JSON 返回：{"clue_nodes": [ {"rhythm_intent": "...", "nodes": [...]}, {"rhythm_intent": "...", "nodes": [...]} ]}""",
    "core_twist": """你是克苏鲁风格生存副本的资深模组设计师。请基于给定的副本上下文，只生成【核心诡计 core_twist】的 2 个差异显著候选（每条 80-120 字）。
严格以 JSON 返回：{"core_twist": ["第1版文本", "第2版文本"]}""",
    "tangchi_start": """你是克苏鲁风格生存副本的资深模组设计师。请基于给定的副本上下文，只生成【唐迟第一秒 tangchi_start】的 2 个差异显著候选（每条 80-120 字，要有画面感与第一个异常）。
严格以 JSON 返回：{"tangchi_start": ["第1版文本", "第2版文本"]}""",
    "cost_cap": """你是克苏鲁风格生存副本的资深模组设计师。请基于给定的副本上下文，只生成【代价上限 cost_cap】的 2 个差异显著候选（每条 80-120 字）。

必填，但**不要套固定分类**。不要默认写成「道德底线/存在本身/情感记忆」那三种——
先判断这一副本的类型是什么，再决定该付出什么代价。代价可以是：
理智（清醒着却不记得自己是谁）、肉体（某处再也长不好）、关系（某个人的脸记不住了）、
道德底线（做了自己不接受的事）、资源（时间/寿命/记忆被扣走）、规则（身上被留下不可逆的印记）……
也可以是这一副本独有的、前面几类都不贴的代价。用一句话写清「他被碰到了什么底线」。

严格以 JSON 返回：{"cost_cap": ["第1版文本", "第2版文本"]}""",
}


_LIST_KEYS = {"death_nodes", "climax_nodes", "clue_nodes"}
_TEXT_KEYS = {"core_twist", "tangchi_start", "cost_cap"}
_ALL_KEYS = ["death_nodes", "climax_nodes", "clue_nodes", "core_twist", "tangchi_start", "cost_cap"]


def _call_llm_cards(deps, system_prompt: str, context: str, user_hint: str, fallback: dict) -> dict:
    """调用 LLM 做结构化输出，失败回退 fallback。"""
    from ..generation import complete_structured
    try:
        def _call():
            return deps.chore_provider("scaffold").chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context + "\n\n" + user_hint},
            ]).text
        comp = complete_structured(_call, max_regenerations=2)
        data = comp.data if isinstance(comp.data, dict) else {}
        # 简单做非空校验
        if not any(data.get(k) for k in _ALL_KEYS):
            return fallback
        return data
    except Exception as _e:
        log.warning("scaffold LLM 失败，回退 fallback：%s", _e)
        return fallback


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def register_scaffold_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable,
    chore_provider: Callable,
) -> None:
    deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        chore_provider=chore_provider,
    )
    router = APIRouter(tags=["scaffold"])
    prefix = "/api/projects/{pid}/workbench/volumes/{vol_no}/scaffold"

    # ----- POST /scaffold/prefill -----
    def _scaffold_prefill(
        pid: str,
        vol_no: int,
        body: _ScaffoldPrefillIn = Body(...),
    ):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        # 校验七日历
        if not vol.tangchi_story_outline:
            raise HTTPException(422, "请先完成魂穿（唐迟七日历为空）")

        sid = _session_id(pid, vol_no)
        # 若已有进行中的同 kind 任务：force 时取消，否则报冲突
        for existing in job_store.list_session(sid):
            if existing.kind == "scaffold_prefill" and existing.status in ("pending", "running"):
                if not body.force:
                    raise HTTPException(409, {"code": "JOB_ACTIVE", "job": existing.as_dict()})
                job_store.cancel(existing.job_id)

        job = job_store.create(sid, "scaffold_prefill", meta={"phase": "scaffold", "vol_no": vol_no})

        is_mock = _is_mock(vol)

        # 把 vol/save 句柄绑定到 worker
        def _work(j, update):
            update(message="正在分析副本骨架与宿主设定…", progress=5)

            if is_mock:
                for p, msg in [
                    (20, "梳理死亡节点节奏…"),
                    (40, "推演高潮三幕结构…"),
                    (60, "布置线索与红鲱鱼…"),
                    (80, "打磨核心反转与代价上限…"),
                    (95, "整理 6×3 版详设卡…"),
                ]:
                    time.sleep(0.4)
                    update(message=msg, progress=p)
                payload = _mock_payload()
            else:
                from ..generation import complete_structured
                update(message="正在组装上下文…", progress=10)
                context = _build_context(vol, body.directive or "")
                update(message="LLM 生成 6×3 版详设卡…", progress=30)

                def _call():
                    return deps.chore_provider("scaffold").chat([
                        {"role": "system", "content": _SCAFFOLD_SYSTEM},
                        {"role": "user", "content": context + "\n\n请严格按 system 给定 JSON schema 返回 6 卡×3 版详设，不要输出任何其他文字。"},
                    ]).text

                try:
                    comp = complete_structured(_call, max_regenerations=2)
                    data = comp.data if isinstance(comp.data, dict) else {}
                except Exception as _e:
                    log.warning("scaffold LLM 失败，回退 mock：%s", _e)
                    data = _mock_payload()
                payload = {
                    "death_nodes": _normalize_list_card(data.get("death_nodes")),
                    "climax_nodes": _normalize_list_card(data.get("climax_nodes")),
                    "clue_nodes": _normalize_list_card(data.get("clue_nodes")),
                    "core_twist": data.get("core_twist") or [],
                    "tangchi_start": data.get("tangchi_start") or [],
                    "cost_cap": data.get("cost_cap") or [],
                }
                update(message="解析完成，写入缓存…", progress=90)

            # 写缓存字段（不标记已选定/锁定）
            cards = vol.scaffold_cards or {}
            for key in ["core_twist", "tangchi_start", "cost_cap"]:
                cards[key] = {"variants": payload.get(key) or [], "picked_idx": None, "locked_at": None, "text": None}
            for key in ["death_nodes", "climax_nodes", "clue_nodes"]:
                vers = payload.get(key) or []
                cards[key] = {
                    "picked_idx": None, "locked_at": None, "variants": vers,
                    # 每版的节奏意图单独存一份，前端展示「这一版是什么节奏」
                    "rhythm_intents": [_intent_of(v) for v in vers],
                }
            # 顶层字段存第一版的节点数组（历史兼容：下游按 vol.death_nodes 消费数组）
            vols.update_planning(
                vol_no,
                death_nodes=_nodes_of((payload.get("death_nodes") or [None])[0]),
                climax_nodes=_nodes_of((payload.get("climax_nodes") or [None])[0]),
                clue_nodes=_nodes_of((payload.get("clue_nodes") or [None])[0]),
                pasted_nodes=[],
                scaffold_cards=cards,
            )
            update(message="副本详设预填完成", progress=100)
            return {
                "death_nodes": payload.get("death_nodes"),
                "climax_nodes": payload.get("climax_nodes"),
                "clue_nodes": payload.get("clue_nodes"),
                "core_twist": payload.get("core_twist"),
                "tangchi_start": payload.get("tangchi_start"),
                "cost_cap": payload.get("cost_cap"),
                "is_mock": is_mock,
            }

        run_in_background(job_store, job.job_id, _work)
        return {"ok": True, "job_id": job.job_id, "job": job.as_dict()}

    # ----- GET /scaffold/jobs -----
    def _scaffold_jobs(pid: str, vol_no: int):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        if vols.get(vol_no) is None:
            raise HTTPException(404, "卷不存在")
        sid = _session_id(pid, vol_no)
        jobs = job_store.list_session(sid)
        return {"jobs": [j.as_dict() for j in jobs if j.kind == "scaffold_prefill"]}

    # ----- GET /scaffold/stream (SSE) -----
    def _scaffold_stream(pid: str, vol_no: int):
        """SSE：持续推送 scaffold session 下的 prefill job，3 轮 idle 后断开。"""
        from ..jobs.store import job_store as _js, jobs_to_sse_event_list
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        if vols.get(vol_no) is None:
            raise HTTPException(404, "卷不存在")
        sid = _session_id(pid, vol_no)

        def gen():
            idle_rounds = 0
            # 先推一次全量，供前端初始化
            jobs = [j for j in _js.list_session(sid) if j.kind == "scaffold_prefill"]
            yield jobs_to_sse_event_list(jobs)
            while True:
                jobs = [j for j in _js.list_session(sid) if j.kind == "scaffold_prefill"]
                any_active = any(j.status in ("pending", "running") for j in jobs)
                if not any_active:
                    idle_rounds += 1
                    # 结束前再推一次最新状态（含 result）
                    yield jobs_to_sse_event_list(jobs)
                    if idle_rounds >= 3:
                        yield f"event: job_idle\ndata: {json.dumps({'ok': True}, ensure_ascii=False)}\n\n"
                        return
                else:
                    idle_rounds = 0
                    yield jobs_to_sse_event_list(jobs)
                time.sleep(0.6)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ----- POST /scaffold/lock-card -----
    def _scaffold_lock_card(
        pid: str,
        vol_no: int,
        body: _LockCardIn = Body(...),
    ):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        card_key = body.card_key
        idx = int(body.pick_index)
        list_keys = {"death_nodes", "climax_nodes", "clue_nodes"}
        text_keys = {"core_twist", "tangchi_start", "cost_cap"}
        if card_key not in list_keys | text_keys:
            raise HTTPException(422, f"未知卡片：{card_key}")

        cards = vol.scaffold_cards or {}
        cards.setdefault(card_key, {"variants": [], "picked_idx": None, "locked_at": None})
        entry = cards[card_key]

        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        if card_key in list_keys:
            if body.custom_value is not None and isinstance(body.custom_value, list):
                # 自编可能是裸节点数组，也可能是 {rhythm_intent, nodes}
                if (body.custom_value and isinstance(body.custom_value[0], dict)
                        and "nodes" in body.custom_value[0]):
                    custom = _normalize_list_card(body.custom_value)
                    selected = _nodes_of(custom[0]) if custom else []
                    custom_intent = _intent_of(custom[0]) if custom else ""
                else:
                    selected = list(body.custom_value)
                    custom_intent = ""
                entry["rhythm_intent"] = custom_intent
            else:
                all_variants = entry.get("variants") or getattr(vol, card_key, None) or []
                idx = int(body.pick_index)
                if idx < 0 or idx >= len(all_variants):
                    raise HTTPException(422, f"版本序号越界：{card_key}[{idx}]")
                ver = all_variants[idx]
                # 落库存纯节点数组（下游 rhythm/settle/foreshadow 都按数组消费），
                # 节奏意图单独存进 scaffold_cards 供前端展示与作者回看。
                selected = _nodes_of(ver)
                entry["rhythm_intent"] = _intent_of(ver)
            entry["picked_idx"] = body.pick_index if body.custom_value is None else -1
            entry["locked_at"] = now
            if body.custom_value is None:
                entry["variants"] = all_variants
            vols.update_planning(vol_no, **{card_key: selected}, scaffold_cards=cards)
        else:
            if body.custom_value is not None and isinstance(body.custom_value, str):
                selected_text = body.custom_value
            else:
                variants = entry.get("variants") or []
                idx = int(body.pick_index)
                if idx < 0 or idx >= len(variants):
                    raise HTTPException(422, f"版本序号越界：{card_key}[{idx}]")
                selected_text = variants[idx]
            entry["picked_idx"] = body.pick_index if body.custom_value is None else -1
            entry["locked_at"] = now
            entry["text"] = selected_text
            vols.update_planning(vol_no, scaffold_cards=cards)

        all_keys = ["death_nodes", "climax_nodes", "clue_nodes", "core_twist", "tangchi_start", "cost_cap"]
        # 重新加载以拿到最新值
        vol = vols.get(vol_no)
        all_locked = all(((vol.scaffold_cards or {}).get(k) or {}).get("locked_at") for k in all_keys)
        return {
            "ok": True,
            "card_key": card_key,
            "picked_idx": idx,
            "locked_at": now,
            "all_locked": all_locked,
            "volume": vol.as_dict(),
        }

    # ----- POST /scaffold/derive-from-embody -----
    def _scaffold_derive_from_embody(
        pid: str,
        vol_no: int,
        body: _DeriveFromEmbodyIn = Body(...),
    ):
        """基于魂穿产物（七日历+宿主+切入点+信息差+姿态）一键产出 6 卡×3 版，并默认锁定每卡的第1版为"A 版"。
        - 如果 scaffold 已完成（stage_completed.scaffold=True），默认拒绝；传 force=True 覆盖。
        - 如果已经有 scaffold_cards（中途状态），也拒绝（避免覆盖已选）；同样由 force 控制。
        """
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        if not vol.tangchi_story_outline:
            raise HTTPException(422, "请先完成魂穿（唐迟七日历为空）")

        sc = vol.stage_completed or {}
        cards_exist = bool(vol.scaffold_cards and any(
            (vol.scaffold_cards.get(k) or {}).get("variants") for k in _ALL_KEYS
        ))
        if (sc.get("scaffold") or cards_exist) and not body.force:
            raise HTTPException(409, {
                "code": "ALREADY_PREFILLED",
                "msg": "副本详设已存在，若要重新生成请传 force=true（会清空已选/已锁）",
            })

        # 清空旧数据
        fresh_cards: dict = {}
        for k in _TEXT_KEYS:
            fresh_cards[k] = {"variants": [], "picked_idx": None, "locked_at": None, "text": None}
        for k in _LIST_KEYS:
            fresh_cards[k] = {"picked_idx": None, "locked_at": None, "variants": []}
        vols.update_planning(
            vol_no,
            death_nodes=[], climax_nodes=[], clue_nodes=[],
            pasted_nodes=[], scaffold_cards=fresh_cards,
        )
        vol = vols.get(vol_no)

        is_mock = _is_mock(vol)
        context = _build_context(vol, body.directive or "")

        if is_mock:
            data = _mock_payload()
        else:
            data = _call_llm_cards(
                deps,
                _SCAFFOLD_SYSTEM,
                context,
                "请严格按 system 给定 JSON schema 返回 6 卡×3 版详设，不要输出任何其他文字。",
                _mock_payload(),
            )

        payload = {k: (data.get(k) or []) for k in _ALL_KEYS}
        # 列表卡统一成 {rhythm_intent, nodes} 形状（旧数据会自动包一层空意图）
        for k in _LIST_KEYS:
            payload[k] = _normalize_list_card(payload.get(k))

        now = _now()
        new_cards: dict = {}
        # 文本卡：variants=[v1,v2,v3]，picked_idx=0 默认锁 A 版，text=v1
        for k in _TEXT_KEYS:
            vs = payload.get(k) or []
            if len(vs) < 1:
                vs = ["（占位文本）"]
            new_cards[k] = {
                "variants": vs,
                "picked_idx": 0,
                "locked_at": now,
                "text": vs[0],
            }
        # 列表卡：variants 存 {rhythm_intent, nodes}；顶层字段存 A 版的纯节点数组
        for k in _LIST_KEYS:
            arr = payload.get(k) or []
            if not arr:
                arr = [{"rhythm_intent": "", "nodes": []}]
            new_cards[k] = {
                "variants": arr,
                "picked_idx": 0,
                "locked_at": now,
                "rhythm_intent": _intent_of(arr[0]),
                "rhythm_intents": [_intent_of(v) for v in arr],
            }

        # 顶层层字段写入 vol（存 A 版的纯节点数组——desk/brief 按节点数组消费）
        vols.update_planning(
            vol_no,
            death_nodes=_nodes_of((payload.get("death_nodes") or [None])[0]),
            climax_nodes=_nodes_of((payload.get("climax_nodes") or [None])[0]),
            clue_nodes=_nodes_of((payload.get("clue_nodes") or [None])[0]),
            scaffold_cards=new_cards,
        )
        vol = vols.get(vol_no)
        return {
            "ok": True,
            "is_mock": is_mock,
            "locked_default": "A",
            "payload": payload,
            "scaffold_cards": new_cards,
            "volume": vol.as_dict(),
            # 全卡已默认锁 A 版，所以可以直接 finalize；但这里不自动 finalize 留给前端/作者确认
            "all_locked_prefilled": True,
        }

    # ----- POST /scaffold/reroll-card/{card_key} -----
    def _scaffold_reroll_card(
        pid: str,
        vol_no: int,
        card_key: str,
        body: _RerollCardIn = Body(...),
    ):
        """对单张卡做🔄重抽：返回 2 个差异化变体，追加/替换 variants。
        - 如果该卡已锁定：报错（不允许对已锁卡 reroll；先由前端解锁或作者确认）。
        - variants 返回后追加到该卡的 variants 列表末尾，不自动选。
        """
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        if card_key not in _LIST_KEYS | _TEXT_KEYS:
            raise HTTPException(422, f"未知卡片：{card_key}")

        cards = dict(vol.scaffold_cards or {})
        entry = dict(cards.get(card_key) or {})
        if entry.get("locked_at"):
            raise HTTPException(409, {"code": "CARD_LOCKED", "msg": f"「{card_key}」已锁定，不能重抽"})

        is_mock = _is_mock(vol)
        context = _build_context(vol, body.directive or "")
        sys_prompt = _SCAFFOLD_SINGLE_CARD_SYSTEMS[card_key]

        # fallback：mock 数据里取该卡的后 2 个版（B/C）作为 reroll 结果
        mock = _mock_payload()
        fallback = {card_key: (mock.get(card_key) or [])[-2:]} if mock.get(card_key) else {card_key: []}

        if is_mock:
            data = fallback
        else:
            data = _call_llm_cards(
                deps, sys_prompt, context,
                f"请严格按 system 给定 JSON schema 返回【{card_key}】的 2 个差异化候选变体，不要输出其他文字。",
                fallback,
            )
        new_variants = data.get(card_key) or []
        if card_key in _LIST_KEYS:
            # 重抽的候选统一成 {rhythm_intent, nodes}；重抽出来的两版必须意图不同
            new_variants = _normalize_list_card(new_variants)
        if len(new_variants) < 1:
            raise HTTPException(500, "LLM 返回空结果，重试或换指令")
        # 规范成 2 版（截断/补齐）
        new_variants = list(new_variants)[:2]

        # 写入 cards 与顶层数组
        existing_variants = list(entry.get("variants") or [])
        if card_key in _LIST_KEYS:
            existing_intents = list(entry.get("rhythm_intents") or [])
            top_arr = list(getattr(vol, card_key, None) or [])
            for v in new_variants:
                if _nodes_of(v):
                    top_arr.append(_nodes_of(v))
                existing_variants.append(v)
                existing_intents.append(_intent_of(v))
            entry["variants"] = existing_variants
            entry["rhythm_intents"] = existing_intents
            cards[card_key] = entry
            kwargs = {card_key: top_arr, "scaffold_cards": cards}
        else:
            for v in new_variants:
                if isinstance(v, str) and v.strip():
                    existing_variants.append(v)
            entry["variants"] = existing_variants
            cards[card_key] = entry
            kwargs = {"scaffold_cards": cards}

        vols.update_planning(vol_no, **kwargs)
        vol = vols.get(vol_no)
        return {
            "ok": True,
            "card_key": card_key,
            "new_variants": new_variants,
            "variant_count": len(existing_variants),
            "is_mock": is_mock,
            "volume": vol.as_dict(),
        }

    # ----- POST /scaffold/finalize -----
    def _scaffold_finalize(
        pid: str,
        vol_no: int,
        body: _FinalizeIn = Body(...),
    ):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        cards = vol.scaffold_cards or {}
        all_keys = ["death_nodes", "climax_nodes", "clue_nodes", "core_twist", "tangchi_start", "cost_cap"]
        missing = [k for k in all_keys if not (cards.get(k) or {}).get("locked_at")]
        if missing:
            raise HTTPException(422, {"code": "NOT_ALL_LOCKED", "missing": missing, "msg": f"还有 {len(missing)} 张卡未锁定"})

        sc = vol.stage_completed or {}
        sc["scaffold"] = True
        vols.update_planning(vol_no, stage_completed=sc)
        vol = vols.get(vol_no)
        return {"ok": True, "stage_completed": vol.stage_completed, "volume": vol.as_dict()}

    # ---- 注册路由（函数定义之后，避免 UnboundLocalError）----
    router.add_api_route(f"{prefix}/prefill", _scaffold_prefill, methods=["POST"])
    router.add_api_route(f"{prefix}/derive-from-embody", _scaffold_derive_from_embody, methods=["POST"])
    router.add_api_route(f"{prefix}/reroll-card/{{card_key}}", _scaffold_reroll_card, methods=["POST"])
    router.add_api_route(f"{prefix}/jobs", _scaffold_jobs, methods=["GET"])
    router.add_api_route(f"{prefix}/stream", _scaffold_stream, methods=["GET"])
    router.add_api_route(f"{prefix}/lock-card", _scaffold_lock_card, methods=["POST"])
    router.add_api_route(f"{prefix}/finalize", _scaffold_finalize, methods=["POST"])

    app.include_router(router)
