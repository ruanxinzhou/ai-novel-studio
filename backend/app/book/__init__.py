"""书级硬骨架：《第七日的亡者》专属持久配置。

七张跨场硬卡：
    premise.json        —— 世界观公理（双轨时间线/讣告/神不出场铁律）
    tangchi.json        —— 唐迟自我刻度（手艺↑/侵蚀↑ 双轴）
    endgame.json        —— 终局七层因果锚点
    cost_debts.json     —— 未兑现执念代价账
    remaining_pages.json —— 讣告残页集
    finale_notes.json   —— 杀青记录
    seven_day_pulse.json —— 唐迟入戏情绪张力基线（T-6→T-0，写作节拍，非原剧本日历天）

卷级规划板（M3）：
    volumes/            —— VolumeStore，每卷一卷规划板 + 杀青归账

所有 book/* 作为 REQUIRED 材料注入所有流水线层。
"""
from app.book.profile import BookProfile, BookStore
from app.book.volume import Volume, VolumeStore

__all__ = ["BookProfile", "BookStore", "Volume", "VolumeStore"]
