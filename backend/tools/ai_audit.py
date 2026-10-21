"""AI 含量审计器 v2 —— lieflat 实证指标 + 活人节奏指标 + A/B 对比。

用法：
    python tools/ai_audit.py                       # 审计项目所有章节所有阶段
    python tools/ai_audit.py --ch 2                # 只审计第2章 draft/polish 对比
    python tools/ai_audit.py --file 某文件.txt      # 审计任意正文文件
    python tools/ai_audit.py --ab                  # A/B 对比模式：看润色是否磨掉活人感
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

try:
    from app.generation.humanize import (
        _burstiness_stats, _rhythm_metrics, _lieflat_ai_signals,
        _detect_opening_template,
    )
except Exception:  # 独立运行兜底
    def _burstiness_stats(text: str) -> dict:
        chars = len(text.strip())
        if not chars:
            return {k: 0 for k in ("question_density", "sentence_cv", "char_type_density",
                                  "comma_density", "number_density", "time_anchor_density",
                                  "zero_anaphora_hits", "personified_simile")}
        q = len(re.findall(r"[？?]", text))
        c = len(re.findall(r"[，,]", text))
        sents = [s for s in re.split(r"[。！？!?]+", text) if s.strip()]
        lens = [len(s.strip()) for s in sents]
        cv = (statistics.pstdev(lens) / statistics.mean(lens)) if len(lens) > 1 and statistics.mean(lens) else 0.0
        cn = re.findall(r"[一-鿿]", text[:1500])
        return {
            "question_density": round(q * 1000 / chars, 2),
            "sentence_cv": round(cv, 3),
            "char_type_density": round(len(set(cn)) / len(cn), 3) if cn else 0.0,
            "comma_density": round(c * 1000 / chars, 2),
            "number_density": 0.0, "time_anchor_density": 0.0,
            "zero_anaphora_hits": 0, "personified_simile": 0,
        }
    def _rhythm_metrics(text: str) -> dict: return {}
    def _lieflat_ai_signals(text: str) -> dict: return {}
    def _detect_opening_template(text: str): return ("", "")


# ── 传统 AI 套话（表面信号，弱） ──────────────────────────────────
CLICHES = [
    "仿佛", "似乎", "某种", "一丝", "一抹", "涌上", "涌起", "难以言喻", "不可言喻",
    "五味杂陈", "百感交集", "感慨万千", "浮上眼帘", "映入眼帘", "扑面而来",
    "嘴角勾起", "深吸一口气", "缓缓地", "微微地", "空气中弥漫", "空气仿佛凝固",
    "不容置疑", "毋庸置疑", "显而易见", "不由得", "情不自禁", "自然而然",
    "真正的危机", "故事才刚刚开始", "命运的齿轮", "他不知道的是",
    "像一位智慧的导师", "像一位", "树立了", "诠释了", "体现了",
]
ABSTRACT_TAIL = re.compile(
    r"(五味杂陈|百感交集|感慨万千|久久不能平静|涌上心头|让他感到|让她感到|"
    r"感慨良多|心中泛起|这一切|一切都结束了)[。！？]?$"
)
NARRATIVE_FILTER = re.compile(r"(他|她|唐迟|陆知返|江喻)(看见|看到|感到|觉得|注意到|发现|意识到|明白)")
UNIVERSAL_PHYSIO = re.compile(
    r"(后背发凉|头皮发麻|心跳如鼓|指尖一僵|呼吸一窒|空气凝固|瞳孔骤缩|后颈.{0,4}汗毛|嘴角勾起)"
)
SEAM_BAD_START = re.compile(r"^(与此同时|话音刚落|话音未落|就在这时|就在这一刻|霎时间|刹那间|下一秒|紧接着|随后)")

# 动作收尾特征（段尾落点）
ACTION_TAIL = re.compile(
    r"(起来|过来|下去|上去|走了|站着|坐着|看着|听着|伸手|收回|转身|抬头|低头|点头|摇头|"
    r"抓起|放下|摸了摸|摸出|掏出|递|推|拉|打开|关上|起身|蹲下|弯腰|伸手|按住|点了|踩)"
)

# ── 活人节奏指标（我们新加的：润色不能磨掉的） ─────────────────────
def rhythm_metrics(text: str) -> dict:
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    n = len(paras)
    if n == 0:
        return {}
    lens = [len(p) for p in paras]
    mean = statistics.mean(lens)
    cv = statistics.pstdev(lens) / mean if mean else 0.0
    short = sum(1 for L in lens if L <= 12)          # 极短段
    tiny = sum(1 for L in lens if 2 <= L <= 6)        # 2-6字段
    single = sum(1 for p in paras if len(re.findall(r"[。！？!?]", p)) <= 1)
    action_tail = sum(1 for p in paras if ACTION_TAIL.search(p.rstrip("。！？!?…")))
    dialog_tail = sum(1 for p in paras if re.search(r"[「」“”\"']", p.rstrip("。！？!?…")))
    abstract_tail = sum(1 for p in paras if ABSTRACT_TAIL.search(p))
    buckets = collections.Counter()
    for L in lens:
        buckets["1-20" if L <= 20 else "21-40" if L <= 40 else "41-60" if L <= 60
                else "61-100" if L <= 100 else "101+"] += 1
    return {
        "段落数": n,
        "段落均长": round(mean, 1),
        "段落CV": round(cv, 3),
        "极短段(≤12字)": short,
        "极短段占比": round(short * 100 / n, 1),
        "超短段(2-6字)": tiny,
        "单句成段占比": round(single * 100 / n, 1),
        "动作收尾占比": round(action_tail * 100 / n, 1),
        "对白收尾占比": round(dialog_tail * 100 / n, 1),
        "抽象收尾数": abstract_tail,
        "段长分布": dict(buckets),
    }


# ── 主审计 ────────────────────────────────────────────────────────
def audit(text: str, label: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        return {"label": label, "字数": 0}
    stats = _burstiness_stats(text)
    rh = _rhythm_metrics(text)
    lf = _lieflat_ai_signals(text)
    tmpl, tmpl_ev = _detect_opening_template(text)
    per_k = lambda x: round(x * 1000 / len(text), 2)
    hits = {w: text.count(w) for w in CLICHES if text.count(w) > 0}

    r = {
        "label": label,
        "字数": len(text),
        # ── lieflat 实证指标 ──
        "问号密度": stats["question_density"],
        "句长CV": stats["sentence_cv"],
        "字种密度": stats["char_type_density"],
        "数字密度": stats.get("number_density", 0),
        "时间锚点密度": stats.get("time_anchor_density", 0),
        "拟人化喻体": stats.get("personified_simile", 0),
        "零回指命中": stats.get("zero_anaphora_hits", 0),
        # ── lieflat 11 项 AI 指纹 ──
        "AI指纹": lf,
        "章首模板": tmpl,
        # ── 传统套话 ──
        "叙事过滤词": len(NARRATIVE_FILTER.findall(text)),
        "万能生理反应": len(UNIVERSAL_PHYSIO.findall(text)),
        "段尾抽象总结": rh.get("抽象收尾数", 0),
        "套话禁词": sum(hits.values()),
        "套话明细": hits,
        "千字感叹号": per_k(text.count("！") + text.count("!")),
        # ── 段长阶梯（xt K 条款） ──
        **{k: v for k, v in rh.items() if k != "段长分布"},
    }
    r["判定"] = judge(r)
    return r


def judge(r: dict) -> list[str]:
    v = []
    q, ct = r["问号密度"], r["字种密度"]
    if q < 2:
        v.append("P0 问号密度<2（人机差 17 倍，AI 强信号）")
    elif q < 5:
        v.append("P1 问号偏少（建议 5-8）")
    num, ta = r["数字密度"], r["时间锚点密度"]
    if num < 2:
        v.append("P1 具体数字<2/千字（人类 2.8×）")
    if ta < 1:
        v.append("P1 时间锚点<1/千字（人类 2.6×）")
    if ct < 0.25:
        v.append("P0 字种密度<0.25")
    elif ct < 0.35:
        v.append("P1 字种偏低")

    # 段长阶梯（xt K 条款）
    if r.get("段落数", 0) >= 8:
        if r.get("长段数(>80字)", 0) < 3:
            v.append(f"P0 节奏：长段仅 {r.get('长段数(>80字)')} 个（要求 ≥3）"
                     f"——全是中短段拼起来的匀速机感，这是最严重的节奏问题")
        elif r.get("超长段数(>120字)", 0) == 0:
            v.append(f"P1 节奏：无 >120 字长段（建议 ≥1）")
        if r.get("最长连续单句段", 0) >= 6:
            v.append(f"P0 节奏：连续 {r.get('最长连续单句段')} 段单句成段"
                     f"（≥6 是典型 AI 瀑布体）")
        elif r.get("单句成段占比", 0) > 50:
            v.append(f"P1 节奏：单句成段占比 {r.get('单句成段占比')}%（上限 30%）")
        if r.get("动作收尾占比", 100) < 8:
            v.append(f"P1 动作收尾仅 {r.get('动作收尾占比')}%（建议 ≥10%）")

    # lieflat AI 指纹
    lf = r.get("AI指纹") or {}
    for k, n in lf.items():
        if k == "冒号-空转句引列表" and n >= 2:
            v.append(f"P0 AI指纹·冒号空转句引列表 {n} 处（R=9.4，最强）")
        elif k == "拟人化褒义喻体" and n >= 1:
            v.append(f"P1 AI指纹·拟人化褒义喻体 {n} 处（R=7.3）")
        elif k == "对举结构(不是A而是B)" and n >= 3:
            v.append(f"P1 AI指纹·对举结构 {n} 处（R=3.4，上限 2）")
        elif k == "冒号-提示语引出内容" and n >= 2:
            v.append(f"P1 AI指纹·冒号提示语 {n} 处（R=3.8）")
        elif k == "起首语(说白了)" and n >= 2:
            v.append(f"P1 AI指纹·起首语 {n} 处（R=3.2）")
        elif k == "序数词当小标题" and n >= 2:
            v.append(f"P1 AI指纹·序数词当小标题 {n} 处（R=3.1）")
        elif k == "相邻句结构同构" and n >= 4:
            v.append(f"P1 AI指纹·相邻句结构同构 {n} 处（R=2.0）")
        elif k.startswith("译文-") and n >= 2:
            v.append(f"P1 AI指纹·{k} {n} 处（R=2.6-5.3）")

    if r.get("章首模板"):
        t = r["章首模板"]
        if t == "醒来开头":
            v.append(f"P1 章首模板：{t}（最典型的 AI 开场，近 10 章 ≥4 即 FAIL）")
        else:
            v.append(f"P1 章首模板：{t}（注意不要连续多章同模板）")

    if r.get("万能生理反应", 0) > 2:
        v.append(f"P1 万能生理反应 {r['万能生理反应']} 处")
    if r.get("叙事过滤词", 0) > 3:
        v.append(f"P1 叙事过滤词 {r['叙事过滤词']} 处")
    if r.get("段尾抽象总结", 0) > 0:
        v.append(f"P1 段尾抽象总结 {r['段尾抽象总结']} 处")
    if r.get("套话禁词", 0) > 5:
        v.append(f"P1 AI 套话 {r['套话禁词']} 处")
    return v or ["通过：无显著 AI 信号"]


def fmt(r: dict) -> str:
    if not r.get("字数"):
        return f"[{r.get('label','')}] （空）"
    L = [f"═══ {r['label']}  {r['字数']} 字 ═══"]
    L.append("【lieflat 实证指标】")
    for k in ("问号密度", "句长CV", "字种密度", "数字密度", "时间锚点密度",
              "拟人化喻体", "零回指命中", "千字感叹号"):
        L.append(f"   {k:<12} {r[k]}")
    L.append("【lieflat 11 项 AI 指纹】")
    lf = r.get("AI指纹") or {}
    if lf:
        for k, n in sorted(lf.items(), key=lambda x: -x[1]):
            L.append(f"   {k:<26} {n}")
    else:
        L.append(f"   （无命中）")
    L.append(f"   {'章首入场模板':<24} {r.get('章首模板') or '（未识别）'}")
    L.append("【传统套话】")
    for k in ("套话禁词", "叙事过滤词", "万能生理反应", "段尾抽象总结"):
        L.append(f"   {k:<12} {r[k]}")
    if r["套话明细"]:
        top = sorted(r["套话明细"].items(), key=lambda x: -x[1])[:8]
        L.append(f"   {'明细':<12} " + ", ".join(f"{w}×{c}" for w, c in top))
    L.append("【段长阶梯】")
    for k in ("段落数", "段落均长", "短段占比(<30字)", "中段占比(30-80字)",
              "长段数(>80字)", "超长段数(>120字)", "单句成段占比",
              "最长连续单句段", "动作收尾占比"):
        if k in r:
            L.append(f"   {k:<20} {r[k]}")
    L.append(f"【判定】{len(r['判定'])} 项")
    for x in r["判定"]:
        L.append(f"   ⚠ {x}" if x != "通过：无显著 AI 信号" else f"   ✔ {x}")
    return "\n".join(L)


# ── 项目读取 ──────────────────────────────────────────────────────
def load_desk(pid: str = "20261014-c6c432") -> dict | None:
    p = BACKEND / "data" / "projects" / pid / "desk.json"
    if not p.exists():
        cands = sorted((BACKEND / "data" / "projects").glob("*/desk.json"))
        if not cands:
            return None
        p = cands[-1]
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def audit_project(pid: str | None = None, only_ch: int | None = None,
                  stages=("draft", "polish")) -> None:
    d = load_desk(pid or "")
    if not d:
        print("找不到 desk.json")
        return
    chs = d.get("chapters", {})
    for no in sorted(chs, key=lambda x: int(x)):
        if only_ch and int(no) != only_ch:
            continue
        c = chs[no]
        got = False
        for st in stages:
            v = c.get(st) or {}
            t = v.get("text", "") if isinstance(v, dict) else ""
            if t.strip():
                print(fmt(audit(t, f"ch{no}《{c.get('title','')}》 {st}")))
                print()
                got = True
        if not got and only_ch:
            print(f"ch{no}《{c.get('title','')}》：draft/polish 均为空（未生成）")
            print()


def ab_compare(ch: int = 2, pid: str | None = None) -> None:
    """A/B 对比：润色是否磨掉了 draft 的活人节奏。"""
    d = load_desk(pid or "")
    if not d:
        print("找不到 desk.json")
        return
    c = d["chapters"][str(ch)]
    dt = (c.get("draft") or {}).get("text", "")
    pt = (c.get("polish") or {}).get("text", "")
    if not dt or not pt:
        print(f"ch{ch} 缺 draft 或 polish，无法对比")
        return
    rd, rp = _rhythm_metrics(dt), _rhythm_metrics(pt)
    keys = ["段落数", "短段占比(<30字)", "中段占比(30-80字)", "长段数(>80字)",
            "超长段数(>120字)", "单句成段占比", "最长连续单句段", "动作收尾占比"]
    print(f"═══ ch{ch} 润色 A/B 对比（draft → polish）═══")
    print(f"{'指标':<22}{'draft':>10}{'polish':>10}{'变化':>12}  结论")
    verdicts = []
    for k in keys:
        a, b = rd.get(k, 0), rp.get(k, 0)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            delta = (b - a) / a * 100 if a else (100 if b else 0)
            bad = ""
            if k == "长段数(>80字)":
                bad = "⚠ 长段被削碎" if delta < -20 else ("↑ 好" if delta > 0 else "")
            elif k == "超长段数(>120字)":
                bad = "⚠ 超长段丢失" if b == 0 and a > 0 else ""
            elif k == "单句成段占比":
                bad = "⚠ 全是单句段" if b > 50 else ("⚠ 上升" if delta > 20 else "")
            elif k == "最长连续单句段":
                bad = "⚠ 瀑布体" if b >= 6 else ("⚠ 上升" if delta > 30 else "")
            elif k == "短段占比(<30字)":
                bad = "⚠ 变碎" if delta > 25 else ""
            elif k == "动作收尾占比":
                bad = "⚠ 段尾变抽象" if delta < -30 else ("↑ 好" if delta > 0 else "")
            elif k == "段落数":
                bad = "⚠ 合并过多" if delta < -20 else ""
            verdicts.append((k, bad))
            print(f"{k:<22}{a:>10}{b:>10}{delta:>+11.1f}%  {bad}")
    print()
    worse = [v for v in verdicts if "⚠" in v[1]]
    if worse:
        print("诊断：润色破坏了段长阶梯 / 动作落点 → 新版 polish prompt 的【节奏保护纪律】正是为此设计")
        print("建议：用新版 polish 重跑，或对该章执行 refine 补回长段与动作收尾")
    else:
        print("诊断：润色保留了段长阶梯 ✔")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ch", type=int, help="只审计某一章")
    ap.add_argument("--file", help="审计任意正文文件")
    ap.add_argument("--ab", type=int, nargs="?", const=2, help="润色 A/B 对比")
    ap.add_argument("--pid", help="项目 ID")
    a = ap.parse_args()
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            print(fmt(audit(f.read(), a.file)))
    elif a.ab:
        ab_compare(a.ab, a.pid)
    else:
        audit_project(a.pid, a.ch)
