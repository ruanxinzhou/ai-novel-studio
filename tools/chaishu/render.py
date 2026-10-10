"""渲染层：底料 → report.md（十行节奏决策表，只印聚合数不印情节）+ arc.md（全书脉络）。

红线（圆桌决议）：report.md 只输出聚合数与段落区间，不出现具体情节内容。
量纲（鸭哥）：章距必须带字数量纲（"每 X 万字一次爽点"），才可迁移到自己的书。
"""
from __future__ import annotations

import json
from pathlib import Path

from engine import trae_exec

EVENT_CN = {"shuangdian": "爽点", "foreshadow": "伏笔", "upgrade": "升级", "other": "转折"}


def _gaps(nos: list[int]) -> list[int]:
    return [b - a for a, b in zip(nos, nos[1:]) if b > a]


def _median(values: list[int]) -> int:
    s = sorted(values)
    if not s:
        return 0
    return s[len(s) // 2]


def _wan(chars: int) -> str:
    return f"{chars / 10000:.1f}万"


def _has_event(label: dict, etype: str) -> bool:
    return any(e["type"] == etype for e in label.get("events", []))


def collect(chapters: list[dict], labels: dict[int, dict]) -> dict:
    """从底料聚合出报告所需的全部统计（纯计算，无 LLM）。"""
    chars = [c["chars"] for c in chapters]
    labeled_nos = sorted(n for n in labels if n != 0)
    seq = [c for c in chapters if c["no"] in labels]  # 已标注章，按书序

    shuang_nos = [c["no"] for c in seq if _has_event(labels[c["no"]], "shuangdian")]
    upgrade_nos = [c["no"] for c in seq if labels[c["no"]].get("power_up")
                   or _has_event(labels[c["no"]], "upgrade")]
    foreshadow_nos = [c["no"] for c in seq if _has_event(labels[c["no"]], "foreshadow")]
    hook_count = sum(1 for c in seq if labels[c["no"]].get("hook"))
    ev_counts = [len(labels[c["no"]].get("events", [])) for c in seq]

    # 蓄力段：连续无爽点的已标注章序列，累计章号跨度 ≥5 且含 ≥3 章
    buildup: list[tuple[int, int]] = []
    run_start, run_len, run_span = None, 0, 0
    prev_no = None
    for c in seq:
        no = c["no"]
        if _has_event(labels[no], "shuangdian"):
            if run_start is not None and run_span >= 5 and run_len >= 3:
                buildup.append((run_start, prev_no))
            run_start, run_len, run_span = None, 0, 0
        else:
            if run_start is None:
                run_start, run_len, run_span = no, 1, 0
            else:
                run_len += 1
                run_span += no - prev_no
        prev_no = no
    if run_start is not None and run_span >= 5 and run_len >= 3:
        buildup.append((run_start, prev_no))

    # 蓄爆距离：蓄力段结束章 → 下一个爽点章
    shuang_set = set(shuang_nos)
    burst_dist: list[int] = []
    for _, end in buildup:
        nxt = [n for n in shuang_nos if n > end]
        if nxt:
            burst_dist.append(min(nxt) - end)

    # 伏笔周期（近似口径）：埋点章 → 之后 50 章内最近的回收章（爽点/升级/转折）的章距
    recover_nos = [c["no"] for c in seq
                   if any(_has_event(labels[c["no"]], t) for t in ("shuangdian", "upgrade", "other"))]
    foreshadow_gaps: list[int] = []
    unresolved: list[int] = []
    for f in foreshadow_nos:
        nxt = [r for r in recover_nos if f < r <= f + 50]
        if nxt:
            foreshadow_gaps.append(min(nxt) - f)
        else:
            unresolved.append(f)

    return {
        "n_chapters": len(chapters), "total_chars": sum(chars),
        "avg_chars": sum(chars) // max(1, len(chars)),
        "p25": _pct(chars, 25), "p50": _pct(chars, 50), "p75": _pct(chars, 75),
        "volumes": _volume_rows(chapters),
        "n_labeled": len(seq), "labeled_nos": labeled_nos,
        "hook_rate": hook_count, "hook_total": len(seq),
        "ev_counts": ev_counts,
        "shuang_nos": shuang_nos, "shuang_gaps": _gaps(shuang_nos),
        "upgrade_nos": upgrade_nos, "upgrade_gaps": _gaps(upgrade_nos),
        "foreshadow_nos": foreshadow_nos, "foreshadow_gaps": foreshadow_gaps,
        "unresolved": unresolved, "buildup": buildup, "burst_dist": burst_dist,
    }


def _pct(values: list[int], p: int) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))]


