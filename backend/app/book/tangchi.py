"""唐迟自我刻度卡：手艺↑ / 侵蚀↑ 双轴跨场累积。"""
from __future__ import annotations

DEFAULT_TANGCHI = {
    "crafts": [],                  # [{field_no, name, desc, chapters_used}]
    "erosion_level": 0,            # 0-100
    "erosion_signs": [],           # [{field_no, sign}]  侵蚀外显迹象（半疯痕迹）
    "madness_milestones": [        # 阈值→症状（审稿可提示）
        {"threshold": 20, "symptom": "偶尔分不清自己是唐迟还是附身过的死者"},
        {"threshold": 40, "symptom": "说话口吻偶尔切换为死者语气，自己没察觉"},
        {"threshold": 60, "symptom": "照镜子时偶尔认不出自己；现实感偶尔抽离"},
        {"threshold": 80, "symptom": "长时间独处会听到死者低语；现实与戏边界模糊"},
        {"threshold": 95, "symptom": "半疯狂状态；但也是最接近真相、最能逃的时候"},
    ],
    "home_memories_faded": [],     # [{field_no, memory_lost}]  对家的记忆淡化
}


def render_tangchi(d: dict) -> str:
    erosion = int(d.get("erosion_level", 0))
    crafts = d.get("crafts", [])
    signs = d.get("erosion_signs", [])
    milestones = d.get("madness_milestones", [])
    # 找当前已达到的疯狂阈值
    reached = [m for m in milestones if erosion >= m["threshold"]]
    next_milestone = next((m for m in milestones if erosion < m["threshold"]), None)

    lines = [
        "## 唐迟自我刻度（跨场累积）\n",
        f"**当前侵蚀度**：{erosion}/100",
        f"**累积手艺数**：{len(crafts)}",
    ]
    if crafts:
        lines.append("\n**已掌握的手艺**：")
        for c in crafts[-8:]:
            lines.append(f"- 第{c['field_no']}场·{c['name']}：{c['desc']}")
        if len(crafts) > 8:
            lines.append(f"- …（其余 {len(crafts)-8} 项略）")
    if signs:
        lines.append("\n**已显现的侵蚀迹象**：")
        for s in signs[-5:]:
            lines.append(f"- 第{s['field_no']}场后：{s['sign']}")
    if reached:
        lines.append(f"\n**当前疯狂状态**：{reached[-1]['symptom']}")
    if next_milestone:
        lines.append(f"\n**下一阈值**（侵蚀度 {next_milestone['threshold']}）：{next_milestone['symptom']}")
    lines.append(
        "\n写作提醒：爽点（新手艺/小装 X）必须与侵蚀一体两面——"
        "爽的瞬间就埋下一个自我迷失的小信号，不能只爽不付代价。"
    )
    return "\n".join(lines) + "\n"
