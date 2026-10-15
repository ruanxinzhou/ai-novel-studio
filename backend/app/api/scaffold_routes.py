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
  "death_nodes":  [[ {day,who,how,tease,cost}, ...3~5个死亡事件... ], [...第2版...], [...第3版...]],
  "climax_nodes": [[ {day,title,beat,weight}, ...2~3个高潮节点... ], [...第2版...], [...第3版...]],
  "clue_nodes":   [[ {day,where,what,leads_to,is_red_herring}, ...5条线索... ], [...第2版...], [...第3版...]],
  "core_twist":   [ "第1版核心反转文本，约80-120字", "第2版…", "第3版…" ],
  "tangchi_start":[ "第1版唐迟开篇文本，约80-120字", "第2版…", "第3版…" ],
  "cost_cap":     [ "第1版代价上限文本，约80-120字", "第2版…", "第3版…" ]
}

字段语义：
- death_nodes：死亡事件清单。day=1~7（数字），who=死者身份，how=死法（要带克苏鲁味的惊悚/诡异细节，40-80字），tease=死亡伏笔（正文里事先埋的细微异常，20-40字），cost=该死亡给唐迟/团队带来的代价（理智值/道具/关系/信息损失，20-40字）。
- climax_nodes：高潮/转折节点。day=1~7（数字），title=节点标题（5-12字），beat=高潮动作&反转（60-100字），weight=1~3 的整数（1=小反转、2=中高潮、3=终极高潮，每版恰好 1 个 weight=3）。
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


class _LockCardIn(BaseModel):
    card_key: str
    pick_index: int


class _FinalizeIn(BaseModel):
    pass


def _session_id(pid: str, vol_no: int) -> str:
    return f"scaff:{pid}:{vol_no}"


def _is_mock(vol) -> bool:
    try:
        am = vol.adapted_module or ""
        if isinstance(am, str) and am.startswith("测试副本核文本"):
            return True
        hc = vol.host_character or {}
        if isinstance(hc, dict) and "林薇" in (hc.get("name") or ""):
            return True
    except Exception:
        return False
    return False


