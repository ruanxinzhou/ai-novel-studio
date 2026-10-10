#!/usr/bin/env python3
"""拆书 CLI：一本 txt → 节奏决策表（report.md）+ 全书脉络（arc.md）+ 底料（chapters.jsonl）。

独立工具，不依赖主系统后端。双层管线：
- 统计层（零 LLM，秒出）：章长分布 / 卷结构 / 字数节奏
- 语义层（LLM 逐章标签，默认本地 trae-exec 零成本）：概要 / 断章钩子 / 爽点 / 伏笔 / 升级

调度四样封顶：一个命令、--workers 并发、chaishu.log 日志、重跑续传（labels.jsonl 收据幂等）。
试点：--limit 20 分层抽样（前 50% / 中 25% / 后 25%），先验成色再全量。

用法：
  python chaishu.py 书.txt                     # 全量拆
  python chaishu.py 书.txt --limit 20          # 抽 20 章试点
  python chaishu.py 书.txt --skip-label        # 只出统计层（不调 LLM）
  python chaishu.py 书.txt --workers 4         # 实测稳了再加并发
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess  # noqa: F401  (engine 内部使用)
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from engine import trae_exec  # noqa: E402

# ---------------------------------------------------------------- 切章（复用主系统 importer/slicing 逻辑）

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CHAPTER_HEAD = re.compile(r"^\s*第\s*([0-9零一二三四五六七八九十百千万两]+)\s*章\s*([^\n]*)", re.M)
_VOLUME_HEAD = re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百千]+\s*卷\s*([^\n]*)", re.M)

def cn_num_to_int(s: str) -> int:
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, current = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            current = current * 10 + _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                total = (total + current) * unit
            else:
                total += (current or 1) * unit
            current = 0
        else:
            raise ValueError(f"非法中文数字：{ch}")
    result = total + current
    if result <= 0:
        raise ValueError(f"非法中文数字：{s}")
    return result


def read_book(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"无法识别文件编码（试过 utf-8/gb18030）：{path}")


def split_book(text: str) -> list[dict]:
    """切章 + 卷归属。卷头行不黏在上一章尾部，挪到本卷第一章开头。"""
    heads = list(_CHAPTER_HEAD.finditer(text))
    if not heads:
        body = text.strip()
        return [{"no": 0, "title": "", "volume": "正文", "body": body, "chars": len(body)}] if body else []

    out: list[dict] = []
    preamble = text[: heads[0].start()].strip()
    if preamble:
        out.append({"no": 0, "title": "前言", "volume": "正文", "body": preamble, "chars": len(preamble)})
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.start():end].strip()
        if not body:
            continue
        out.append({
            "no": int(m.group(1)) if m.group(1).isdigit() else cn_num_to_int(m.group(1)),
            "title": m.group(2).strip()[:60],
            "volume": "正文",
            "body": body,
            "chars": len(body),
        })

    # 卷头挪移：章尾的卷头行（其后无正文）归入下一章开头
    for i in range(len(out) - 1):
        cur, nxt = out[i], out[i + 1]
        vm = _VOLUME_HEAD.search(cur["body"][-300:])
        if not vm:
            continue
        line_start = cur["body"].rfind("\n", 0, vm.start()) + 1
        tail = cur["body"][line_start:]
        if tail.strip() and tail.strip() == vm.group(0).strip():
            cur["body"] = cur["body"][:line_start].rstrip()
            cur["chars"] = len(cur["body"])
            nxt["body"] = f"{tail.strip()}\n{nxt['body']}"
            nxt["chars"] = len(nxt["body"])

    # 卷名识别：章开头附近的卷头行；后续无卷头章继承上一卷名
    current_vol = "正文"
    for ch in out:
        vm = _VOLUME_HEAD.search(ch["body"][:300])
        if vm:
            current_vol = ch["body"][: vm.end()].splitlines()[-1].strip()
        ch["volume"] = current_vol
    return out


# ---------------------------------------------------------------- 统计层（零 LLM）

def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return s[k]


def volume_rows(chapters: list[dict]) -> list[dict]:
    """按卷聚合：起止章 / 卷字数 / 卷内均章字数。"""
    rows: list[dict] = []
    for ch in chapters:
        if not rows or rows[-1]["volume"] != ch["volume"]:
            rows.append({"volume": ch["volume"], "from": ch["no"], "to": ch["no"],
                         "chars": ch["chars"], "n": 1})
        else:
            r = rows[-1]
            r["to"] = ch["no"]
            r["chars"] += ch["chars"]
            r["n"] += 1
    for r in rows:
        r["avg"] = r["chars"] // max(1, r["n"])
    return rows


def stratified_sample(total: int, limit: int) -> list[int]:
    """分层抽样：前半 50% / 中四分之一 25% / 后四分之一 25%，各段内均匀取；
    段边界取整导致不足额时从剩余章均匀补齐。返回下标列表（有序、恰好 limit 个）。"""
    if limit >= total:
        return list(range(total))
    limit = max(1, limit)
    n1 = max(1, round(limit * 0.5))
    n3 = max(1, limit // 4)
    n2 = max(0, limit - n1 - n3)

    def even(seg_start: int, seg_end: int, n: int) -> list[int]:
        if n <= 0 or seg_end <= seg_start:
            return []
        span = seg_end - seg_start
        if n >= span:
            return list(range(seg_start, seg_end))
        return [seg_start + i * span // n for i in range(n)]

    half, three_q = total // 2, total * 3 // 4
    idx = set(even(0, half, n1))
    idx |= set(even(half, three_q, n2))
    idx |= set(even(three_q, total, n3))
    if len(idx) < limit:
        rest = [i for i in range(total) if i not in idx]
        need = limit - len(idx)
        step = max(1, len(rest) // need)
        idx |= set(rest[::step][:need])
    return sorted(idx)[:limit]


# ---------------------------------------------------------------- 语义层（LLM 逐章标签）

LABEL_PROMPT = """你是网文拆书助手。阅读下面这一章正文，只输出一个 JSON object（不要 markdown 代码块，不要任何解释文字）：
{{"summary": "本章一句话概要，≤30字，含主语和结果",
 "hook": <true 或 false>,
 "power_up": <true 或 false>,
 "events": [{{"type": "<shuangdian|foreshadow|upgrade|other>", "note": "≤10字"}}]}}

