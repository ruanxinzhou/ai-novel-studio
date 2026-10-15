"""终局七层因果锚点卡：追踪每层所需埋点数量、埋点进度、误导方向、兑现时机。"""
from __future__ import annotations

# 终局七层因果链（作者给定）
DEFAULT_ENDGAME = {
    "layers": [
        {
            "id": 1,
            "name": "无尽循环意识",
            "description": "文末最后一卷初，主角意识到这是无尽的表演循环",
            "required_seeds": 3,
            "seeds": [],  # 实际埋点
        },
        {
            "id": 2,
            "name": "蓝星也是戏",
            "description": "关键翻转：现实世界（蓝星）也是戏的一部分，主角从未真正回家",
            "required_seeds": 5,
            "seeds": [],
        },
        {
            "id": 3,
            "name": "讣告写自己",
            "description": "卷十讣告送达，死者是唐迟自己（自己的名字出现在讣告上）",
            "required_seeds": 4,
            "seeds": [],
        },
        {
            "id": 4,
            "name": "读者以为要活",
            "description": "让主角与读者同以为：再惊险活过七天就能脱离",
            "required_seeds": 4,
            "seeds": [],
        },
        {
            "id": 5,
            "name": "主动选最无聊死法",
            "description": "主角不再挣扎求生，主动选择对神来说最无聊的死法——读者/神都没想到",
            "required_seeds": 3,
            "seeds": [],
        },
        {
            "id": 6,
            "name": "神觉无聊松绑",
            "description": "神的兴趣消退→循环松绑→主角在神不看的地方复生",
            "required_seeds": 3,
            "seeds": [],
        },
        {
            "id": 7,
            "name": "目的本就是逃离",
            "description": "揭示主角的目的从头到尾不是战胜神而是退出循环保留自我；半疯着活",
            "required_seeds": 3,
            "seeds": [],
        },
    ],
}


def render_endgame(d: dict) -> str:
    layers = d.get("layers", [])
    # 找出目前还需要埋的点（按场分配）
    pending_layers = []
    for layer in layers:
        seeds = layer.get("seeds", [])
        buried = sum(1 for s in seeds if s.get("status") in ("buried", "hinted", "teased"))
        paid = sum(1 for s in seeds if s.get("status") == "paid")
        need = int(layer.get("required_seeds", 0))
        remaining = need - buried
        if remaining > 0:
            pending_layers.append((layer, remaining, buried, paid))

    out = ["## 终局七层因果锚点\n"]
    if not pending_layers:
        out.append("所有七层锚点已按计划埋入。\n")
    else:
        out.append("**近期写作需要注意埋的针**（埋点越靠前越要隐蔽，误导方向要明确）：\n")
        for layer, remaining, buried, paid in pending_layers[:3]:
            out.append(
                f"- 第{layer['id']}层「{layer['name']}」："
                f"已埋 {buried}/{layer['required_seeds']}，已兑现 {paid}，"
                f"还差 {remaining} 处埋点。"
                f"误导方向：{layer.get('next_hint','自然融入情节，勿直白')}"
                if layer.get("next_hint") else
                f"- 第{layer['id']}层「{layer['name']}」："
                f"已埋 {buried}/{layer['required_seeds']}，还差 {remaining} 处埋点。"
            )
        out.append(
            "\n埋点原则：埋针不是剧透。"
            "表面读者读起来应该以为是别的意思（误导方向），"
            "真相层指向终局。写的时候主角（唐迟视角）绝对不能意识到这些暗示，"
            "否则视角泄漏。\n"
        )
    return "\n".join(out)