def _mock_payload() -> dict:
    """6卡×3版 克苏鲁基调硬编码数据。"""
    return {
        "death_nodes": [
            # 版 A：慢热解谜·尸体逐个说话
            [
                {"day": 2, "who": "宿管阿姨王桂芬", "how": "凌晨三点被发现悬挂在宿舍楼外的晾衣绳上，脖颈缠绕着一团湿滑的黑发，头发末端探出细小的吸盘在月光下一张一合，像某种深海软体动物的触手。", "tease": "前一晚她曾念叨'水龙头里流出咸水'。", "cost": "失去唯一能查民国旧档的内线。"},
                {"day": 3, "who": "历史系研究生周凯", "how": "在校史陈列室里被自己翻开的合订本报纸吞噬，油墨从纸面渗出像黑色焦油糊满七窍，整张脸被吸成了一张扁平的报纸人，嘴还张成'O'型。", "tease": "他之前在微信群里发过'报纸上的人在看我'。", "cost": "即将拼出的'1927海难'线索断在关键一页。"},
                {"day": 5, "who": "林薇（小报记者）", "how": "第七教学楼天台边缘，她的影子在正午阳光下先于她一步跌落，随后身体像被影子拖拽般后仰坠下，落地时脸上保持着一个不属于她的、极满足的微笑。", "tease": "唐迟在前一夜看见她在镜子前练习那个微笑。", "cost": "唐迟 San -2；记者相机落入校工之手。"},
            ],
            # 版 B：生存紧逼·每天都死人
            [
                {"day": 1, "who": "宿管阿姨王桂芬", "how": "唐迟刚进宿舍的当晚，她在查房时被楼道里渗出的海水灌进肺里淹死——楼道里没一滴水，但她口鼻中源源不断涌出带着海腥气的咸水，整个人像在空气里溺亡。", "tease": "她开门时嘀咕了一句'今天走廊怎么这么潮'。", "cost": "开局即建立'空气溺亡'惊悚规则。"},
                {"day": 2, "who": "保安老刘", "how": "在巡逻至游泳馆废墟时被一团湿漉漉的校服拖拽进干涸的泳池底部，第二天打捞上来时他全身布满藤壶，皮肤已被泡成灰白色，像在海里沉了三年。", "tease": "他胸口别着的校徽背面刻着一个陌生女生的名字。", "cost": "失去夜间唯一可能的武力支援。"},
                {"day": 3, "who": "历史系研究生周凯", "how": "在校史室被旧报纸糊脸窒息而死，死时手里攥着一张1927年的毕业照，照片最角落的位置多出了一张和唐迟一模一样的脸。", "tease": "他死前给唐迟发了半条微信'你不是第一次——'。", "cost": "唐迟被卷入更深层轮回疑云，San -1。"},
                {"day": 4, "who": "中文系学姐苏雅", "how": "在图书馆翻到一本无字书后开始用自己的血在墙上写字，写到第七十七个字时全身血液从毛孔蒸干，变成一具趴在墙上的干尸，脸上是宗教狂热般的笑。", "tease": "前一天她反复哼着一段不成调的海妖童谣。", "cost": "损失一名理智尚存的队友；童谣线索被迫中断。"},
                {"day": 5, "who": "林薇（小报记者）", "how": "在天台被自己的影子拉下高楼，坠地瞬间影子脱离她的身体钻进下水道消失，留下的尸体没有影子——阳光直射下的地面干干净净。", "tease": "她相机里最后一张照片拍的是唐迟的脚下，那里本该没有影子。", "cost": "唐迟意识到自己的影子可能也有问题，San -2。"},
            ],
            # 版 C：诡计误导·身份错配
            [
                {"day": 3, "who": "宿管王桂芬（伪）", "how": "死在锅炉房里的'王桂芬'其实是1927年失踪女生陈阿妹借壳复活的皮囊；真正的王阿姨早在副本开启前就被替换。尸体被发现时正在缓慢融化成一滩咸水。", "tease": "唐迟此前注意到她右手的烫伤疤换了位置。", "cost": "信任崩塌——玩家无法判断 NPC 是否已被替换。"},
                {"day": 4, "who": "历史系研究生周凯", "how": "在意识到'整个校园都在演一出1927年的戏'后，他被自己的倒影拖进了校史馆的落地镜里，镜子里只剩一张贴在镜面的、正在尖叫的扁平人脸。", "tease": "他最后一句话是'我们都不是第一次来'。", "cost": "轮回/迭代线索由他口中抛出，代价是他再也不会开口。"},
                {"day": 5, "who": "林薇（小报记者）", "how": "在天台试图拍下'真正的幕后'时被一只从镜头里伸出来的、布满鳞片的手勒住脖子拖进相机；相机摔在地上，屏幕里她正在一片漆黑的海底下沉，手还在拍打玻璃。", "tease": "她相机前一晚拍到的唐迟，眼睛里有另一双眼睛。", "cost": "唐迟被标记为'容器候选人'，San -3；相机成为通向深渊的道具。"},
                {"day": 6, "who": "中文系学姐苏雅", "how": "发现校歌倒放是召唤咒后试图毁弃钟楼顶的铜钟，被从钟内壁涌出的绿色海藻状物质缠满全身拖进钟内，铜钟自鸣七下后归于沉寂，钟身渗出暗色液体。", "tease": "她给唐迟留了一张纸条'别信合唱队的指挥'。", "cost": "最终决战前损失最能辨识仪式的人。"},
            ],
        ],
        "climax_nodes": [
            # 版 A
            [
                {"day": 3, "title": "报纸睁眼", "beat": "周凯之死让唐迟发现旧报纸上的学生照片会眨眼，他意识到这些纸不是记录历史而是在窥视当下；第一次用火烧报纸，火里传出女声惨叫，纸灰拼成一个名字'陈阿妹'。", "weight": 1},
                {"day": 5, "title": "影子先行", "beat": "林薇当着唐迟的面坠楼，唐迟在天台水渍里看到自己的影子也在缓慢转身。他第一次明确知道'影子是独立个体'，并用盐圈暂时困住自己的影子争取到半天时间。", "weight": 2},
                {"day": 7, "title": "海床点名", "beat": "午夜游泳馆废墟被海水灌满，唐迟在水下看见1927年溺亡的47名学生坐在池底朝他举手'点名'，而讲台后站着的指挥者——是七天里一直温柔照顾他的辅导员，她露出了鳃。", "weight": 3},
            ],
            # 版 B
            [
                {"day": 2, "title": "空气溺亡", "beat": "刘保安死时楼道里完全干燥却涌出咸水，唐迟用布蘸水尝了一口——是真的海水。他意识到副本把'海'折进了这座内陆大学，规则第一条诞生：'听到潮声时不要回头'。", "weight": 1},
                {"day": 4, "title": "血字童谣", "beat": "苏雅干尸旁的墙上，她用自己血写的第七十七个字是唐迟的真名。唐迟意识到童谣在点名，而他是被选中的下一个。合唱队方向传来第一声齐唱。", "weight": 2},
                {"day": 7, "title": "合唱终章", "beat": "暴雨夜全校广播突然响起校歌倒放，所有幸存 NPC 眼神空洞向大礼堂汇集；唐迟冲进礼堂发现指挥台上的'指挥'不是人，而是一团由47张湿脸拼成的聚合体，它抬起手——最后一个音节即将被唱出。", "weight": 3},
            ],
            # 版 C
            [
                {"day": 3, "title": "皮囊融化", "beat": "'王桂芬'尸体融成咸水，唐迟强行撕下另一宿管手腕上的皮——人皮像蜡一样剥落露出底下发绿的鳞片。他意识到副本里大部分 NPC 早被替换，人类可能不足三成。", "weight": 1},
                {"day": 5, "title": "镜中周凯", "beat": "唐迟对着校史馆镜子呼唤周凯，镜面浮出一张被压平的脸用唇语说'你就是陈阿妹等的人'；唐迟在镜中看到自己穿着1927年的学生装。", "weight": 2},
                {"day": 7, "title": "第七次迭代", "beat": "唐迟在钟楼顶层发现刻在铜钟内壁的'自己的名字'出现了六次——他已经来过这个副本六次并死了六次。这一次林薇坠楼时的微笑是她保留前次记忆的证明，而唐迟自己也开始记起不属于这七天的事。", "weight": 3},
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
                parts = []
                am = vol.adapted_module or {}
                if isinstance(am, dict):
                    parts.append("===== 副本骨架（改编稿）=====")
                    parts.append(am.get("adapted_premise") or json.dumps(am, ensure_ascii=False)[:2000])
                elif am:
                    parts.append(f"===== 副本骨架 =====\n{am}")
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
                context = "\n".join(parts)

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
                    "death_nodes": data.get("death_nodes") or [],
                    "climax_nodes": data.get("climax_nodes") or [],
                    "clue_nodes": data.get("clue_nodes") or [],
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
                cards[key] = {"picked_idx": None, "locked_at": None, "variants": payload.get(key) or []}
            vols.update_planning(
                vol_no,
                death_nodes=payload.get("death_nodes") or [],
                climax_nodes=payload.get("climax_nodes") or [],
                clue_nodes=payload.get("clue_nodes") or [],
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
            all_variants = entry.get("variants") or getattr(vol, card_key, None) or []
            if idx < 0 or idx >= len(all_variants):
                raise HTTPException(422, f"版本序号越界：{card_key}[{idx}]")
            selected = all_variants[idx]
            entry["picked_idx"] = idx
            entry["locked_at"] = now
            entry["variants"] = all_variants
            vols.update_planning(vol_no, **{card_key: selected}, scaffold_cards=cards)
        else:
            variants = entry.get("variants") or []
            if idx < 0 or idx >= len(variants):
                raise HTTPException(422, f"版本序号越界：{card_key}[{idx}]")
            entry["picked_idx"] = idx
            entry["locked_at"] = now
            entry["text"] = variants[idx]
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
    router.add_api_route(f"{prefix}/jobs", _scaffold_jobs, methods=["GET"])
    router.add_api_route(f"{prefix}/stream", _scaffold_stream, methods=["GET"])
    router.add_api_route(f"{prefix}/lock-card", _scaffold_lock_card, methods=["POST"])
    router.add_api_route(f"{prefix}/finalize", _scaffold_finalize, methods=["POST"])

    app.include_router(router)