字段判定：
- hook：章末是否有悬念/危机/强期待（断章钩子）
- power_up：主角本章是否获得实力/地位/关键道具的实质提升
- events：本章关键事件 1-4 个。shuangdian=爽点（打脸/扬眉吐气/获得认可/大胜）；foreshadow=伏笔（新悬念/新人物/新物件/未解之事）；upgrade=升级（境界/技能/装备/地位跃迁）；other=其他关键转折

【第{no}章 {title}】
{body}"""


def _strip_fence(text: str) -> str:
    """剥离 ```json 围栏（MiniMax/GLM 输出常见，实测教训）。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def _parse_label(raw: str) -> dict:
    """解析标签输出。三级容错：完整 JSON → 剥围栏/截取花括号 → 字段级正则提取。"""
    t = _strip_fence(raw)
    obj: dict | None = None
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            cand = json.loads(m.group(0))
            if isinstance(cand, dict) and "summary" in cand:
                obj = cand
        except json.JSONDecodeError:
            pass
    if obj is None:
        # 截断/杂质兜底：字段级提取（截断在任何位置都能抢救已出现字段）
        sm = re.search(r'"summary"\s*:\s*"([^"]*)"', t)
        if not sm:
            raise ValueError(f"输出不含 summary 字段：{t[:80]!r}")
        hm = re.search(r'"hook"\s*:\s*(true|false)', t)
        pm = re.search(r'"power_up"\s*:\s*(true|false)', t)
        obj = {
            "summary": sm.group(1),
            "hook": bool(hm and hm.group(1) == "true"),
            "power_up": bool(pm and pm.group(1) == "true"),
            "events": [{"type": ty, "note": nt}
                       for ty, nt in re.findall(
                           r'"type"\s*:\s*"(\w+)"\s*,\s*"note"\s*:\s*"([^"]*)"', t)],
        }
    obj["hook"] = bool(obj.get("hook"))
    obj["power_up"] = bool(obj.get("power_up"))
    ev = obj.get("events") or []
    obj["events"] = [
        {"type": str(e.get("type", "other")), "note": str(e.get("note", ""))[:20]}
        for e in ev if isinstance(e, dict)
    ][:6]
    return obj


def label_chapter(ch: dict, model: str, timeout: int) -> dict:
    body = ch["body"][:12000]  # 超长章截断，控制单次输入
    prompt = LABEL_PROMPT.format(no=ch["no"], title=ch["title"], body=body)
    raw = trae_exec(prompt, model=model, timeout_sec=timeout)
    obj = _parse_label(raw)
    return {"no": ch["no"], "model": model,
            "ts": datetime.now().isoformat(timespec="seconds"), **obj}


# ---------------------------------------------------------------- 收据（幂等续跑）

def load_receipts(labels_file: Path) -> dict[int, dict]:
    done: dict[int, dict] = {}
    if not labels_file.exists():
        return done
    for line in labels_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            done[int(obj["no"])] = obj
        except (json.JSONDecodeError, KeyError, ValueError):
            continue  # 崩溃残行：忽略，重跑自动补
    return done


def append_receipt(labels_file: Path, obj: dict) -> None:
    with labels_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()


# ---------------------------------------------------------------- 主流程

def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="拆书：txt → 节奏决策表 + 全书脉络 + 底料")
    ap.add_argument("book", help="小说 txt 文件路径")
    ap.add_argument("--out", help="输出目录（默认：书旁的 拆书-书名/）")
    ap.add_argument("--workers", type=int, default=2, help="并发数（默认 2，实测稳了再加）")
    ap.add_argument("--model", default="GLM-5.3", help="trae-exec 模型")
    ap.add_argument("--timeout", type=int, default=300, help="单章调用超时秒数")
    ap.add_argument("--limit", type=int, default=0, help="试点：分层抽样 N 章（0=全量）")
    ap.add_argument("--skip-label", action="store_true", help="只出统计层与底料，不调 LLM")
    ap.add_argument("--skip-diagnose", action="store_true", help="跳过第二次 LLM 总评（report 第 10 行）")
    args = ap.parse_args()

    book = Path(args.book)
    if not book.exists():
        raise SystemExit(f"文件不存在：{book}")
    out_dir = Path(args.out) if args.out else book.parent / f"拆书-{book.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "chaishu.log"

    def log(msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"开始拆书：{book.name}")

    # 1. 切章 + 底料落盘
    chapters = split_book(read_book(book))
    if not chapters:
        raise SystemExit("切章结果为空：文件无内容")
    total_chars = sum(c["chars"] for c in chapters)
    chapters_file = out_dir / "chapters.jsonl"
    with chapters_file.open("w", encoding="utf-8") as f:
        for ch in chapters:
            f.write(json.dumps({k: ch[k] for k in ("no", "title", "volume", "chars")},
                               ensure_ascii=False) + "\n")
    log(f"切章完成：{len(chapters)} 章 / {total_chars} 字 → {chapters_file.name}")

    # 2. 统计层（秒出，先落一份无标签报告）
    labels_file = out_dir / "labels.jsonl"
    render = _import_render()
    base_meta = {"book": book.stem, "chapters": chapters, "labels": {},
                 "sampled": None, "skip_diagnose": True}
    render.render_all(out_dir, **base_meta)
    log(f"统计层完成：report.md / arc.md（无标签版）已生成")
    if args.skip_label:
        log(f"--skip-label：跳过语义层，收工。底料 {chapters_file.name}，报告 report.md")
        return

    # 3. 试点抽样
    targets = chapters
    sampled_idx: list[int] | None = None
    if args.limit and args.limit < len(chapters):
        sampled_idx = stratified_sample(len(chapters), args.limit)
        targets = [chapters[i] for i in sampled_idx]
        log(f"试点模式：从 {len(chapters)} 章分层抽样 {len(targets)} 章（前50%/中25%/后25%）")

    # 4. 语义层：并发打标签，收据续跑
    done = load_receipts(labels_file)
    todo = [ch for ch in targets if ch["no"] not in done]
    log(f"语义层：{len(targets)} 章中待打标签 {len(todo)} 章（已有收据 {len(targets) - len(todo)}，跳过）")
    lock = threading.Lock()
    ok, fail = 0, 0
    t0 = time.time()
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(label_chapter, ch, args.model, args.timeout): ch for ch in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                ch = futures[fut]
                try:
                    obj = fut.result()
                    with lock:
                        append_receipt(labels_file, obj)
                        done[obj["no"]] = obj
                        ok += 1
                    log(f"第{ch['no']}章 完成（{i}/{len(todo)}，累计 {time.time()-t0:.0f}s）")
                except Exception as e:
                    with lock:
                        fail += 1
                    log(f"第{ch['no']}章 失败：{e}")
    log(f"语义层结束：成功 {ok}，失败 {fail}（失败章重跑本命令即自动补）")

    # 5. 渲染最终报告（含标签 + 诊断）
    labels = {no: obj for no, obj in done.items()}
    render.render_all(out_dir, book=book.stem, chapters=chapters, labels=labels,
                      sampled=sampled_idx, skip_diagnose=args.skip_diagnose)
    log("收工。产出：report.md（节奏决策表）/ arc.md（全书脉络）/ chapters.jsonl + labels.jsonl（底料）")


def _import_render():
    import render
    return render


if __name__ == "__main__":
    main()
