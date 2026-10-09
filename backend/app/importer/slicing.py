"""导入切分（M4）：把整本/批量正文按「第N章」切成章。

兼容中文数字（第一章/第一百二十三章/第一万零一章）与阿拉伯数字（第12章），
标题行允许章号前后空格并保留章名。无任何章节标签的文本整体算一份（章号 0，
作为「全文」处理，仍可拆解，只是没有章粒度）。
"""
from __future__ import annotations

import re

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}

_CHAPTER_HEAD = re.compile(r"^\s*第\s*([0-9零一二三四五六七八九十百千万两]+)\s*章\s*([^\n]*)", re.M)


def cn_num_to_int(s: str) -> int:
    """中文数字 → int；纯阿拉伯数字直接 int()。不支持小数与负数。"""
    s = s.strip()
    if not s:
        raise ValueError("空数字")
    if s.isdigit():
        return int(s)
    total, current, saw_digit = 0, 0, False
    for ch in s:
        if ch in _CN_DIGITS:
            current, saw_digit = current * 10 + _CN_DIGITS[ch], True
            if current == 0:
                current = 0
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                total = (total + current) * unit
            else:
                total += (current or 1) * unit
            current, saw_digit = 0, True
        else:
            raise ValueError(f"非法中文数字：{ch}")
    result = total + current
    if not saw_digit or result <= 0:
        raise ValueError(f"非法中文数字：{s}")
    return result


def _norm_num(raw: str) -> int:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return cn_num_to_int(raw)


def split_import(text: str) -> list[dict]:
    """切分为 [{no, title, body, chars}]；无标签整体为 no=0 一份。"""
    heads = list(_CHAPTER_HEAD.finditer(text))
    if not heads:
        body = text.strip()
        return ([{"no": 0, "title": "", "body": body, "chars": len(body)}]
                if body else [])

    out: list[dict] = []
    preamble = text[: heads[0].start()].strip()
    if preamble:
        out.append({"no": 0, "title": "前言", "body": preamble, "chars": len(preamble)})
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.start():end].strip()
        if not body:
            continue
        title = m.group(2).strip()
        out.append({
            "no": _norm_num(m.group(1)),
            "title": title[:60],
            "body": body,
            "chars": len(body),
        })
    return out