def _volume_rows(chapters: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for ch in chapters:
        if not rows or rows[-1]["volume"] != ch["volume"]:
            rows.append({"volume": ch["volume"], "from": ch["no"], "to": ch["no"],
                         "chars": ch["chars"], "n": 1})
        else:
            r = rows[-1]
            r["to"], r["n"] = ch["no"], r["n"] + 1
            r["chars"] += ch["chars"]
    for r in rows:
        r["avg"] = r["chars"] // max(1, r["n"])
    return rows


def render_report(book: str, s: dict, sampled: bool) -> str:
    """十行决策表。红线：只印聚合数与区间，不印情节。"""
    avg = s["avg_chars"]
    lines: list[str] = [f"# 《{book}》节奏体检表", ""]
    if sampled:
        lines += [f"> **试点模式**：基于抽样 {s['n_labeled']} 章（共 {s['n_chapters']} 章），"
                  f"数字代表抽样区间的节奏，全量跑后更新。", ""]

    lines += ["## 一、总量与体格", ""]
    lines += [f"- 总章数 **{s['n_chapters']}** 章，总字数 **{s['total_chars']:,}**"
              f"（{_wan(s['total_chars'])}字），均章 **{s['avg_chars']:,}** 字"
              f"（P25 {s['p25']:,} / P50 {s['p50']:,} / P75 {s['p75']:,}）", ""]
    lines += ["| 卷 | 起止章 | 章数 | 卷字数 | 均章字数 |", "|---|---|---|---|---|"]
    for v in s["volumes"]:
        lines.append(f"| {v['volume']} | {v['from']}–{v['to']} | {v['n']} "
                     f"| {v['chars']:,} | {v['avg']:,} |")
    lines.append("")

    n = s["n_labeled"]
    if not n:
        lines += ["## 二、节奏引擎", "", "（语义层未跑或无收据：用 `python chaishu.py 书.txt` 补标签后重新生成）", ""]
        return "\n".join(lines)

    def wan_per(gap: int) -> str:
        return f"（≈{_wan(gap * avg)}字）" if gap else ""

    lines += ["## 二、节奏引擎（写手最关心的四行）", ""]
    sg = s["shuang_gaps"]
    lines += [f"- **爽点章距**：爽点章 {len(s['shuang_nos'])}/{n}，"
              f"相邻间隔中位 **{_median(sg)} 章**（最大 {max(sg) if sg else 0} 章{wan_per(_median(sg))}）"
              f"——压几章爆一次", ""]
    evc = s["ev_counts"]
    lines += [f"- **事件密度**：每章事件数中位 **{_median(evc)}**"
              f"（0 事件的章 {evc.count(0)}/{n}）——堆叠还是单线推进", ""]
    lines += [f"- **断章钩子率**：**{s['hook_rate']}/{n} = {s['hook_rate'] * 100 // max(1, n)}%**"
              f"——期待管理强度", ""]
    ug = s["upgrade_gaps"]
    lines += [f"- **升级/获得间隔**：升级章 {len(s['upgrade_nos'])}/{n}，"
              f"间隔中位 **{_median(ug)} 章**（最大 {max(ug) if ug else 0} 章{wan_per(_median(ug))}）"
              f"——成长线骨架", ""]

    lines += ["## 三、节奏异象（这本书的个性）", ""]
    if s["buildup"]:
        segs = "、".join(f"第{a}–{b}章" for a, b in s["buildup"][:10])
        more = f" 等 {len(s['buildup'])} 段" if len(s["buildup"]) > 10 else ""
        lines += [f"- **蓄力段**（连续≥5章无爽点）：{segs}{more}——哪里在憋大招", ""]
    else:
        lines += ["- **蓄力段**：无（无连续 5 章以上的爽点真空）", ""]
    if s["burst_dist"]:
        lines += [f"- **蓄爆距离**：蓄力段结束后中位 **{_median(s['burst_dist'])} 章** 出爽点"
                  f"（≈{_wan(_median(s['burst_dist']) * avg)}字）", ""]
    fg = s["foreshadow_gaps"]
    lines += [f"- **伏笔周期**（近似口径：埋点→50章内最近回收章）：埋点 {len(s['foreshadow_nos'])} 章，"
              f"回收章距中位 **{_median(fg)} 章**（最大 {max(fg) if fg else 0} 章），"
              f"50 章内未见回收 {len(s['unresolved'])} 处——老书迷最认的功力指标", ""]
    return "\n".join(lines)


def render_arc(book: str, chapters: list[dict], labels: dict[int, dict]) -> str:
    """全书脉络：每章一行概要（作者对照手感用，含情节属正常——不进报告红线区）。"""
    lines = [f"# 《{book}》全书脉络", "", "章号 | 卷 | 章名 | 字数 | 钩 | 事件 | 概要", "|---|---|---|---|---|---|---|"]
    for c in chapters:
        lb = labels.get(c["no"])
        if not lb:
            lines.append(f"| {c['no']} | {c['volume']} | {c['title']} | {c['chars']:,} | – | – | （未标注） |")
            continue
        hook = "✓" if lb.get("hook") else ""
        evs = "、".join(
            f"{EVENT_CN.get(e['type'], e['type'])}{(':' + e['note']) if e.get('note') else ''}"
            for e in lb.get("events", [])[:3])
        lines.append(f"| {c['no']} | {c['volume']} | {c['title']} | {c['chars']:,} "
                     f"| {hook} | {evs} | {lb.get('summary', '')} |")
    return "\n".join(lines) + "\n"


def diagnose(book: str, s: dict, labels: dict[int, dict], chapters: list[dict]) -> str:
    """第二次 LLM 调用：读全书标签总评，输出三行诊断。"""
    stats = {
        "总章数": s["n_chapters"], "总字数": s["total_chars"], "均章字数": s["avg_chars"],
        "抽样章数": s["n_labeled"],
        "爽点间隔中位(章)": _median(s["shuang_gaps"]),
        "断章钩子率": f"{s['hook_rate'] * 100 // max(1, s['hook_total'])}%",
        "升级间隔中位(章)": _median(s["upgrade_gaps"]),
        "每章事件数中位": _median(s["ev_counts"]),
        "蓄力段数": len(s["buildup"]),
        "伏笔回收间隔中位(章)": _median(s["foreshadow_gaps"]),
    }
    digest = "\n".join(
        f"第{c['no']}章 hook={int(labels[c['no']].get('hook', False))} "
        f"power_up={int(labels[c['no']].get('power_up', False))} "
        f"events={[e['type'] for e in labels[c['no']].get('events', [])]} "
        f"{labels[c['no']].get('summary', '')}"
        for c in chapters if c["no"] in labels
    )[:30000]
    prompt = (f"以下是一部网文《{book}》的节奏统计与逐章摘要（抽样）。"
              f"请只输出恰好 3 行的诊断（每行以「1.」「2.」「3.」开头，不要多余文字）：\n"
              f"1. 节奏型判断：给一个准确的自创标签（如「高压快爽型」「慢热蓄爆型」）并用一句话说明依据；\n"
              f"2. 最值得学习（结构层面）的 3 个优点，一行写完；\n"
              f"3. 最该避开的 1 个坑。\n"
              f"只谈结构与节奏，禁止引用具体情节。\n\n【统计】{json.dumps(stats, ensure_ascii=False)}\n"
              f"\n【逐章摘要】\n{digest}")
    return trae_exec(prompt)


def render_all(out_dir: Path, book: str, chapters: list[dict], labels: dict[int, dict],
               sampled: list[int] | None, skip_diagnose: bool) -> None:
    s = collect(chapters, labels)
    report = render_report(book, s, sampled is not None)
    if labels and not skip_diagnose:
        try:
            diag = diagnose(book, s, labels, chapters)
            report += "\n## 四、一句话诊断\n\n" + diag.strip() + "\n"
        except Exception as e:
            report += f"\n## 四、一句话诊断\n\n（诊断调用失败：{e}）\n"
    elif labels:
        report += "\n## 四、一句话诊断\n\n（--skip-diagnose 已跳过）\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "arc.md").write_text(render_arc(book, chapters, labels), encoding="utf-8")
