"""截断 JSON 的词法证据修复（M1-T8，机制源自 Vela structured-syntax-repair）。

铁律：只允许**追加闭合符**——已有字符一个不动（标量序列与容器形状不变），
防止"修复"悄悄篡改模型产出。修不了的返回 None，交给上层整体重生成。
"""
from __future__ import annotations

import json
import re

_CLOSERS = {"{": "}", "[": "]"}
_FENCE_FULL = re.compile(r"^\s*```[^\n`]*\n(.*?)```\s*$", re.DOTALL)
_FENCE_OPEN = re.compile(r"^\s*```[^\n`]*\n", re.DOTALL)


def strip_code_fence(raw: str) -> str:
    """剥掉模型常见的 ```json ... ``` 围栏（MiniMax 等结构化输出习惯）；
    截断输出只有开头围栏时，剥掉围栏头让截断修复接管。"""
    m = _FENCE_FULL.match(raw)
    if m:
        return m.group(1)
    return _FENCE_OPEN.sub("", raw, count=1)


def repair_truncated_json(raw: str) -> str | None:
    """尝试为截断的 JSON 补齐闭合符，返回保证可解析的结果；失败返回 None。

    允许的动作仅有两种：
    1. 追加闭合符（引号/括号）——已有字符一个不动；
    2. 丢弃紧邻末尾的悬空逗号（逗号不承载标量信息，词法证据不受损）。
    修复结果必须通过 json.loads 校验，否则视为不可修复。
    """
    stack: list[str] = []
    in_string = False
    escaped = False

    for ch in raw:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in _CLOSERS:
                stack.append(ch)
            elif ch in "}]":
                if not stack or _CLOSERS[stack.pop()] != ch:
                    return None

    candidate = raw
    if not in_string:
        stripped = candidate.rstrip()
        if stripped.endswith(","):
            candidate = stripped[:-1]

    suffix = ""
    if in_string:
        # 悬空转义符（尾部奇数个反斜杠）：补引号只会产出 \" 仍在串内，无法闭合
        if escaped:
            return None
        suffix += '"'
    while stack:
        suffix += _CLOSERS[stack.pop()]

    repaired = candidate + suffix
    try:
        json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return repaired


def parse_repaired_json(raw: str) -> object | None:
    """修复 + 解析；任何一步失败返回 None（fail-loud 交给调用方决定重生成）。"""
    raw = strip_code_fence(raw)
    repaired = repair_truncated_json(raw)
    if repaired is None:
        return None
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None
