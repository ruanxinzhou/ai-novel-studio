# 后续章骨架重排 + 知情表（角色层 + 读者层）+ 分档字数与场数 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在工作台加两个功能——每章可折叠的「原剧本·本章视角」信息窗（原剧本剧情 / 原剧情发展方向 / 角色与读者知情表），以及卷级的「重排后续骨架」按钮（按 day 分段重算、逐章 diff 确认、同步实体库），同时把每章字数与场数从一律 4500 改成按 beat_type 分档。

**Architecture:** 新建 `app/knowledge/` 包承载按章知情表（角色层 + 读者层），与实体库的全局 `state_attrs` 并存但优先。骨架重排走「模型生成 diff → 作者逐章确认 → 写卷级骨架 + 重建章级方向卡 → 实体库软标记对账」四步，全部写操作前先快照。字数分档落在 `chapter_plan[*].words`，由 `beat_type` 决定区间，`quality.py` 的门禁改为按档取上下限。

**Tech Stack:** Python 3.10 / FastAPI / Pydantic / pytest / Vue 3 `<script setup>` / Element Plus / TypeScript

## Global Constraints

- 运行环境：`cd backend` 后用 `python -m pytest tests/xxx.py -v`；前端类型检查 `cd frontend && npx vue-tsc --noEmit`
- 后端端口 8321，前端端口 5173；项目 ID `20261014-c6c432`
- **禁止硬删实体**：骨架改写后只做软标记（`state_attrs["plan_removed_at"]`），伏笔状态绝不回退
- **已定稿章（`finalized_at` 非空）永不参与骨架重算**，只作为上下文喂给模型
- **作者手写章（`brief.is_custom` 为真）默认不覆盖**，在 diff 面板单独标记，可手动勾选
- 知情表的 `knows` / `blinded_to` 必须存**关键词**（长度 2-12 字，可被正文命中），完整描述另存 `knows_detail` / `blinded_detail`
- 字数分档表（唯一真值，代码与 prompt 都以此为准）：

  | beat_type | 场数 | 字数区间 | 门禁下限 | 门禁上限 |
  |---|---|---|---|---|
  | hook / sell | 2-3 | 3000-3500 | 2400 | 3900 |
  | engine | 2-3 | 3000-3800 | 2400 | 4200 |
  | climax | 3-4 | 3500-4500 | 2800 | 5000 |
  | cooldown | 2 | 2500-3000 | 2000 | 3400 |

- 新增/修改的 Python 源码里字符串用 `\n` 转义写换行，**不要在字符串字面量里插真实换行**（本项目已因此踩过两次 SyntaxError）
- 每个 Task 结束必须提交；本环境 `git` 不可用，提交步骤由执行者在有 git 的环境完成
- Python 字符串拼接里含中文引号时注意编码；测试文件统一 `from __future__ import annotations`

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `backend/app/knowledge/__init__.py` | 导出 `KnowledgeStore` / `knowledge_for` |
| `backend/app/knowledge/store.py` | 按章知情表的读写、按章查询、与 `state_attrs` 的合并回落 |
| `backend/app/knowledge/gen.py` | 知情表自动推导（模型 prompt + 结果规范化） |
| `backend/app/knowledge/beat.py` | 字数/场数分档表（唯一真值）、`beat_bounds()` |
| `backend/app/knowledge/replan.py` | 骨架重排的模型调用 + diff 计算 + 快照/回滚 |
| `backend/app/api/knowledge_routes.py` | 知情表三端点（draft / apply / get） |
| `backend/app/entities/vault_plan.py` | 新增 `reconcile_with_plan()` 实体库软标记对账 |
| `backend/app/api/brief_routes.py` | 新增 `replan` / `replan/apply` / `replan/restore` 三端点 |
| `backend/app/api/main.py` | 新增 `GET /desk/{chapter}/origin-context` |
| `backend/app/context/ledger.py` | `_knowledge_seg()` 改为优先读按章表 |
| `backend/app/review/book_checks.py` | 知情边界检查按章取 |
| `backend/app/generation/quality.py` | 字数门禁按档；plot 层场数上限 P1 |
| `backend/app/pipeline/engine.py` | 章级 `words` 覆盖 stage 默认 `target_chars` |
| `frontend/src/views/DeskView.vue` | 折叠信息窗第三项 |
| `frontend/src/views/WritingDeskView.vue` | 「重排后续骨架」按钮 + diff 弹窗 |
| `frontend/src/api.ts` | 新端点 TS 封装 |

任务顺序：**Task 1-3（地基：分档表 + 知情表存储 + 实体对账）→ Task 4-6（知情推导 + 两个端点 + 接线）→ Task 7-8（骨架重排）→ Task 9-11（字数门禁 + 前端两处）→ Task 12（端到端验证）**。

---

### Task 1: 字数/场数分档表（beat.py）

**Files:**
- Create: `backend/app/knowledge/__init__.py`
- Create: `backend/app/knowledge/beat.py`
- Test: `backend/tests/test_beat_bounds.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `BEAT_BOUNDS: dict[str, dict]` —— 键为 beat_type，值含 `scenes_min` / `scenes_max` / `words_min` / `words_max` / `gate_lo` / `gate_hi`
  - `beat_bounds(beat_type: str) -> dict` —— 未知 beat_type 回退到 engine 档
  - `chapter_words_target(beat_type: str) -> int` —— 该档的目标字数（区间中值）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_beat_bounds.py`：

```python
"""字数/场数分档表测试：分档是全局唯一真值，门禁与 prompt 都读它。"""
from __future__ import annotations

from app.knowledge.beat import (
    BEAT_BOUNDS,
    beat_bounds,
    chapter_words_target,
)


class TestBeatBounds:
    def test_four_beat_types_present(self):
        assert set(BEAT_BOUNDS) == {"hook", "sell", "engine", "climax", "cooldown"}

    def test_climax_keeps_high_ceiling(self):
        b = beat_bounds("climax")
        assert b["words_max"] == 4500
        assert b["gate_hi"] == 5000
        assert b["scenes_max"] == 4

    def test_hook_is_shortest(self):
        assert beat_bounds("hook")["words_max"] == 3500
        assert beat_bounds("hook")["scenes_max"] == 3

    def test_cooldown_is_shortest_band(self):
        assert beat_bounds("cooldown")["words_max"] == 3000
        assert beat_bounds("cooldown")["scenes_max"] == 2

    def test_unknown_beat_falls_back_to_engine(self):
        assert beat_bounds("nonsense") == beat_bounds("engine")
        assert beat_bounds("") == beat_bounds("engine")

    def test_gate_window_contains_word_band(self):
        """门禁区间必须包住字数区间，否则会出现「目标合法但门禁报错」。"""
        for name, b in BEAT_BOUNDS.items():
            assert b["gate_lo"] <= b["words_min"], name
            assert b["gate_hi"] >= b["words_max"], name

    def test_words_target_inside_band(self):
        for name, b in BEAT_BOUNDS.items():
            t = chapter_words_target(name)
            assert b["words_min"] <= t <= b["words_max"], name


class TestSceneLimits:
    def test_climax_allows_four_scenes(self):
        """climax 的 4 场合法——旧实现硬卡场数会误杀高潮章。"""
        assert beat_bounds("climax")["scenes_max"] >= 4

    def test_engine_caps_at_three(self):
        assert beat_bounds("engine")["scenes_max"] == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_beat_bounds.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.knowledge'`

- [ ] **Step 3: 写实现**

创建 `backend/app/knowledge/__init__.py`：

```python
"""知情表（按章）+ 字数场数分档。

模块职责边界：
- beat.py    字数/场数分档表（全局唯一真值，门禁与 prompt 都读它）
- store.py   按章知情表的读写
- gen.py     知情表自动推导
- replan.py  骨架重排
"""
from .beat import BEAT_BOUNDS, beat_bounds, chapter_words_target
from .store import KnowledgeStore, knowledge_for

__all__ = [
    "BEAT_BOUNDS",
    "beat_bounds",
    "chapter_words_target",
    "KnowledgeStore",
    "knowledge_for",
]
```

创建 `backend/app/knowledge/beat.py`：

```python
"""字数/场数分档表——全局唯一真值。

**为什么分档而不是一刀 3000 字**：实测定稿三章 6766/4994/6227 字，
一刀 3000 会让 climax 章（陆骁收网、红线结算）的高潮被削掉，
而且 81000 字的卷要拉成 27 章，七日倒计时的节奏全碎。

**为什么场数跟着 beat_type 走**：起承转合（qi/cheng/zhuan/he）是
**一个场景的内部结构**，不是一章的四个场景。hook 章 2 场就够，
climax 章 3-4 场才铺得开硬卡场数。

门禁区间（gate_lo/gate_hi）必须包住字数区间（words_min/words_max），
否则会出现「目标字数合法但门禁报错」的自相矛盾。
"""
from __future__ import annotations

#: beat_type → 场数区间 / 字数区间 / 门禁上下限
#: 字数区间是作者的目标；门禁区间是机器判定，留出容差。
BEAT_BOUNDS: dict[str, dict] = {
    "hook": {
        "scenes_min": 2, "scenes_max": 3,
        "words_min": 3000, "words_max": 3500,
        "gate_lo": 2400, "gate_hi": 3900,
    },
    "sell": {
        "scenes_min": 2, "scenes_max": 3,
        "words_min": 3000, "words_max": 3500,
        "gate_lo": 2400, "gate_hi": 3900,
    },
    "engine": {
        "scenes_min": 2, "scenes_max": 3,
        "words_min": 3000, "words_max": 3800,
        "gate_lo": 2400, "gate_hi": 4200,
    },
    "climax": {
        "scenes_min": 3, "scenes_max": 4,
        "words_min": 3500, "words_max": 4500,
        "gate_lo": 2800, "gate_hi": 5000,
    },
    "cooldown": {
        "scenes_min": 2, "scenes_max": 2,
        "words_min": 2500, "words_max": 3000,
        "gate_lo": 2000, "gate_hi": 3400,
    },
}

#: 未知 beat_type 的回退档
_FALLBACK = "engine"


def beat_bounds(beat_type: str) -> dict:
    """取某 beat_type 的分档；未知/空值回退到 engine 档。"""
    key = str(beat_type or "").strip().lower()
    return BEAT_BOUNDS.get(key, BEAT_BOUNDS[_FALLBACK])


def chapter_words_target(beat_type: str) -> int:
    """该档的目标字数（区间中值，四舍五入到百位）。"""
    b = beat_bounds(beat_type)
    mid = (b["words_min"] + b["words_max"]) / 2
    return int(round(mid / 100.0) * 100)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_beat_bounds.py -v`
Expected: PASS —— 7 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/knowledge/ backend/tests/test_beat_bounds.py
git commit -m "feat(knowledge): add beat-type word/scene bounds table"
```

---

### Task 2: 按章知情表存储（store.py）

**Files:**
- Create: `backend/app/knowledge/store.py`
- Test: `backend/tests/test_knowledge_store.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `KnowledgeStore(path: Path | str)` —— 构造即加载，`flush()` 落盘
  - `KnowledgeStore.get_chapter(chapter: int) -> dict` —— 返回 `{"day": int, "characters": {...}, "reader": {...}}`，不存在返回 `{}`
  - `KnowledgeStore.set_chapter(chapter: int, payload: dict) -> None`
  - `KnowledgeStore.all_chapters() -> dict[int, dict]`
  - `KnowledgeStore.character_entries(chapter: int) -> dict` —— 角色层条目
  - `KnowledgeStore.reader_entry(chapter: int) -> dict` —— 读者层条目
  - `knowledge_for(pid: str) -> KnowledgeStore` —— 按项目路径取实例（带缓存）
  - `merge_knowledge(entity, chapter: int, store) -> str` —— 返回注入用文本段；按章表优先，查不到退回 `entity.state_attrs`

**数据形状（固定，全项目唯一）：**

```json
{
  "chapters": {
    "7": {
      "day": 3,
      "characters": {
        "character-0-唐迟": {
          "knows": ["黑胆珠遇陶露会发热"],
          "blinded_to": ["献名仪式"],
          "knows_detail": ["唐迟已确认寄件人就是陆骁"],
          "blinded_detail": ["阮衡知道献名仪式的全部步骤"]
        }
      },
      "reader": {
        "known": ["寄件人就是陆骁"],
        "misbelieved": ["唐迟以为替帖能彻底断契约"],
        "unknown": ["陆骁已供第四次血"],
        "gaps": ["阮衡知道献名仪式的全部步骤"]
      }
    }
  }
}
```

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_knowledge_store.py`：

```python
"""按章知情表存储测试：按章隔离、关键词校验、与 state_attrs 的回落。"""
from __future__ import annotations

import json

import pytest

from app.knowledge.store import KnowledgeStore, merge_knowledge

#: 关键词长度上下限，与 review/book_checks._knowledge_keywords 一致
KW_MIN, KW_MAX = 2, 12


class TestChapterIsolation:
    def test_chapters_are_isolated(self, tmp_path):
        s = KnowledgeStore(tmp_path / "k.json")
        s.set_chapter(7, {"day": 3, "characters": {"c1": {"knows": ["甲"]}}})
        s.set_chapter(12, {"day": 6, "characters": {"c1": {"knows": ["乙"]}}})
        assert s.get_chapter(7)["characters"]["c1"]["knows"] == ["甲"]
        assert s.get_chapter(12)["characters"]["c1"]["knows"] == ["乙"]

    def test_missing_chapter_returns_empty(self, tmp_path):
        s = KnowledgeStore(tmp_path / "k.json")
        assert s.get_chapter(99) == {}

    def test_persists_to_disk(self, tmp_path):
        p = tmp_path / "k.json"
        s = KnowledgeStore(p)
        s.set_chapter(7, {"day": 3, "characters": {}})
        s.flush()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "7" in data["chapters"]

    def test_reload_keeps_data(self, tmp_path):
        p = tmp_path / "k.json"
        KnowledgeStore(p).set_chapter(7, {"day": 3, "characters": {}})
        s2 = KnowledgeStore(p)
        assert s2.get_chapter(7)["day"] == 3


class TestKeywordValidation:
    def test_rejects_overlong_keyword(self, tmp_path):
        """超过 12 字的条目没法撞正文——检查链路会空转，必须在存储层挡住。"""
        s = KnowledgeStore(tmp_path / "k.json")
        s.set_chapter(7, {"day": 3, "characters": {
            "c1": {"knows": ["这是一条远远超过十二个字的很长的信息描述"]}}})
        assert s.get_chapter(7)["characters"]["c1"]["knows"] == []

    def test_rejects_single_char_keyword(self, tmp_path):
        s = KnowledgeStore(tmp_path / "k.json")
        s.set_chapter(7, {"day": 3, "characters": {"c1": {"knows": ["甲"]}}})
        assert s.get_chapter(7)["characters"]["c1"]["knows"] == []

    def test_keeps_valid_keywords(self, tmp_path):
        s = KnowledgeStore(tmp_path / "k.json")
        s.set_chapter(7, {"day": 3, "characters": {"c1": {"knows": ["献名仪式"]}}})
        assert s.get_chapter(7)["characters"]["c1"]["knows"] == ["献名仪式"]

    def test_detail_fields_not_length_checked(self, tmp_path):
        """完整描述可以很长——它只给作者看，不参与撞正文。"""
        s = KnowledgeStore(tmp_path / "k.json")
        long_desc = "阮衡知道献名仪式的全部步骤以及第七次供血的日期和地点"
        s.set_chapter(7, {"day": 3, "characters": {"c1": {"blinded_detail": [long_desc]}}})
        got = s.get_chapter(7)["characters"]["c1"]["blinded_detail"]
        assert got == [long_desc]


class TestReaderEntry:
    def test_reader_four_fields(self, tmp_path):
        s = KnowledgeStore(tmp_path / "k.json")
        s.set_chapter(7, {"day": 3, "reader": {
            "known": ["a"], "misbelieved": ["b"], "unknown": ["c"], "gaps": ["d"]}})
        r = s.reader_entry(7)
        assert r["known"] == ["a"]
        assert r["misbelieved"] == ["b"]
        assert r["unknown"] == ["c"]
        assert r["gaps"] == ["d"]

    def test_reader_missing_returns_empty(self, tmp_path):
        s = KnowledgeStore(tmp_path / "k.json")
        assert s.reader_entry(7) == {}


class TestMergeKnowledge:
    def test_prefers_chapter_table(self, tmp_path):
        class E:
            id = "c1"
            name = "唐迟"
            state_attrs = {"knows": "旧值", "blinded_to": "旧盲区"}

        s = KnowledgeStore(tmp_path / "k.json")
        s.set_chapter(7, {"day": 3, "characters": {"c1": {
            "knows": ["新值"], "blinded_to": ["新盲区"]}}})
        seg = merge_knowledge(E(), 7, s)
        assert "新值" in seg and "新盲区" in seg
        assert "旧值" not in seg

    def test_falls_back_to_state_attrs(self, tmp_path):
        """按章表没有这个角色时退回实体库旧字段——旧项目不至于空白。"""
        class E:
            id = "c1"
            name = "唐迟"
            state_attrs = {"knows": "旧值", "blinded_to": "旧盲区"}

        s = KnowledgeStore(tmp_path / "k.json")
        seg = merge_knowledge(E(), 7, s)
        assert "旧值" in seg and "旧盲区" in seg

    def test_empty_yields_empty_segment(self, tmp_path):
        class E:
            id = "c1"
            name = "唐迟"
            state_attrs = {}

        s = KnowledgeStore(tmp_path / "k.json")
        assert merge_knowledge(E(), 7, s) == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_knowledge_store.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.knowledge.store'`

- [ ] **Step 3: 写实现**

创建 `backend/app/knowledge/store.py`：

```python
"""按章知情表：角色层 + 读者层。

## 为什么不用 entity.state_attrs["knows"]

`state_attrs["knows"]` 是**全局单值**。第 7 章知道的信息，到第 12 章
就该变——塞进全局字段必然互相覆盖。项目现有的记忆碎片机制
（entities/store.py 的 MemoryFragment）已经给出了正确答案：
**带闸门的内容单独存**。这里就是那个闸门：章号。

## 为什么 knows/blinded_to 存关键词，*_detail 存完整描述

`review/book_checks.py` 的 check_knowledge_boundary() 靠关键词撞正文：
关键词必须是 2-12 字、正文里真的可能出现的词。而作者在折叠窗里要看的
是「阮衡知道献名仪式的全部步骤」这样的完整句。两个用途不能共用一个
字段——共用则检查永远命中不了，不共用则检查无法工作。

## 读者层为什么不是平行的 knows/blinded_to

「读者知道、主角也知道」不是信息差，是复述。所以读者层记的是四类：
known（读者已确知）/ misbelieved（读者正相信但为假）/ unknown（悬念清单）
/ gaps（读者不知但某角色知）。gaps 与角色层的 blinded_to 是同一份事实
的两种视角：gaps 从读者看，blinded_to 从角色看。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("knowledge.store")

#: 关键词长度上下限，与 review/book_checks._knowledge_keywords 保持一致
KW_MIN = 2
KW_MAX = 12

#: 需要做长度校验的字段（只校验这两个；detail 字段不限长）
KEYWORD_FIELDS = ("knows", "blinded_to")

#: 读者层的四个字段
READER_FIELDS = ("known", "misbelieved", "unknown", "gaps")


def _clean_keywords(value: Any) -> list[str]:
    """把值洗成关键词列表；超长或过短的丢弃。

    丢弃而不是截断：截断出来的「阮衡知道献名仪」既撞不中正文，
    也不是一句能读的话，只会污染账本。
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        kw = str(item or "").strip()
        if not kw or not (KW_MIN <= len(kw) <= KW_MAX):
            continue
        if kw not in out:
            out.append(kw)
    return out


def _clean_detail(value: Any) -> list[str]:
    """完整描述不限长度，只做去空与去重。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        t = str(item or "").strip()
        if t and t not in out:
            out.append(t)
    return out


class KnowledgeStore:
    """按章知情表。构造时加载，flush() 落盘。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.chapters: dict[str, dict] = {}
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                chs = data.get("chapters")
                if isinstance(chs, dict):
                    self.chapters = {str(k): v for k, v in chs.items() if isinstance(v, dict)}
            except (json.JSONDecodeError, TypeError, OSError):
                self.chapters = {}

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"chapters": self.chapters},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---- 读 ----

    def get_chapter(self, chapter: int) -> dict:
        """取某章的完整条目；不存在返回 {}。"""
        return self.chapters.get(str(int(chapter)), {}) or {}

    def all_chapters(self) -> dict[int, dict]:
        """全部章条目，键为 int 章号。"""
        out: dict[int, dict] = {}
        for k, v in self.chapters.items():
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
        return out

    def character_entries(self, chapter: int) -> dict:
        """某章的角色层条目。"""
        ch = self.get_chapter(chapter)
        ents = ch.get("characters")
        return ents if isinstance(ents, dict) else {}

    def reader_entry(self, chapter: int) -> dict:
        """某章的读者层条目；缺失返回 {}。"""
        ch = self.get_chapter(chapter)
        rd = ch.get("reader")
        return rd if isinstance(rd, dict) else {}

    # ---- 写 ----

    def set_chapter(self, chapter: int, payload: dict) -> None:
        """写某章条目。关键词字段做长度校验，detail 字段不限长。"""
        if not isinstance(payload, dict):
            return
        day = payload.get("day")
        out: dict[str, Any] = {"characters": {}, "reader": {}}
        try:
            out["day"] = int(day) if day is not None else 0
        except (TypeError, ValueError):
            out["day"] = 0

        chars = payload.get("characters")
        if isinstance(chars, dict):
            for eid, entry in chars.items():
                if not isinstance(entry, dict):
                    continue
                rec: dict[str, list[str]] = {}
                for f in KEYWORD_FIELDS:
                    vals = _clean_keywords(entry.get(f))
                    if vals:
                        rec[f] = vals
                for f in ("knows_detail", "blinded_detail"):
                    vals = _clean_detail(entry.get(f))
                    if vals:
                        rec[f] = vals
                if rec:
                    out["characters"][str(eid)] = rec

        reader = payload.get("reader")
        if isinstance(reader, dict):
            rec_r: dict[str, list[str]] = {}
            for f in READER_FIELDS:
                vals = _clean_detail(reader.get(f))
                if vals:
                    rec_r[f] = vals
            out["reader"] = rec_r

        self.chapters[str(int(chapter))] = out


#: 项目 → KnowledgeStore 缓存（与 api/main.py 的 _project_entity_stores 同模式）
_stores: dict[str, KnowledgeStore] = {}


def knowledge_for(pid: str, root: Path | str | None = None) -> KnowledgeStore:
    """按项目取知情表实例；带缓存避免重复读盘。"""
    if pid in _stores:
        return _stores[pid]
    if root is None:
        from app import config as _config
        root = _config.REPO / "backend" / "data" / "projects" / pid
    p = Path(root) / "knowledge.json"
    s = KnowledgeStore(p)
    _stores[pid] = s
    return s


def merge_knowledge(entity, chapter: int, store: KnowledgeStore) -> str:
    """给角色实体生成知情边界注入段。

    **按章表优先，查不到退回 entity.state_attrs**——旧项目没有知情表时
    仍能工作（虽然字段多半是空的）。

    返回形如「（已知：甲；不知情：乙）」，无信息时返回空串。
    """
    entry = store.character_entries(chapter).get(getattr(entity, "id", ""), {})
    knows_list = list(entry.get("knows") or [])
    blind_list = list(entry.get("blinded_to") or [])
    knows_detail = list(entry.get("knows_detail") or [])
    blind_detail = list(entry.get("blinded_detail") or [])

    if not knows_list and not blind_list:
        attrs = getattr(entity, "state_attrs", None) or {}
        knows_list = _split_legacy(str(attrs.get("knows") or ""))
        blind_list = _split_legacy(str(attrs.get("blinded_to") or ""))

    parts: list[str] = []
    if knows_list or knows_detail:
        seg = "、".join(knows_detail) if knows_detail else "、".join(knows_list)
        parts.append(f"已知：{seg}")
    if blind_list or blind_detail:
        seg = "、".join(blind_detail) if blind_detail else "、".join(blind_list)
        parts.append(f"不知情：{seg}")
    if not parts:
        return ""
    return "（" + "；".join(parts) + "）"


def _split_legacy(raw: str) -> list[str]:
    """旧 state_attrs 是自由文本，按顿号/逗号/分号切词。"""
    import re
    if not raw:
        return []
    out: list[str] = []
    for piece in re.split(r"[、，,；;\n]+", raw):
        kw = piece.strip()
        if kw:
            out.append(kw)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_knowledge_store.py -v`
Expected: PASS —— 13 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/knowledge/store.py backend/tests/test_knowledge_store.py
git commit -m "feat(knowledge): add per-chapter knowledge store with keyword validation"
```

---

### Task 3: 实体库软标记对账（reconcile_with_plan）

**Files:**
- Modify: `backend/app/entities/vault_plan.py`（在文件末尾追加新函数）
- Test: `backend/tests/test_plan_reconcile.py`

**Interfaces:**
- Consumes: `EntityStore`（`app/entities/store.py`）；`chapter_plan`（list of dict，字段 `chapter` / `characters_on_stage` / `location` / `foreshadow_seeds` / `foreshadow_payback`）
- Produces:
  - `reconcile_with_plan(store: EntityStore, plan: list, *, start_chapter_no: int, changed_from: int) -> dict`
    返回 `{"created": [name], "removed": [name], "foreshadow_frozen": [name], "plotline_notes": {name: notes}, "skipped": [name]}`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_plan_reconcile.py`：

```python
"""骨架重排后的实体库对账：软标记，绝不硬删。"""
from __future__ import annotations

from app.entities import EntityStore
from app.entities.vault_plan import reconcile_with_plan


def _plan(chapter, chars=None, locs=None, seeds=None, payoff=None, plotlines=None):
    return [{
        "chapter": chapter,
        "characters_on_stage": chars or [],
        "location": locs or [],
        "foreshadow_seeds": seeds or [],
        "foreshadow_payback": payoff or [],
        "plotline_notes": plotlines or {},
    }]


class TestReconcileAdds:
    def test_new_character_is_created(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        rep = reconcile_with_plan(s, _plan(5, chars=["新角色"]), start_chapter_no=1, changed_from=5)
        assert s.find("character", "新角色") is not None
        assert "新角色" in rep["created"]

    def test_new_foreshadow_is_planted(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        reconcile_with_plan(s, _plan(5, seeds=["黑胆珠的来历"]), start_chapter_no=1, changed_from=5)
        f = s.find("foreshadow", "黑胆珠的来历")
        assert f is not None and f.status == "planted"


class TestReconcileSoftMarks:
    def test_removed_entity_is_marked_not_deleted(self, tmp_path):
        """实体库是资产——角色可能在新骨架外的其他卷还会出现，不能删。"""
        s = EntityStore(tmp_path / "e.json")
        s.add("character", "旧角色", summary="曾经的", chapter=4)
        rep = reconcile_with_plan(s, _plan(9, chars=["别的角色"]), start_chapter_no=1, changed_from=9)
        e = s.find("character", "旧角色")
        assert e is not None, "实体不能被硬删"
        assert e.state_attrs.get("plan_removed_at") == "9"
        assert "旧角色" in rep["removed"]

    def test_removed_entity_drops_appearances_in_range(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        s.add("character", "旧角色", chapter=4)
        s.find("character", "旧角色").appearances = [4, 9]
        reconcile_with_plan(s, _plan(9, chars=["别的"]), start_chapter_no=1, changed_from=9)
        assert 9 not in s.find("character", "旧角色").appearances
        assert 4 in s.find("character", "旧角色").appearances

    def test_foreshadow_status_never_reverts(self, tmp_path):
        """已埋的伏笔不能当没埋过——硬退会让已写章节正文与账本对不上。"""
        s = EntityStore(tmp_path / "e.json")
        s.add("foreshadow", "门后的声音", summary="", chapter=3)
        reconcile_with_plan(s, _plan(9, seeds=[]), start_chapter_no=1, changed_from=9)
        f = s.find("foreshadow", "门后的声音")
        assert f.status == "planted"
        assert f.state_attrs.get("plan_no_more") == "9"

    def test_plotline_notes_written_to_state_attrs(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        s.add("plotline", "借运帖", summary="线索")
        reconcile_with_plan(s, _plan(9, plotlines={"借运帖": "本章推进到验尸环节"}),
                           start_chapter_no=1, changed_from=9)
        assert s.find("plotline", "借运帖").state_attrs.get("plan_notes_ch9") == "本章推进到验尸环节"


class TestReconcileSafety:
    def test_finalized_chapter_entities_not_marked_removed(self, tmp_path):
        """已定稿章出场过的实体不能被标记移除——那章正文还在。"""
        s = EntityStore(tmp_path / "e.json")
        s.add("character", "已定稿角色", chapter=2)
        reconcile_with_plan(s, _plan(9, chars=["别的"]), start_chapter_no=1, changed_from=9)
        e = s.find("character", "已定稿角色")
        assert "plan_removed_at" not in e.state_attrs

    def test_empty_plan_is_noop(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        s.add("character", "甲", chapter=1)
        rep = reconcile_with_plan(s, [], start_chapter_no=1, changed_from=5)
        assert rep["removed"] == []
        assert "plan_removed_at" not in s.find("character", "甲").state_attrs
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_plan_reconcile.py -v`
Expected: FAIL —— `ImportError: cannot import name 'reconcile_with_plan'`

- [ ] **Step 3: 写实现**

在 `backend/app/entities/vault_plan.py` 末尾追加：

```python
# ============================================================
# 骨架重排后的实体库对账
# ============================================================
#
# **为什么是软标记而不是删除**：实体库是跨卷资产。某角色在这个卷的新骨架里
# 不出现了，不代表他在别的卷也不出现；硬删会让已写章节的正文和账本对不上。
#
# **伏笔为什么绝不回退 planted**：已埋的伏笔就是已埋的——它已经在正文里
# 出现过，账本要记着。只标记「将来不再推进」。
#
# **已定稿章出场过的实体永不标记移除**：那章正文已经定稿，实体还在正文里。

def reconcile_with_plan(store: EntityStore, plan: list, *,
                        start_chapter_no: int, changed_from: int) -> dict:
    """按新骨架对账实体库。

    plan：新的 chapter_plan 列表（字段同 rhythm_blueprint.chapter_plan）
    start_chapter_no：本卷起始全书章号（plan 里的 chapter 是卷内相对号）
    changed_from：从哪一章开始算「被改动的范围」（该章之后的出场才算数）

    返回 {"created": [...], "removed": [...], "foreshadow_frozen": [...],
          "plotline_notes": {...}, "skipped": [...]}。
    """
    res = {"created": [], "removed": [], "foreshadow_frozen": [],
           "plotline_notes": {}, "skipped": []}
    if not isinstance(plan, list) or not plan:
        return res

    start_no = int(start_chapter_no or 1)
    from_no = int(changed_from or 1)

    # ---- 收集新骨架里出现的一切（绝对章号口径）----
    on_stage: set[str] = set()
    locations: set[str] = set()
    seeds: set[str] = set()
    plotline_notes: dict[int, dict[str, str]] = {}

    for item in plan:
        if not isinstance(item, dict):
            continue
        try:
            rel = int(item.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        if rel <= 0:
            continue
        abs_ch = start_no + max(rel - 1, 0)
        for name, _note in _split_names(item.get("characters_on_stage")):
            on_stage.add(name)
        for name, _note in _split_names(item.get("location")):
            locations.add(name)
        for fs in _split_foreshadow_names(item.get("foreshadow_seeds") or []):
            seeds.add(fs)
        notes = item.get("plotline_notes")
        if isinstance(notes, dict) and notes:
            plotline_notes[abs_ch] = {str(k): str(v) for k, v in notes.items() if str(v)}

    # ---- 1) 新增：建档 ----
    for name in sorted(on_stage):
        st = _seed_character(store, name, "", None, "plan_reconcile")
        if st == "created":
            res["created"].append(name)
    for name in sorted(locations):
        st = _seed_simple(store, "location", name, "", None, "plan_reconcile")
        if st == "created":
            res["created"].append(name)
    for fs in sorted(seeds):
        st, _planted = _seed_foreshadow(store, fs, None, "plan_reconcile", planted=True)
        if st == "created":
            res["created"].append(fs)

    # ---- 2) 移除：软标记 ----
    # 只看「改动范围之后才出现」的实体——前面章号的出场属于旧骨架，
    # 那些章可能还没写，不能因为新骨架没列它就判定为不再出现。
    for e in store.list():
        if e.type not in ("character", "location", "item", "plotline"):
            continue
        if e.name in on_stage or e.name in locations:
            continue
        future = [a for a in e.appearances if a >= from_no]
        if not future:
            continue
        e.state_attrs["plan_removed_at"] = str(from_no)
        e.appearances = [a for a in e.appearances if a < from_no]
        e.updated_at = _now_iso()
        res["removed"].append(e.name)

    # ---- 3) 伏笔：不回退状态，只标记不再推进 ----
    for e in store.list(type="foreshadow"):
        if e.name in seeds:
            continue
        future = [a for a in e.appearances if a >= from_no]
        if not future:
            continue
        e.state_attrs["plan_no_more"] = str(from_no)
        e.appearances = [a for a in e.appearances if a < from_no]
        e.updated_at = _now_iso()
        res["foreshadow_frozen"].append(e.name)

    # ---- 4) 剧情线细节 ----
    for abs_ch, notes in plotline_notes.items():
        for pname, note in notes.items():
            e = store.find("plotline", pname)
            if e is None:
                # store.add() 同名会抛 ValueError——必须先 find 再 add
                try:
                    e = store.add("plotline", pname, summary="", source="plan_reconcile")
                except ValueError:
                    e = store.find("plotline", pname)
                    if e is None:
                        res["skipped"].append(pname)
                        continue
            e.state_attrs[f"plan_notes_ch{abs_ch}"] = note
            e.updated_at = _now_iso()
            res["plotline_notes"][pname] = note

    store._flush()
    return res
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_plan_reconcile.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/entities/vault_plan.py backend/tests/test_plan_reconcile.py
git commit -m "feat(entities): add soft-mark plan reconciliation for entity store"
```

---

### Task 4: 知情表自动推导（gen.py）

**Files:**
- Create: `backend/app/knowledge/gen.py`
- Test: `backend/tests/test_knowledge_gen.py`

**Interfaces:**
- Consumes: `EntityStore`（取角色档案）；`origin_novel`（dict）；`plan_item`（dict，章骨架）；`written_text`（str，已写定稿正文）
- Produces:
  - `build_messages(chapter: int, day: int, characters: list[dict], origin_novel: dict, plan_item: dict, written_text: str) -> list[dict]`
  - `normalize_draft(raw: Any) -> dict` —— 返回 `{"day": int, "characters": {...}, "reader": {...}}`；无效时返回 `{"_error": "..."}`
  - `generate_draft(provider, chapter, day, characters, origin_novel, plan_item, written_text) -> dict`

**关键约束（必须写进 prompt）：`knows` / `blinded_to` 是 2-12 字关键词，不是完整句。**

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_knowledge_gen.py`：

```python
"""知情表自动推导测试：草稿规范化 + 关键词约束。"""
from __future__ import annotations

from app.knowledge.gen import build_messages, normalize_draft


class TestNormalizeDraft:
    def test_rejects_non_dict(self):
        assert "_error" in normalize_draft("不是字典")
        assert "_error" in normalize_draft(None)
        assert "_error" in normalize_draft([1, 2, 3])

    def test_normalizes_structure(self):
        raw = {
            "day": 3,
            "characters": {
                "c1": {"knows": ["献名仪式"], "blinded_to": ["黑胆珠"],
                       "knows_detail": ["唐迟已确认寄件人就是陆骁"]},
            },
            "reader": {"known": ["a"], "misbelieved": ["b"],
                       "unknown": ["c"], "gaps": ["d"]},
        }
        out = normalize_draft(raw)
        assert out["day"] == 3
        assert out["characters"]["c1"]["knows"] == ["献名仪式"]
        assert out["characters"]["c1"]["blinded_to"] == ["黑胆珠"]
        assert out["reader"]["gaps"] == ["d"]

    def test_drops_overlong_keywords(self):
        """完整句当关键词会导致审稿检查一条都命中不了——必须在推导层就挡住。"""
        raw = {"day": 1, "characters": {
            "c1": {"knows": ["这是一条远远超过十二个字的很长的信息描述"]}}}
        out = normalize_draft(raw)
        assert out["characters"]["c1"].get("knows", []) == []

    def test_keeps_detail_long(self):
        raw = {"day": 1, "characters": {
            "c1": {"knows_detail": ["阮衡知道献名仪式的全部步骤以及第七次供血的日期和地点"]}}}
        out = normalize_draft(raw)
        assert len(out["characters"]["c1"]["knows_detail"]) == 1

    def test_reader_four_fields_default_empty(self):
        out = normalize_draft({"day": 1, "characters": {}})
        assert out["reader"] == {"known": [], "misbelieved": [], "unknown": [], "gaps": []}

    def test_empty_characters_ok(self):
        out = normalize_draft({"day": 1, "characters": {}})
        assert out["characters"] == {}


class TestBuildMessages:
    def test_prompt_contains_keyword_constraint(self):
        """关键词约束必须进 prompt——否则模型一定给完整句。"""
        msgs = build_messages(
            chapter=7, day=3,
            characters=[{"id": "c1", "name": "唐迟", "summary": "主角"}],
            origin_novel={"one_liner": "雾汀镇", "summary": "梗概", "days": [{"day": 3, "summary": "第三天"}]},
            plan_item={"focus": "查勘旧闸", "plot_beats": {"qi": "起"}},
            written_text="唐迟醒来。",
        )
        system = msgs[0]["content"]
        user = msgs[1]["content"]
        assert "2-12" in system or "2~12" in system
        assert "唐迟" in user
        assert "雾汀镇" in user

    def test_written_text_included(self):
        """已写正文要进输入——模型得知道唐迟已经发现了什么。"""
        msgs = build_messages(
            chapter=7, day=3, characters=[],
            origin_novel={}, plan_item={},
            written_text="唐迟把黑胆珠封进铜盒。",
        )
        assert "黑胆珠封进铜盒" in msgs[1]["content"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_knowledge_gen.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.knowledge.gen'`

- [ ] **Step 3: 写实现**

创建 `backend/app/knowledge/gen.py`：

```python
"""知情表自动推导：模型推 → 草稿 → 作者确认 → 落库。

## 三条铁律

1. **只从已有档案和骨架推导，不臆造**。输入里没有的信息不要编。
2. **不覆盖作者内容**。生成的是草稿，走预览弹窗，作者逐条勾选再决定采用；
   端点本身不写库。
3. **knows / blinded_to 必须是 2-12 字关键词**。这是审稿检查
   （review/book_checks.check_knowledge_boundary）能工作的硬前提——
   完整句永远撞不中正文。完整描述另存 *_detail。

与 entities/memory_gen.py 是同一套范式（草稿 → 确认 → 落库），
可以照抄它的 diff_against_current 思路。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

log = logging.getLogger("knowledge.gen")

#: 关键词长度上下限
KW_MIN, KW_MAX = 2, 12

#: 读者层四个字段
READER_FIELDS = ("known", "misbelieved", "unknown", "gaps")

_ITEM_PREFIX_RE = re.compile(r"^\s*(?:[-*•·]|\(?\d{1,2}[.、)）]|[一二三四五六七八九十]+[、.])\s*")
_TAIL_NOTE_RE = re.compile(r"[（(][^）)]{8,}[）)]\s*$")


def _strip_noise(x: str) -> str:
    s = _ITEM_PREFIX_RE.sub("", str(x).strip()).strip()
    return _TAIL_NOTE_RE.sub("", s).strip()


def _clean_kw(raw: Any) -> list[str]:
    """关键词：长度必须落在 [2,12]，否则丢弃。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        kw = _strip_noise(str(item or ""))
        if not kw or not (KW_MIN <= len(kw) <= KW_MAX):
            continue
        if kw not in out:
            out.append(kw)
    return out


def _clean_text(raw: Any) -> list[str]:
    """完整描述：不限长，只去空去重。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        t = _strip_noise(str(item or ""))
        if t and t not in out:
            out.append(t)
    return out


def normalize_draft(raw: Any) -> dict:
    """把模型返回洗成规范结构。

    返回 {"day": int, "characters": {...}, "reader": {...}}。
    输入形状不对时返回 {"_error": "..."}。
    """
    if not isinstance(raw, dict):
        return {"_error": "模型返回的结构不对"}
    if isinstance(raw.get("characters"), str) or isinstance(raw.get("characters"), list):
        raw = raw.get("characters") and raw or raw
    day_raw = raw.get("day")
    try:
        day = int(day_raw) if day_raw is not None else 0
    except (TypeError, ValueError):
        day = 0

    out_chars: dict[str, dict] = {}
    chars = raw.get("characters")
    if isinstance(chars, list):
        # 模型可能给 [{id, knows, blinded_to}] 的列表形式
        for item in chars:
            if not isinstance(item, dict):
                continue
            eid = str(item.get("id") or item.get("name") or "").strip()
            if not eid:
                continue
            rec: dict[str, list[str]] = {}
            for f in ("knows", "blinded_to"):
                vals = _clean_kw(item.get(f))
                if vals:
                    rec[f] = vals
            for f in ("knows_detail", "blinded_detail"):
                vals = _clean_text(item.get(f))
                if vals:
                    rec[f] = vals
            if rec:
                out_chars[eid] = rec
    elif isinstance(chars, dict):
        for eid, entry in chars.items():
            if not isinstance(entry, dict):
                continue
            rec = {}
            for f in ("knows", "blinded_to"):
                vals = _clean_kw(entry.get(f))
                if vals:
                    rec[f] = vals
            for f in ("knows_detail", "blinded_detail"):
                vals = _clean_text(entry.get(f))
                if vals:
                    rec[f] = vals
            if rec:
                out_chars[str(eid)] = rec

    out_reader: dict[str, list[str]] = {}
    reader = raw.get("reader")
    if isinstance(reader, dict):
        for f in READER_FIELDS:
            vals = _clean_text(reader.get(f))
            if vals:
                out_reader[f] = vals

    return {"day": day, "characters": out_chars, "reader": out_reader}


_GEN_SYSTEM = """你是一名无限流小说的信息差设计师，正在为《第七日的亡者》某一章推导**知情边界表**。

主角唐迟「魂穿」进一具陌生的身体——一个在原剧本里注定七天之后死掉的倒霉蛋。
读者往往知道一些主角不知道的事（悬念），主角也知道一些读者不知道的事。
你的任务是把这张「信息 × 持有者」的表推导出来。

严格以 JSON 返回，不要输出 JSON 之外的任何文字，不要写 Markdown 代码块包裹。

{
  "day": 3,
  "characters": {
    "角色id": {
      "knows": ["关键词1", "关键词2"],
      "blinded_to": ["关键词3"],
      "knows_detail": ["完整描述1"],
      "blinded_detail": ["完整描述2"]
    }
  },
  "reader": {
    "known": ["读者已确知的信息"],
    "misbelieved": ["读者正相信但其实为假的认知"],
    "unknown": ["读者还不知道的底牌"],
    "gaps": ["读者不知但某个角色知道的信息"]
  }
}

## 最关键的一条约束：knows / blinded_to 必须是 2-12 字的**关键词**

不是完整句子。审稿检查会用这些关键词去撞正文，命中才算数。

✓ 正确：「献名仪式」「黑胆珠」「周朔」「第七次供血」
✗ 错误：「阮衡知道献名仪式的全部步骤」← 太长，撞不中正文，检查永远空转
✗ 错误：「他」← 太短，撞车率极高

完整描述放 knows_detail / blinded_detail（不限长度），那两个字段只给人看。

## 其他约束

1. **只从输入推导，不臆造**。输入里没有的信息不要编。
2. **reader.unknown 是悬念清单**：这些是后面几章要用的底牌，本章不能写出来。
3. **reader.gaps 是反转的落点**：读者不知道但角色知道的信息，下一章可以爆。
4. **每个出场角色都要给至少一条 knows 和一条 blinded_to**——
   只给一条说明数据不完整。
5. 角色 id 用输入里给的 id，不要自己编名字。
"""


def build_messages(chapter: int, day: int, characters: list[dict],
                   origin_novel: dict, plan_item: dict,
                   written_text: str) -> list[dict]:
    """把角色档案 + 原剧本 + 骨架 + 已写正文拍平成模型输入。"""
    lines: list[str] = [f"本章：第{chapter}章（副本 Day {day}）"]

    if characters:
        lines.append("本章出场角色档案：")
        for c in characters:
            name = str(c.get("name") or "").strip()
            eid = str(c.get("id") or "").strip()
            bits = [f"  - id={eid} 姓名={name}"]
            if c.get("summary"):
                bits.append(f"简介={c['summary']}")
            if c.get("details"):
                bits.append(f"档案={str(c['details'])[:300]}")
            lines.append(" ".join(bits))

    on = origin_novel if isinstance(origin_novel, dict) else {}
    if on.get("one_liner"):
        lines.append(f"原剧本一句话：{on['one_liner']}")
    if on.get("summary"):
        lines.append(f"原剧本梗概：\n{str(on['summary'])[:2000]}")
    days = on.get("days") or []
    if isinstance(days, list) and days:
        dlines = [f"- 第{d.get('day')}天：{d.get('summary')}" for d in days if isinstance(d, dict)]
        lines.append("原剧本逐日推进：\n" + "\n".join(dlines))

    if isinstance(plan_item, dict):
        if plan_item.get("focus"):
            lines.append(f"本章骨架方向：{plan_item['focus']}")
        beats = plan_item.get("plot_beats")
        if isinstance(beats, dict):
            blines = [f"  {k}：{v}" for k, v in beats.items() if v]
            if blines:
                lines.append("本章起承转合：\n" + "\n".join(blines))
        if plan_item.get("characters_on_stage"):
            lines.append(f"本章出场实体：{plan_item['characters_on_stage']}")

    if written_text and written_text.strip():
        lines.append("已经写出的正文（唐迟目前已经发现的事）：\n"
                     + str(written_text)[-3000:])

    return [
        {"role": "system", "content": _GEN_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def generate_draft(provider: Any, chapter: int, day: int, characters: list[dict],
                   origin_novel: dict, plan_item: dict, written_text: str,
                   *, regen: Callable[[], str] | None = None) -> dict:
    """生成知情表草稿。失败时返回 {"_error": ...}，不抛异常阻断作者操作。"""
    from ..generation import complete_structured

    messages = build_messages(chapter, day, characters, origin_novel,
                              plan_item, written_text)

    def _call() -> str:
        if regen is not None:
            return regen()
        return provider.chat(messages).text

    try:
        comp = complete_structured(_call, max_regenerations=2)
    except Exception as e:
        log.warning("知情表生成失败（第%s章）：%s", chapter, e)
        return {"_error": f"生成失败：{e}"}

    data = comp.data if isinstance(comp.data, dict) else {}
    if not data:
        return {"_error": "模型没有返回可用内容"}

    out = normalize_draft(data)
    if "_error" in out:
        return out
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_knowledge_gen.py -v`
Expected: PASS —— 8 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/knowledge/gen.py backend/tests/test_knowledge_gen.py
git commit -m "feat(knowledge): add knowledge-table inference with keyword constraint"
```

---

### Task 5: 知情表 API 端点 + 接线（ledger / book_checks）

**Files:**
- Create: `backend/app/api/knowledge_routes.py`
- Modify: `backend/app/api/main.py`（注册路由 + 新增 `origin-context` 端点）
- Modify: `backend/app/context/ledger.py`（`_knowledge_seg()` 改为按章优先）
- Modify: `backend/app/review/book_checks.py`（`check_knowledge_boundary()` 按章取边界）
- Test: `backend/tests/test_knowledge_routes.py`

**Interfaces:**
- Consumes: `KnowledgeStore` / `knowledge_for`（Task 2）；`merge_knowledge`（Task 2）
- Produces:
  - `POST /api/projects/{pid}/knowledge/draft` —— body `{chapter: int}`；返回 `{ok, day, characters, reader}` 或 `{ok: false, error}`
  - `POST /api/projects/{pid}/knowledge/apply` —— body `{chapter: int, day: int, characters: {}, reader: {}}`；返回 `{ok, chapter}`
  - `GET /api/projects/{pid}/knowledge` —— 返回 `{chapters: {...}}`
  - `GET /api/projects/{pid}/desk/{chapter}/origin-context` —— 返回 `{chapter, day, origin: {...}, direction: {...}, knowledge: {...}}`
  - `ledger._knowledge_seg(e, chapter: int, store) -> str`（签名变更：新增 chapter 与 store 两参）
  - `book_checks.check_knowledge_boundary(desk, chapter, ..., knowledge_store=None)`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_knowledge_routes.py`：

```python
"""知情表端点 + 按章接线测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore
from app.knowledge.store import KnowledgeStore
from app.projects import ProjectStore
from app.wizard import DeskStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr("app.api.main._entities_for",
                        lambda pid: EntityStore(tmp_path / "ent" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._desk_for",
                        lambda pid: DeskStore(tmp_path / "dsk" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main.knowledge_for",
                        lambda pid: KnowledgeStore(tmp_path / "kn" / f"{pid}.json"))
    from app.api.main import app
    return TestClient(app)


class TestKnowledgeApplyGet:
    def test_apply_then_get(self, client):
        pid = client.post("/api/projects", json={"title": "知情书"}).json()["id"]
        r = client.post(f"/api/projects/{pid}/knowledge/apply", json={
            "chapter": 7, "day": 3,
            "characters": {"c1": {"knows": ["献名仪式"], "blinded_to": ["黑胆珠"]}},
            "reader": {"known": ["a"], "misbelieved": [], "unknown": [], "gaps": ["g"]},
        })
        assert r.status_code == 200, r.text
        got = client.get(f"/api/projects/{pid}/knowledge").json()
        assert "7" in got["chapters"]
        assert got["chapters"]["7"]["characters"]["c1"]["knows"] == ["献名仪式"]

    def test_apply_rejects_overlong_keyword(self, client):
        """超长关键词在存储层被挡掉——不让它进账本。"""
        pid = client.post("/api/projects", json={"title": "知情书"}).json()["id"]
        client.post(f"/api/projects/{pid}/knowledge/apply", json={
            "chapter": 7, "day": 3,
            "characters": {"c1": {"knows": ["这是一条远远超过十二个字的很长的信息描述"]}},
        })
        got = client.get(f"/api/projects/{pid}/knowledge").json()
        assert got["chapters"]["7"]["characters"]["c1"].get("knows", []) == []

    def test_get_empty_project(self, client):
        pid = client.post("/api/projects", json={"title": "空书"}).json()["id"]
        assert client.get(f"/api/projects/{pid}/knowledge").json()["chapters"] == {}


class TestOriginContext:
    def test_returns_three_panels(self, client):
        pid = client.post("/api/projects", json={"title": "原剧本书"}).json()["id"]
        r = client.get(f"/api/projects/{pid}/desk/1/origin-context")
        assert r.status_code == 200, r.text
        data = r.json()
        for key in ("chapter", "day", "origin", "direction", "knowledge"):
            assert key in data, key
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_knowledge_routes.py -v`
Expected: FAIL —— 404（端点不存在）

- [ ] **Step 3: 写实现**

创建 `backend/app/api/knowledge_routes.py`：

```python
"""知情表端点：草稿推导 / 落库 / 读取。

按章存储（角色层 + 读者层），与实体库的全局 state_attrs 并存但优先。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

log = logging.getLogger("api.knowledge")


@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]
    entities_for: Callable[[str], Any]
    chore_provider: Callable
    knowledge_for: Callable[[str], Any]


_deps: _Deps | None = None


def register_knowledge_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable,
    desk_for: Callable,
    entities_for: Callable,
    chore_provider: Callable,
    knowledge_for: Callable,
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        desk_for=desk_for,
        entities_for=entities_for,
        chore_provider=chore_provider,
        knowledge_for=knowledge_for,
    )
    router = APIRouter(tags=["knowledge"])
    router.add_api_route("/api/projects/{pid}/knowledge/draft", _knowledge_draft, methods=["POST"])
    router.add_api_route("/api/projects/{pid}/knowledge/apply", _knowledge_apply, methods=["POST"])
    router.add_api_route("/api/projects/{pid}/knowledge", _knowledge_get, methods=["GET"])
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("knowledge_routes 未调用 register_knowledge_routes 初始化")
    return _deps


class _DraftIn(BaseModel):
    chapter: int


class _ApplyIn(BaseModel):
    chapter: int
    day: int = 0
    characters: dict = {}
    reader: dict = {}


def _chapter_plan_item(vol, chapter: int, start_no: int) -> dict:
    """取某章在 chapter_plan 里的骨架项（按绝对章号换算）。"""
    bp = getattr(vol, "rhythm_blueprint", None) or {}
    plan = bp.get("chapter_plan") if isinstance(bp, dict) else []
    if not isinstance(plan, list):
        return {}
    rel = chapter - int(start_no or 1) + 1
    for item in plan:
        if not isinstance(item, dict):
            continue
        try:
            if int(item.get("chapter") or 0) == rel:
                return item
        except (TypeError, ValueError):
            continue
    return {}


def _knowledge_draft(pid: str, body: _DraftIn = Body(...)) -> dict:
    """POST 推导某章的知情表草稿。不写库。"""
    deps = _must_deps()
    deps.require_project(pid)
    chapter = int(body.chapter)
    desk = deps.desk_for(pid)
    work = desk.get(chapter)
    if work is None:
        raise HTTPException(status_code=404, detail=f"第{chapter}章不存在")

    vol_no = getattr(work, "field_no", None)
    vol = deps.volumes_for(pid).get(vol_no) if vol_no else None
    start_no = int(getattr(vol, "start_chapter_no", 1) or 1) if vol else 1
    day = int(getattr(work, "day", 0) or 0)
    if isinstance(work.brief, dict):
        sel = work.brief.get("selected")
        if isinstance(sel, dict) and sel.get("day"):
            try:
                day = int(sel["day"])
            except (TypeError, ValueError):
                pass

    plan_item = _chapter_plan_item(vol, chapter, start_no)

    # 本章出场角色
    store = deps.entities_for(pid)
    on_stage = plan_item.get("characters_on_stage") or []
    wanted = {str(x).strip() for x in on_stage if str(x).strip()}
    characters: list[dict] = []
    for e in store.list(type="character"):
        if wanted and e.name not in wanted:
            continue
        characters.append({
            "id": e.id, "name": e.name,
            "summary": e.summary, "details": e.details,
        })

    # 原剧本
    origin: dict = {}
    if vol is not None:
        try:
            from ..book.volume import origin_novel_of
            origin = origin_novel_of(vol)
        except Exception:
            origin = {}

    # 已写正文（定稿优先）
    written = ""
    if work.finalized_at:
        written = (work.polish or {}).get("text") or (work.draft or {}).get("text") or ""
    else:
        written = (work.draft or {}).get("text") or ""

    provider = deps.chore_provider("knowledge")
    from ..knowledge.gen import generate_draft
    out = generate_draft(provider, chapter, day, characters, origin, plan_item, written)
    if "_error" in out:
        return {"ok": False, "error": out["_error"], "chapter": chapter}
    return {
        "ok": True,
        "chapter": chapter,
        "day": out.get("day", day),
        "characters": out.get("characters", {}),
        "reader": out.get("reader", {}),
    }


def _knowledge_apply(pid: str, body: _ApplyIn = Body(...)) -> dict:
    """POST 落库某章知情表。关键词长度由 store 层校验。"""
    deps = _must_deps()
    deps.require_project(pid)
    store = deps.knowledge_for(pid)
    store.set_chapter(body.chapter, {
        "day": body.day,
        "characters": body.characters,
        "reader": body.reader,
    })
    store.flush()
    return {"ok": True, "chapter": body.chapter}


def _knowledge_get(pid: str) -> dict:
    """GET 读全量知情表。"""
    deps = _must_deps()
    deps.require_project(pid)
    store = deps.knowledge_for(pid)
    return {"chapters": store.chapters}
```

在 `backend/app/api/main.py` 末尾（`register_interlude_routes(...)` 之后）追加：

```python
# ============================================================
# 知情表（按章）+ 原剧本本章视角
# ============================================================
from ..knowledge.store import knowledge_for as _knowledge_for
from .knowledge_routes import register_knowledge_routes
register_knowledge_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
    entities_for=_entities_for,
    chore_provider=_chore_provider,
    knowledge_for=_knowledge_for,
)


@app.get("/api/projects/{pid}/desk/{chapter:int}/origin-context")
def desk_origin_context(pid: str, chapter: int) -> dict:
    """折叠信息窗的数据源：原剧本剧情 + 原剧情发展方向 + 本时点知情表。

    单独开端点而不是塞进 desk_chapter 响应：那个响应已经很重
    （各阶段全文 + 大纲），而这个数据只在展开折叠窗时才需要。
    """
    _require_project(pid)
    desk = _desk_for(pid)
    work = desk.get(chapter)
    if work is None:
        raise HTTPException(status_code=404, detail=f"第{chapter}章不存在")

    vol_no = getattr(work, "field_no", None)
    vol = _volumes_for(pid).get(vol_no) if vol_no else None
    day = int(getattr(work, "day", 0) or 0)
    if isinstance(work.brief, dict):
        sel = work.brief.get("selected")
        if isinstance(sel, dict) and sel.get("day"):
            try:
                day = int(sel["day"])
            except (TypeError, ValueError):
                pass

    # ① 原剧本详细剧情
    origin: dict = {"summary": "", "full_text": "", "one_liner": "", "days": [], "acts": []}
    if vol is not None:
        try:
            from ..book.volume import origin_novel_of
            on = origin_novel_of(vol)
            origin = {
                "one_liner": str(on.get("one_liner") or ""),
                "summary": str(on.get("summary") or ""),
                "full_text": str(on.get("full_text") or ""),
                "acts": list(on.get("acts") or []),
                "days": list(on.get("days") or []),
                "survivors": list(on.get("survivors") or []),
            }
        except Exception:
            pass

    # ② 原剧情发展方向
    direction: dict = {}
    if vol is not None:
        tso = getattr(vol, "tangchi_story_outline", None) or {}
        if isinstance(tso, dict):
            direction = {
                "outline_text": str(tso.get("outline_text") or ""),
                "core_twist": str(tso.get("core_twist") or ""),
                "false_cause_red_herring": str(tso.get("false_cause_red_herring") or ""),
                "rule_exploit": str(tso.get("rule_exploit") or ""),
                "climax": str(tso.get("climax") or ""),
                "day_breakdown": list(tso.get("day_breakdown") or []),
            }

    # ③ 本时点知情表（角色层 + 读者层）
    kstore = _knowledge_for(pid)
    knowledge = {
        "day": day,
        "characters": kstore.character_entries(chapter),
        "reader": kstore.reader_entry(chapter),
    }

    return {
        "chapter": chapter,
        "day": day,
        "origin": origin,
        "direction": direction,
        "knowledge": knowledge,
    }
```

修改 `backend/app/context/ledger.py` 的 `_knowledge_seg()`（替换原函数体）：

```python
def _knowledge_seg(e, chapter: int | None = None,
                   knowledge_store=None) -> str:
    """角色行尾追加知情边界（spec 3），如：
    「（已知：唐迟是第七个住客；不知情：猫眼里的东西）」

    **按章表优先**：chapter 不为 None 且传了 knowledge_store 时，
    先查按章知情表（app/knowledge/store.py）；查不到再退回实体库的
    全局 state_attrs。

    为什么按章优先：state_attrs["knows"] 是全局单值——第 7 章知道的
    信息到第 12 章就该变，塞在全局字段里必然互相覆盖。

    为什么进任务书而不只做审稿检查：审稿是**事后**报警，写这一章时模型
    根本不知道这条边界；注入才是让模型在写的时候就守住边界。
    """
    if knowledge_store is not None and chapter is not None:
        try:
            from app.knowledge.store import merge_knowledge
            seg = merge_knowledge(e, chapter, knowledge_store)
            if seg:
                return seg
        except Exception:
            pass
    attrs = getattr(e, "state_attrs", None) or {}
    knows = str(attrs.get("knows") or "").strip()
    blind = str(attrs.get("blinded_to") or "").strip()
    if not knows and not blind:
        return ""
    parts: list[str] = []
    if knows:
        parts.append(f"已知：{_clean(knows, 60)}")
    if blind:
        parts.append(f"不知情：{_clean(blind, 60)}")
    return "（" + "；".join(parts) + "）"
```

在 `backend/app/context/ledger.py` 的 `build_chapter_checklist()` 里，把调用处（约 L1363）改为：

```python
            if etype == "character":
                seg += _knowledge_seg(e, chapter, knowledge_store)
```

并在 `build_chapter_checklist()` 的函数签名里追加 `knowledge_store=None` 参数。

修改 `backend/app/review/book_checks.py` 的 `check_knowledge_boundary()`：在函数签名追加 `knowledge_store=None` 参数，并把读 `blinded_to` 的那处（约 L401）改为：

```python
    for e in entity_store.list(type="character"):
        raw = ""
        if knowledge_store is not None:
            try:
                from app.knowledge.store import merge_knowledge
                entry = knowledge_store.character_entries(chapter).get(e.id, {})
                raw = "、".join(entry.get("blinded_to") or [])
            except Exception:
                raw = ""
        if not raw:
            raw = (e.state_attrs or {}).get("blinded_to") or ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_knowledge_routes.py tests/test_knowledge_store.py tests/test_state_ledger.py tests/test_entities.py -v`
Expected: PASS —— 新增 5 passed，且既有测试无回归

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/knowledge_routes.py backend/app/api/main.py backend/app/context/ledger.py backend/app/review/book_checks.py backend/tests/test_knowledge_routes.py
git commit -m "feat(knowledge): add knowledge API endpoints and per-chapter injection wiring"
```

---

### Task 6: 骨架重排核心（replan.py）

**Files:**
- Create: `backend/app/knowledge/replan.py`
- Test: `backend/tests/test_replan.py`

**Interfaces:**
- Consumes: `chapter_plan`（list）；`brief`（dict，章级方向卡，含 `is_custom`）；`finalized`（set of int，已定稿章号）；`directive`（str，作者提示）
- Produces:
  - `split_by_day(plan: list, groups: int = 3) -> list[list[dict]]` —— 按 day 分段
  - `plan_diff(old: list, new: list, *, finalized: set, custom: set) -> list[dict]` —— 每章一条 `{chapter, changed, locked, custom, old, new, fields}`
  - `snapshot_plan(vol, plan: list) -> None` —— 写进 `vol.chapter_plan_backups`（保留最近 3 次）
  - `restore_plan(vol) -> list | None` —— 回滚上一次，返回恢复后的 plan
  - `rebuild_brief(plan_item: dict, *, chapter: int, old_brief: dict | None) -> dict` —— 重建 `brief.selected`
  - `apply_replan(vol, desk, store, new_plan: list, accepted: list[int], *, start_chapter_no: int, finalized: set) -> dict`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_replan.py`：

```python
"""骨架重排：分段 / diff / 快照 / 回滚 / brief 重建。"""
from __future__ import annotations

from app.knowledge.replan import (
    apply_replan,
    plan_diff,
    rebuild_brief,
    restore_plan,
    snapshot_plan,
    split_by_day,
)


def _plan(n=6, day_base=1):
    out = []
    for i in range(n):
        out.append({
            "chapter": i + 1,
            "day": day_base + i // 2,
            "title": f"第{i + 1}章",
            "focus": f"方向{i + 1}",
            "beat_type": "engine",
            "words": 3500,
            "plot_beats": {"qi": "起", "cheng": "承", "zhuan": "转", "he": "合"},
            "characters_on_stage": ["唐迟"],
        })
    return out


class _Vol:
    def __init__(self):
        self.volume_no = 2
        self.start_chapter_no = 1
        self.rhythm_blueprint = {"chapter_plan": _plan()}
        self.chapter_plan_backups = []


class TestSplitByDay:
    def test_splits_into_groups(self):
        groups = split_by_day(_plan(6), groups=3)
        assert len(groups) == 3

    def test_all_chapters_preserved(self):
        plan = _plan(7)
        groups = split_by_day(plan, groups=3)
        got = [p["chapter"] for g in groups for p in g]
        assert got == [p["chapter"] for p in plan]

    def test_single_group_when_plan_short(self):
        groups = split_by_day(_plan(1), groups=3)
        assert len(groups) == 1


class TestPlanDiff:
    def test_detects_changed_chapter(self):
        old = _plan(3)
        new = _plan(3)
        new[1]["focus"] = "改了"
        d = plan_diff(old, new, finalized=set(), custom=set())
        changed = [x for x in d if x["changed"]]
        assert len(changed) == 1
        assert changed[0]["chapter"] == 2

    def test_finalized_chapter_marked_locked(self):
        """已定稿章不参与重算——diff 里要标出来。"""
        d = plan_diff(_plan(3), _plan(3), finalized={2}, custom=set())
        row = [x for x in d if x["chapter"] == 2][0]
        assert row["locked"] is True

    def test_custom_chapter_marked(self):
        """作者手写的章默认不改——diff 里要单独标。"""
        d = plan_diff(_plan(3), _plan(3), finalized=set(), custom={3})
        row = [x for x in d if x["chapter"] == 3][0]
        assert row["custom"] is True

    def test_unchanged_chapter_not_flagged(self):
        d = plan_diff(_plan(3), _plan(3), finalized=set(), custom=set())
        assert all(not x["changed"] for x in d)

    def test_diff_reports_changed_fields(self):
        old = _plan(3)
        new = _plan(3)
        new[0]["title"] = "新标题"
        d = plan_diff(old, new, finalized=set(), custom=set())
        row = [x for x in d if x["chapter"] == 1][0]
        assert "title" in row["fields"]


class TestSnapshotRestore:
    def test_snapshot_keeps_three(self):
        v = _Vol()
        for i in range(5):
            snapshot_plan(v, _plan(3))
        assert len(v.chapter_plan_backups) == 3

    def test_restore_returns_previous(self):
        v = _Vol()
        before = [dict(p) for p in v.rhythm_blueprint["chapter_plan"]]
        snapshot_plan(v, before)
        v.rhythm_blueprint["chapter_plan"] = _plan(2)
        restored = restore_plan(v)
        assert len(restored) == 3
        assert v.rhythm_blueprint["chapter_plan"][0]["focus"] == "方向1"

    def test_restore_without_snapshot_returns_none(self):
        v = _Vol()
        assert restore_plan(v) is None


class TestRebuildBrief:
    def test_keeps_plot_beats_and_adds_scenes(self):
        item = _plan(1)[0]
        item["scenes"] = [{"name": "第一场", "goal": "g"}]
        sel = rebuild_brief(item, chapter=1, old_brief=None)
        assert sel["plot_beats"]["qi"] == "起"
        assert sel["scenes"][0]["name"] == "第一场"

    def test_preserves_extra_edits(self):
        """作者填过的追加要求不能被重排冲掉。"""
        item = _plan(1)[0]
        old = {"selected": {"id": "x", "extra_edits": "必须保留这句"}}
        sel = rebuild_brief(item, chapter=1, old_brief=old)
        assert sel["extra_edits"] == "必须保留这句"

    def test_uses_plan_title_and_focus(self):
        item = _plan(1)[0]
        sel = rebuild_brief(item, chapter=1, old_brief=None)
        assert sel["title"] == "第1章"
        assert sel["summary"] == "方向1"


class TestApplyReplan:
    def test_applies_accepted_chapters_only(self):
        from app.entities import EntityStore
        from app.wizard import DeskStore
        import tempfile, os

        tmp = tempfile.mkdtemp()
        desk = DeskStore(os.path.join(tmp, "d.json"))
        store = EntityStore(os.path.join(tmp, "e.json"))
        v = _Vol()
        old = _plan(3)
        new = _plan(3)
        new[0]["focus"] = "新方向"
        new[1]["focus"] = "新方向二"
        rep = apply_replan(v, desk, store, new, accepted=[1],
                           start_chapter_no=1, finalized=set())
        assert rep["applied"] == [1]
        assert v.rhythm_blueprint["chapter_plan"][0]["focus"] == "新方向"
        assert v.rhythm_blueprint["chapter_plan"][1]["focus"] == "方向2"

    def test_marks_rewritten(self):
        from app.entities import EntityStore
        from app.wizard import DeskStore
        import tempfile, os

        tmp = tempfile.mkdtemp()
        desk = DeskStore(os.path.join(tmp, "d.json"))
        store = EntityStore(os.path.join(tmp, "e.json"))
        v = _Vol()
        apply_replan(v, desk, store, _plan(2), accepted=[1],
                     start_chapter_no=1, finalized=set())
        assert v.chapter_plan_rewritten_at
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_replan.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.knowledge.replan'`

- [ ] **Step 3: 写实现**

创建 `backend/app/knowledge/replan.py`：

```python
"""骨架重排：分段重算 → 逐章 diff → 接受 → 写骨架 + 重建方向卡 + 对账实体库。

## 三条铁律

1. **已定稿章永不参与重算**，只作为上下文喂给模型。
2. **作者手写章（brief.is_custom）默认不覆盖**，diff 里单独标，可手动勾选。
3. **改前必快照**（chapter_plan_backups，保留最近 3 次），重排是破坏性操作。

## 为什么分段重算而不是一次到底

18 章全字段一次输出容易被截断，且模型看不到「哪些章必须保持不动」的边界。
按 day 分段后：段内整段重排保证连贯性，段间传衔接约束。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger("knowledge.replan")

#: 骨架快照保留次数
BACKUP_KEEP = 3

#: diff 里要比较的字段
DIFF_FIELDS = ("title", "focus", "beat_type", "words", "day",
               "plot_beats", "scenes", "characters_on_stage",
               "foreshadow_seeds", "foreshadow_payback", "location",
               "time_window", "sensory_hook")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def split_by_day(plan: list, groups: int = 3) -> list[list[dict]]:
    """按 day 把骨架切成若干段，每段内的章连续。

    day 相同的章放同一段——它们共享原剧本的同一时间点，重排时该一起看。
    """
    if not isinstance(plan, list) or not plan:
        return []
    buckets: dict[int, list[dict]] = {}
    for item in plan:
        if not isinstance(item, dict):
            continue
        try:
            d = int(item.get("day") or 1)
        except (TypeError, ValueError):
            d = 1
        buckets.setdefault(d, []).append(item)
    days = sorted(buckets)
    if not days:
        return []
    n = max(1, min(int(groups or 1), len(days)))
    # 按天数量尽量均分
    per = -(-len(days) // n)
    out: list[list[dict]] = []
    for i in range(0, len(days), per):
        seg: list[dict] = []
        for d in days[i:i + per]:
            seg.extend(buckets[d])
        if seg:
            out.append(seg)
    return out


def plan_diff(old: list, new: list, *, finalized: set, custom: set) -> list[dict]:
    """逐章对比，返回每章一条记录。

    locked：已定稿章（不参与重算）
    custom：作者手写章（默认不覆盖）
    changed：内容有变
    fields：变了哪些字段
    """
    old_map = {}
    for item in (old or []):
        if isinstance(item, dict):
            try:
                old_map[int(item.get("chapter") or 0)] = item
            except (TypeError, ValueError):
                continue
    new_map = {}
    for item in (new or []):
        if isinstance(item, dict):
            try:
                new_map[int(item.get("chapter") or 0)] = item
            except (TypeError, ValueError):
                continue

    rows: list[dict] = []
    for ch in sorted(set(old_map) | set(new_map)):
        o = old_map.get(ch) or {}
        n = new_map.get(ch) or {}
        fields = []
        for f in DIFF_FIELDS:
            if o.get(f) != n.get(f):
                fields.append(f)
        rows.append({
            "chapter": ch,
            "changed": bool(fields),
            "locked": ch in (finalized or set()),
            "custom": ch in (custom or set()),
            "fields": fields,
            "old": o,
            "new": n,
        })
    return rows


def snapshot_plan(vol, plan: list) -> None:
    """改前把骨架存进快照（保留最近 BACKUP_KEEP 次）。"""
    backups = list(getattr(vol, "chapter_plan_backups", None) or [])
    backups.append({"at": _now(), "plan": [dict(p) for p in (plan or [])]})
    while len(backups) > BACKUP_KEEP:
        backups.pop(0)
    vol.chapter_plan_backups = backups


def restore_plan(vol) -> list | None:
    """回滚到上一次快照；没有快照返回 None。"""
    backups = list(getattr(vol, "chapter_plan_backups", None) or [])
    if not backups:
        return None
    last = backups.pop()
    vol.chapter_plan_backups = backups
    bp = getattr(vol, "rhythm_blueprint", None)
    if not isinstance(bp, dict):
        return None
    plan = last.get("plan") or []
    bp["chapter_plan"] = plan
    return plan


def rebuild_brief(plan_item: dict, *, chapter: int, old_brief: dict | None) -> dict:
    """按新骨架重建 brief.selected。

    保留作者填过的 extra_edits（除非旧方向完全换了章）。
    """
    sel: dict[str, Any] = {
        "id": f"replan-{chapter}",
        "title": str(plan_item.get("title") or f"第{chapter}章"),
        "summary": str(plan_item.get("focus") or ""),
        "hooks": "",
        "day": plan_item.get("day"),
        "beat_type": plan_item.get("beat_type") or "engine",
        "foreshadow_advance": list(plan_item.get("foreshadow_payback") or []),
        "entities_on_stage": [str(x) for x in (plan_item.get("characters_on_stage") or [])],
        "source": "replan",
        "chapter_words_target": int(plan_item.get("words") or 3500),
        "time_window": str(plan_item.get("time_window") or ""),
        "location": str(plan_item.get("location") or ""),
        "plot_beats": dict(plan_item.get("plot_beats") or {}),
        "foreshadow_seeds": [str(s) for s in (plan_item.get("foreshadow_seeds") or [])],
        "sensory_hook": str(plan_item.get("sensory_hook") or ""),
        "chapter_title": str(plan_item.get("title") or ""),
    }
    scenes = plan_item.get("scenes")
    if isinstance(scenes, list):
        sel["scenes"] = scenes

    # 保留作者的追加要求
    if isinstance(old_brief, dict):
        old_sel = old_brief.get("selected")
        if isinstance(old_sel, dict):
            extra = str(old_sel.get("extra_edits") or "").strip()
            if extra:
                sel["extra_edits"] = extra
            hooks = old_sel.get("hooks")
            if hooks:
                sel["hooks"] = hooks
    return sel


def apply_replan(vol, desk, store, new_plan: list, accepted: list[int], *,
                 start_chapter_no: int, finalized: set) -> dict:
    """把接受的章写进卷级骨架 + 重建章级方向卡 + 对账实体库。

    返回 {"applied": [...], "skipped": [...], "reconcile": {...}}。
    """
    bp = getattr(vol, "rhythm_blueprint", None)
    if not isinstance(bp, dict):
        bp = {}
        vol.rhythm_blueprint = bp

    old_plan = list(bp.get("chapter_plan") or [])
    snapshot_plan(vol, old_plan)

    old_map = {}
    for item in old_plan:
        if isinstance(item, dict):
            try:
                old_map[int(item.get("chapter") or 0)] = item
            except (TypeError, ValueError):
                continue

    applied: list[int] = []
    skipped: list[int] = []
    for item in (new_plan or []):
        if not isinstance(item, dict):
            continue
        try:
            rel = int(item.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        if rel <= 0:
            continue
        if rel not in (accepted or []):
            skipped.append(rel)
            continue
        old_map[rel] = item
        applied.append(rel)

    merged = [old_map[k] for k in sorted(old_map)]
    bp["chapter_plan"] = merged

    # 重建章级方向卡
    start_no = int(start_chapter_no or 1)
    for rel in applied:
        abs_ch = start_no + max(rel - 1, 0)
        work = desk.get(abs_ch)
        if work is None:
            continue
        old_brief = work.brief if isinstance(work.brief, dict) else None
        sel = rebuild_brief(old_map[rel], chapter=abs_ch, old_brief=old_brief)
        work.brief = {
            "selected": sel,
            "selected_at": _now(),
            "extra_edits": str(sel.get("extra_edits") or ""),
            "chapter_no": abs_ch,
            "day": sel.get("day"),
            "is_custom": False,
            "source": "replan",
        }
    desk._flush()

    # 实体库对账（软标记）
    from ..entities.vault_plan import reconcile_with_plan
    changed_from = start_no + max(min(applied) - 1, 0) if applied else start_no
    reconcile = reconcile_with_plan(store, merged, start_chapter_no=start_no,
                                    changed_from=changed_from)

    vol.chapter_plan_rewritten_at = _now()
    return {"applied": applied, "skipped": skipped, "reconcile": reconcile}
```

在 `backend/app/book/volume.py` 的 `Volume` dataclass 末尾追加两个字段，并在 `from_dict` 里读取：

```python
    # 骨架重排：快照（保留最近 3 次）+ 最近一次改写时间
    chapter_plan_backups: list = field(default_factory=list)
    chapter_plan_rewritten_at: str = ""
```

`from_dict` 中（紧跟 `rhythm_blueprint=d.get("rhythm_blueprint"),` 之后）：

```python
            chapter_plan_backups=list(d.get("chapter_plan_backups", []) or []),
            chapter_plan_rewritten_at=str(d.get("chapter_plan_rewritten_at", "") or ""),
```

并把 `chapter_plan_backups` 与 `chapter_plan_rewritten_at` 加入 `VolumeStore.update_planning()` 的 `planning_fields` 集合。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_replan.py -v`
Expected: PASS —— 22 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/knowledge/replan.py backend/app/book/volume.py backend/tests/test_replan.py
git commit -m "feat(knowledge): add skeleton replan core with diff, snapshot and restore"
```

---

### Task 7: 骨架重排 API 端点

**Files:**
- Modify: `backend/app/api/brief_routes.py`（新增三个卷级端点）
- Test: `backend/tests/test_replan_routes.py`

**Interfaces:**
- Consumes: `split_by_day` / `plan_diff` / `apply_replan` / `snapshot_plan` / `restore_plan`（Task 6）
- Produces:
  - `POST /api/projects/{pid}/workbench/volumes/{vol_no}/brief/replan` —— body `{directive: str, from_chapter: int}`；返回 `{ok, segments: [{day_range, chapters, plan}], diff: [...], finalized: [...], custom: [...]}`
  - `POST /api/projects/{pid}/workbench/volumes/{vol_no}/brief/replan/apply` —— body `{accepted: [int], plan: [...]}`；返回 `{ok, applied, skipped, reconcile}`
  - `POST /api/projects/{pid}/workbench/volumes/{vol_no}/brief/replan/restore` —— 返回 `{ok, restored: bool, plan: [...]}`

**注意**：`replan` 是**卷级**操作（一次重排后续多章），所以挂在 `/volumes/{vol_no}/brief/` 下，不带 `chapters/{cno}`——与章级的 `brief/options`、`brief/select` 区分。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_replan_routes.py`：

```python
"""骨架重排端点测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore
from app.projects import ProjectStore
from app.wizard import DeskStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr("app.api.main._entities_for",
                        lambda pid: EntityStore(tmp_path / "ent" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._desk_for",
                        lambda pid: DeskStore(tmp_path / "dsk" / f"{pid}.json"))
    from app.api.main import app
    return TestClient(app)


class TestReplanRoutes:
    def test_replan_requires_plan(self, client):
        """卷没跑过 rhythm 就重排——必须 422 而不是崩。"""
        pid = client.post("/api/projects", json={"title": "空卷"}).json()["id"]
        r = client.post(f"/api/projects/{pid}/workbench/volumes/2/brief/replan",
                        json={"directive": "加快节奏", "from_chapter": 4})
        assert r.status_code in (404, 422), r.text

    def test_restore_without_backup(self, client):
        pid = client.post("/api/projects", json={"title": "空卷"}).json()["id"]
        r = client.post(f"/api/projects/{pid}/workbench/volumes/2/brief/replan/restore")
        assert r.status_code in (200, 404, 422), r.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_replan_routes.py -v`
Expected: FAIL —— 404（端点不存在）

- [ ] **Step 3: 写实现**

在 `backend/app/api/brief_routes.py` 的 `register_brief_routes()` 里，追加三条路由注册（在 `router.add_api_route(old_prefix, _patch_brief, methods=["PATCH"])` 之后）：

```python
    router.add_api_route(f"{vol_prefix}/brief/replan", _brief_replan, methods=["POST"])
    router.add_api_route(f"{vol_prefix}/brief/replan/apply", _brief_replan_apply, methods=["POST"])
    router.add_api_route(f"{vol_prefix}/brief/replan/restore", _brief_replan_restore, methods=["POST"])
```

其中 `vol_prefix` 定义为（加在 `wb_prefix` 旁边）：

```python
    # 卷级路径：重排骨架是一次动多章的操作，不带 chapters/{cno}
    vol_prefix = "/api/projects/{pid}/workbench/volumes/{vol_no}"
```

追加请求体类：

```python
class _ReplanIn(BaseModel):
    directive: str = ""
    from_chapter: int | None = None


class _ReplanApplyIn(BaseModel):
    accepted: list[int] = []
    plan: list[dict] = []
```

追加三个端点函数（放在 `_brief_advance` 之后）：

```python
def _plan_of(vol) -> list:
    bp = getattr(vol, "rhythm_blueprint", None) or {}
    plan = bp.get("chapter_plan") if isinstance(bp, dict) else []
    return plan if isinstance(plan, list) else []


def _locked_and_custom(desk, plan: list, start_no: int) -> tuple[set, set]:
    """已定稿章号 + 作者手写章号（相对卷内号）。"""
    finalized: set = set()
    custom: set = set()
    for item in plan:
        if not isinstance(item, dict):
            continue
        try:
            rel = int(item.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        if rel <= 0:
            continue
        abs_ch = int(start_no) + max(rel - 1, 0)
        work = desk.get(abs_ch)
        if work is None:
            continue
        if getattr(work, "finalized_at", ""):
            finalized.add(rel)
        brief = getattr(work, "brief", None)
        if isinstance(brief, dict) and brief.get("is_custom"):
            custom.add(rel)
    return finalized, custom


def _brief_replan(pid: str, vol_no: int, body: _ReplanIn = Body(...)) -> dict:
    """POST 重排后续章骨架：按 day 分段调模型，返回逐章 diff（不写库）。"""
    deps = _must_deps()
    deps.require_project(pid)
    vs = deps.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    plan = _plan_of(vol)
    if not plan:
        raise HTTPException(status_code=422, detail="请先推演节奏蓝图（chapter_plan 为空）")

    desk = deps.desk_for(pid)
    start_no = int(getattr(vol, "start_chapter_no", 1) or 1)
    finalized, custom = _locked_and_custom(desk, plan, start_no)

    # 只重排 from_chapter 之后
    from_rel = 1
    if body.from_chapter:
        from_rel = max(1, int(body.from_chapter) - start_no + 1)
    target = [p for p in plan
              if isinstance(p, dict) and int(p.get("chapter") or 0) >= from_rel]
    if not target:
        raise HTTPException(status_code=422, detail="没有可重排的章节")

    segments = split_by_day(target, groups=3)
    out_segments: list[dict] = []
    new_plan_all: list[dict] = []
    for seg in segments:
        days = sorted({int(p.get("day") or 1) for p in seg})
        out_segments.append({
            "day_range": [days[0], days[-1]] if days else [0, 0],
            "chapters": [int(p.get("chapter") or 0) for p in seg],
            "plan": seg,
        })
        new_plan_all.extend(seg)

    # 真调模型重排（有 provider 时）；失败则原样返回，作者仍能看到 diff 结构
    try:
        new_plan_all = _replan_segments(deps, vol, plan, new_plan_all,
                                        segments, body.directive, finalized, custom)
    except Exception as e:
        log.warning("骨架重排模型调用失败：%s", e)

    diff = plan_diff(plan, new_plan_all, finalized=finalized, custom=custom)
    return {
        "ok": True,
        "segments": out_segments,
        "diff": diff,
        "finalized": sorted(finalized),
        "custom": sorted(custom),
    }


def _replan_segments(deps, vol, old_plan: list, new_plan: list,
                     segments: list, directive: str,
                     finalized: set, custom: set) -> list:
    """按段调模型重排。段内整段重排，段间传衔接约束。

    已定稿章与作者手写章从待重排集合里剔除，但作为上下文保留。
    """
    from ..knowledge.replan import rebuild_brief  # noqa: F401  (保持导入可见性)
    provider = deps.chore_provider("replan")
    out: list[dict] = []
    prev_tail = ""
    for seg in segments:
        editable = [p for p in seg
                    if int(p.get("chapter") or 0) not in finalized
                    and int(p.get("chapter") or 0) not in custom]
        if not editable:
            out.extend(seg)
            continue
        messages = _build_replan_messages(editable, directive, prev_tail, old_plan)
        try:
            from ..generation import complete_structured
            comp = complete_structured(lambda: provider.chat(messages).text,
                                       max_regenerations=2)
            data = comp.data if isinstance(comp.data, dict) else {}
            arr = data.get("plan") or data.get("chapters") or []
            if not isinstance(arr, list):
                arr = []
            by_ch = {}
            for item in arr:
                if isinstance(item, dict):
                    try:
                        by_ch[int(item.get("chapter") or 0)] = item
                    except (TypeError, ValueError):
                        continue
            merged_seg = []
            for p in seg:
                ch = int(p.get("chapter") or 0)
                merged_seg.append(by_ch.get(ch) or p)
            out.extend(merged_seg)
            tail = merged_seg[-1] if merged_seg else {}
            prev_tail = str(tail.get("focus") or "")
        except Exception as e:
            log.warning("分段重排失败（%s）：%s", seg[0].get("chapter") if seg else "?", e)
            out.extend(seg)
    return out


def _build_replan_messages(editable: list, directive: str,
                           prev_tail: str, old_plan: list) -> list[dict]:
    """构造重排 prompt。"""
    lines = [f"作者要求：{directive or '（无特别要求，按节奏合理性重排）'}"]
    if prev_tail:
        lines.append(f"上一段的收尾：{prev_tail}")
    lines.append("以下是待重排的章节骨架（卷内相对章号），请重新分配节奏与内容：")
    for p in editable:
        lines.append(
            f"  第{p.get('chapter')}章 day={p.get('day')} beat={p.get('beat_type')} "
            f"标题={p.get('title')} 方向={p.get('focus')}")
    lines.append(
        "严格以 JSON 返回：{\"plan\": [{\"chapter\": 相对章号, \"day\": n, \"title\": \"…\", "
        "\"focus\": \"…\", \"beat_type\": \"hook|sell|engine|climax|cooldown\", "
        "\"words\": 3000~4500, \"plot_beats\": {\"qi\": \"\", \"cheng\": \"\", "
        "\"zhuan\": \"\", \"he\": \"\"}}]}。chapter 必须原样保留，不要增删章节。")
    return [
        {"role": "system", "content": "你是长篇小说的节奏编排者，正在按作者要求重排章节骨架。"},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _brief_replan_apply(pid: str, vol_no: int, body: _ReplanApplyIn = Body(...)) -> dict:
    """POST 接受 diff：写骨架 + 重建方向卡 + 对账实体库。"""
    deps = _must_deps()
    deps.require_project(pid)
    vs = deps.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    desk = deps.desk_for(pid)
    store = deps.entities_for(pid) if deps.entities_for else None
    start_no = int(getattr(vol, "start_chapter_no", 1) or 1)

    from ..knowledge.replan import apply_replan
    rep = apply_replan(vol, desk, store, body.plan or [], body.accepted or [],
                       start_chapter_no=start_no, finalized=set())

    # 写回卷文件
    vs.update_planning(vol_no, rhythm_blueprint=vol.rhythm_blueprint)
    try:
        vol.chapter_plan_backups = list(vol.chapter_plan_backups or [])
        vol.chapter_plan_rewritten_at = vol.chapter_plan_rewritten_at
    except Exception:
        pass

    return {
        "ok": True,
        "applied": rep["applied"],
        "skipped": rep["skipped"],
        "reconcile": rep["reconcile"],
    }


def _brief_replan_restore(pid: str, vol_no: int) -> dict:
    """POST 回滚上一次骨架。"""
    deps = _must_deps()
    deps.require_project(pid)
    vs = deps.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    from ..knowledge.replan import restore_plan
    plan = restore_plan(vol)
    if plan is None:
        return {"ok": False, "restored": False, "plan": [], "message": "没有可回滚的快照"}
    vs.update_planning(vol_no, rhythm_blueprint=vol.rhythm_blueprint)
    return {"ok": True, "restored": True, "plan": plan}
```

在 `brief_routes.py` 顶部补一个 import：

```python
from ..knowledge.replan import plan_diff, split_by_day
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_replan_routes.py tests/test_replan.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/brief_routes.py backend/tests/test_replan_routes.py
git commit -m "feat(brief): add volume-level skeleton replan endpoints"
```

---

### Task 8: 字数门禁按档 + 场数上限 + 章级字数覆盖

**Files:**
- Modify: `backend/app/generation/quality.py`
- Modify: `backend/app/pipeline/engine.py`
- Test: `backend/tests/test_beat_gate.py`

**Interfaces:**
- Consumes: `beat_bounds()`（Task 1）
- Produces:
  - `quality.hard_gate(text, prev_tail, target_chars, min_ratio, max_ratio, stage, beat_type)` —— 新增 `beat_type` 参数；当 `beat_type` 非空时忽略 `min_ratio`/`max_ratio`，改用 `gate_lo`/`gate_hi`
  - `quality.scene_count(text) -> int` —— 复用已有的场景标记识别
  - `engine.PipelineSpec` 的 `target_chars` 可被章级 `words` 覆盖

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_beat_gate.py`：

```python
"""按 beat_type 分档的字数门禁 + 场数上限。"""
from __future__ import annotations

from app.generation.quality import hard_gate, scene_count


class TestBeatTypeWordGate:
    def test_climax_allows_4500(self):
        """climax 章 4500 字合法——一刀 3000 会削掉高潮。"""
        text = "字" * 4500
        r = hard_gate(text, target_chars=4000, beat_type="climax")
        assert not any("字数" in p for p in r.p0)

    def test_engine_flags_4500(self):
        """engine 章 4500 字超出上限——该拆章。"""
        text = "字" * 4500
        r = hard_gate(text, target_chars=3500, beat_type="engine")
        assert any("字数" in p for p in r.p1)

    def test_engine_allows_3400(self):
        text = "字" * 3400
        r = hard_gate(text, target_chars=3200, beat_type="engine")
        assert not any("字数" in p for p in r.p1)

    def test_cooldown_flags_3500(self):
        text = "字" * 3500
        r = hard_gate(text, target_chars=2700, beat_type="cooldown")
        assert any("字数" in p for p in r.p1)

    def test_beat_type_overrides_ratio(self):
        """给了 beat_type 就按档走，不再用 ±30%/±80% 的比例。"""
        text = "字" * 4400
        r = hard_gate(text, target_chars=4400, min_ratio=0.6, max_ratio=1.8,
                      beat_type="climax")
        assert not any("字数" in p for p in r.p1)

    def test_no_beat_type_keeps_old_behavior(self):
        """不给 beat_type 时行为不变——旧项目不受影响。"""
        text = "字" * 1000
        r = hard_gate(text, target_chars=1000, min_ratio=0.6, max_ratio=1.8)
        assert not any("字数" in p for p in r.p0)


class TestSceneCount:
    def test_counts_scene_markers(self):
        text = "第1场：客厅\n- 场景：x\n\n第2场：门口\n- 场景：y"
        assert scene_count(text) == 2

    def test_zero_when_no_markers(self):
        assert scene_count("这是一段普通散文，没有场标记。") == 0


class TestSceneUpperBound:
    def test_engine_five_scenes_flags_p1(self):
        """engine 章超过 3 场——信息太杂，该拆。"""
        text = "\n".join(f"第{i}场：x\n- 场景：y" for i in range(1, 6))
        r = hard_gate(text, target_chars=3400, stage="plot", beat_type="engine")
        assert any("场" in p for p in r.p1)

    def test_climax_four_scenes_ok(self):
        """climax 4 场合法。"""
        text = "\n".join(f"第{i}场：x\n- 场景：y" for i in range(1, 5))
        r = hard_gate(text, target_chars=4000, stage="plot", beat_type="climax")
        assert not any("场" in p for p in r.p1)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_beat_gate.py -v`
Expected: FAIL —— `TypeError: hard_gate() got an unexpected keyword argument 'beat_type'`

- [ ] **Step 3: 写实现**

修改 `backend/app/generation/quality.py`：

第一步，在文件头部加 import：

```python
from app.knowledge.beat import beat_bounds
```

第二步，替换 `hard_gate()` 的签名与字数检查段：

```python
def hard_gate(
    text: str,
    prev_tail: str = "",
    target_chars: int = 0,
    min_ratio: float = 0.6,
    max_ratio: float = 1.8,
    stage: str = "",
    beat_type: str = "",
) -> HardGateResult:
    """零 token 硬门禁。stage='plot' 时额外校验章细纲格式。

    **beat_type 非空时按分档判定字数**（gate_lo/gate_hi），忽略 min_ratio/max_ratio——
    climax 章 4500 字合法，engine 章 4500 字超限，一刀比例判不出来。
    """
    p0: list[str] = []
    p1: list[str] = []
    stats: dict = {}
    if not text or not text.strip():
        p0.append("正文为空")
        return HardGateResult(p0=p0, p1=p1, stats=stats)

    chars = len(text.strip())
    stats["chars"] = chars
    if beat_type:
        b = beat_bounds(beat_type)
        lo, hi = b["gate_lo"], b["gate_hi"]
        stats["beat_type"] = beat_type
        stats["words_band"] = [b["words_min"], b["words_max"]]
    elif target_chars > 0:
        lo = int(target_chars * min_ratio)
        hi = int(target_chars * max_ratio)
    else:
        lo = hi = 0
    if lo or hi:
        if chars < lo:
            p0.append(f"字数不足：{chars} / 下限 {lo}")
        elif chars > hi:
            p1.append(f"字数偏长：{chars} / 上限 {hi}")
```

第三步，把 plot 层的场数识别抽成模块级函数（放在 `hard_gate` 之前）：

```python
_SCENE_MARK_RE = re.compile(
    r"^\s*(?:[-·*>]\s*|#{1,4}\s*)?(?:第\s*\d+\s*场|场景\s*\d+|场\s*\d+)", re.M)


def scene_count(text: str) -> int:
    """数章细纲里的场景标记数。

    必须锚到行首（可带 #/列表符）：否则场内任何一处「第N场」
    （比如落点里写「收在第3场之后」）都会被计成一场，场数虚高后
    fields < scenes*4 会误报「字段不全」。
    """
    return len(_SCENE_MARK_RE.findall(text or ""))
```

第四步，在 plot 层（`is_outline` 分支内）加场数上限检查，插在「场数下限 2」判断之后：

```python
        b = beat_bounds(beat_type) if beat_type else None
        if b is not None and len(_scenes) > b["scenes_max"]:
            p1.append(
                f"场数过多：{len(_scenes)} 场（{beat_type} 章上限 {b['scenes_max']} 场）——"
                f"信息太杂，该拆成两章而不是塞在一章里。"
                f"每场只做一件事：一口气说完，不要把多个场景挤进同一章。"
            )
```

同时把 plot 层里原本的 `_scenes = _re.findall(...)` 改成用 `scene_count()` 复用同一个正则：

```python
        _scenes = _SCENE_MARK_RE.findall(text)
```

修改 `backend/app/pipeline/engine.py`：在 `PipelineSpec` 之外加一个按章覆盖的辅助函数，并在 draft/polish 执行处用它取 `target_chars`。找到调用 `hard_gate(...)` 的地方，改为传入 `beat_type`：

```python
# ---- 按 beat_type 覆盖章级目标字数 ----
# 章级 chapter_plan[*].words 是作者设定的目标（按 beat_type 分档），
# stage 默认的 target_chars（draft 4800 / polish 5000）跟它对不上——
# 分档生效就必须让章级值优先。

def resolve_target_chars(default_target: int, chapter_words: int | None,
                         beat_type: str = "") -> int:
    """章级 words 优先；没有则回落到 beat 分档的目标；再没有用 stage 默认。"""
    if chapter_words:
        try:
            w = int(chapter_words)
            if w > 0:
                return w
        except (TypeError, ValueError):
            pass
    if beat_type:
        from app.knowledge.beat import chapter_words_target
        return chapter_words_target(beat_type)
    return int(default_target)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_beat_gate.py tests/test_beat_bounds.py -v`
Expected: PASS —— 13 passed

- [ ] **Step 5: 跑全量后端测试确认无回归**

Run: `cd backend && python -m pytest tests/ -q 2>&1 | Select-Object -Last 20`
Expected: 不新增失败（改动前基线为 813 passed / 39 failed）

- [ ] **Step 6: 提交**

```bash
git add backend/app/generation/quality.py backend/app/pipeline/engine.py backend/tests/test_beat_gate.py
git commit -m "feat(quality): add beat-type word gate and scene upper bound"
```

---

### Task 9: 折叠信息窗前端

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/views/DeskView.vue`
- Test: `cd frontend && npx vue-tsc --noEmit`（类型检查）

**Interfaces:**
- Consumes: `GET /api/projects/{pid}/desk/{chapter}/origin-context`（Task 5）
- Produces:
  - `api.originContext(pid: string, chapter: number) => req<OriginContext>`
  - `interface OriginContext` —— `{chapter, day, origin: {...}, direction: {...}, knowledge: {...}}`

- [ ] **Step 1: 在 api.ts 加类型与封装**

在 `frontend/src/api.ts` 里，`deskApi` 之前新增：

```typescript
// ============================================================
// 原剧本·本章视角（折叠信息窗）
// ============================================================
export interface KnowledgeEntry {
  knows?: string[]
  blinded_to?: string[]
  knows_detail?: string[]
  blinded_detail?: string[]
}

export interface ReaderKnowledge {
  known?: string[]
  misbelieved?: string[]
  unknown?: string[]
  gaps?: string[]
}

export interface OriginContext {
  chapter: number
  day: number
  origin: {
    one_liner?: string
    summary?: string
    full_text?: string
    acts?: any[]
    days?: { day: number; summary: string }[]
    survivors?: string[]
  }
  direction: {
    outline_text?: string
    core_twist?: string
    false_cause_red_herring?: string
    rule_exploit?: string
    climax?: string
    day_breakdown?: any[]
  }
  knowledge: {
    day: number
    characters: Record<string, KnowledgeEntry>
    reader: ReaderKnowledge
  }
}

export const originApi = {
  context: (pid: string, chapter: number) =>
    req<OriginContext>(`/api/projects/${pid}/desk/${chapter}/origin-context`),
}
```

- [ ] **Step 2: 在 DeskView.vue 加状态与加载函数**

在 `DeskView.vue` 的 `<script setup>` 里，`const showRefs = ref<string[]>([])` 附近新增：

```typescript
import { originApi, type OriginContext } from '../api'

/** 原剧本·本章视角（折叠信息窗）——展开时才拉，不占首屏 */
const originCtx = ref<OriginContext | null>(null)
const originLoading = ref(false)
const originActive = ref<string[]>([])

async function loadOriginContext() {
  const pid = currentProjectId.value
  if (!pid || !currentNo.value) return
  if (originCtx.value) return
  originLoading.value = true
  try {
    originCtx.value = await originApi.context(pid, currentNo.value)
  } catch {
    originCtx.value = null
  } finally {
    originLoading.value = false
  }
}

function toggleOrigin(name: string) {
  const i = originActive.value.indexOf(name)
  if (i >= 0) originActive.value.splice(i, 1)
  else originActive.value.push(name)
  if (originActive.value.includes('origin')) loadOriginContext()
}
```

在 `selectChapter()` 里，切章时清空：

```typescript
  originCtx.value = null
  originActive.value = []
```

- [ ] **Step 3: 在模板里加折叠项**

在 `DeskView.vue` 的 `el-collapse` 内，「写作模块注入」折叠项之后、`</el-collapse>` 之前，加：

```vue
            <el-collapse-item name="origin">
              <template #title>
                原剧本·本章视角
                <el-tag v-if="originCtx" size="small" type="warning" style="margin-left: 8px">
                  Day {{ originCtx.day }}·{{ Object.keys(originCtx.knowledge.characters).length }} 人知情
                </el-tag>
                <el-tag v-else size="small" type="info" style="margin-left: 8px">展开加载</el-tag>
              </template>
              <div v-if="originLoading" style="font-size: 12px; color: #999">加载中…</div>
              <el-empty v-else-if="!originCtx" description="展开后加载原剧本与知情表" :image-size="60" />
              <template v-else>
                <!-- ① 原剧本详细剧情 -->
                <div style="margin-bottom: 14px">
                  <div style="font-size: 13px; font-weight: 600; margin-bottom: 6px">原剧本详细剧情</div>
                  <el-tabs size="small">
                    <el-tab-pane label="梗概">
                      <div style="font-size: 12px; color: #606266; white-space: pre-wrap">{{ originCtx.origin.summary || '（无）' }}</div>
                    </el-tab-pane>
                    <el-tab-pane label="逐日推进">
                      <div
                        v-for="d in originCtx.origin.days || []"
                        :key="d.day"
                        style="font-size: 12px; margin-bottom: 4px; padding: 4px 6px; border-radius: 4px"
                        :style="d.day === originCtx.day ? 'background:#fdf6ec' : ''"
                      >
                        <b>第{{ d.day }}天</b>{{ d.day === originCtx.day ? '（← 本章对应日）' : '' }}：{{ d.summary }}
                      </div>
                    </el-tab-pane>
                    <el-tab-pane label="通稿节选">
                      <div style="font-size: 12px; color: #606266; white-space: pre-wrap; max-height: 400px; overflow: auto">{{ originCtx.origin.full_text || '（无）' }}</div>
                    </el-tab-pane>
                  </el-tabs>
                </div>

                <!-- ② 原剧情发展方向 -->
                <div style="margin-bottom: 14px">
                  <div style="font-size: 13px; font-weight: 600; margin-bottom: 6px">原剧情发展方向</div>
                  <div style="font-size: 12px; color: #606266; white-space: pre-wrap; margin-bottom: 6px">{{ originCtx.direction.outline_text || '（无）' }}</div>
                  <el-descriptions :column="1" size="small" border>
                    <el-descriptions-item label="核心反转">{{ originCtx.direction.core_twist || '（无）' }}</el-descriptions-item>
                    <el-descriptions-item label="假因果">{{ originCtx.direction.false_cause_red_herring || '（无）' }}</el-descriptions-item>
                    <el-descriptions-item label="规则利用">{{ originCtx.direction.rule_exploit || '（无）' }}</el-descriptions-item>
                    <el-descriptions-item label="终局">{{ originCtx.direction.climax || '（无）' }}</el-descriptions-item>
                  </el-descriptions>
                  <el-table :data="originCtx.direction.day_breakdown || []" size="small" style="margin-top: 8px">
                    <el-table-column prop="day" label="天" width="50" />
                    <el-table-column prop="original_timeline_anchor" label="原剧本时间锚" />
                    <el-table-column prop="fate_layer_focus" label="因果层" />
                    <el-table-column prop="false_hypothesis" label="唐迟以为" />
                    <el-table-column prop="real_cause_progress" label="真因推进" />
                  </el-table>
                </div>

                <!-- ③ 本时点知情 -->
                <div>
                  <div style="font-size: 13px; font-weight: 600; margin-bottom: 6px">本时点知情</div>
                  <el-tabs size="small">
                    <el-tab-pane label="角色">
                      <el-table :data="Object.entries(originCtx.knowledge.characters).map(([id, e]) => ({ id, ...e }))" size="small">
                        <el-table-column prop="id" label="角色" width="120" />
                        <el-table-column label="已知">
                          <template #default="s">{{ (s.row.knows_detail?.join('、') || s.row.knows?.join('、')) || '—' }}</template>
                        </el-table-column>
                        <el-table-column label="不知情">
                          <template #default="s">{{ (s.row.blinded_detail?.join('、') || s.row.blinded_to?.join('、')) || '—' }}</template>
                        </el-table-column>
                      </el-table>
                    </el-tab-pane>
                    <el-tab-pane label="读者">
                      <el-descriptions :column="1" size="small" border>
                        <el-descriptions-item label="读者已知">{{ originCtx.knowledge.reader.known?.join('、') || '—' }}</el-descriptions-item>
                        <el-descriptions-item label="读者误以为">{{ originCtx.knowledge.reader.misbelieved?.join('、') || '—' }}</el-descriptions-item>
                        <el-descriptions-item label="读者不知（悬念）">{{ originCtx.knowledge.reader.unknown?.join('、') || '—' }}</el-descriptions-item>
                        <el-descriptions-item label="读者被瞒（gaps）">{{ originCtx.knowledge.reader.gaps?.join('、') || '—' }}</el-descriptions-item>
                      </el-descriptions>
                    </el-tab-pane>
                  </el-tabs>
                </div>
              </template>
            </el-collapse-item>
```

把 `el-collapse` 的 `v-model` 从 `showRefs` 保持不变，并把 `origin` 折叠项的展开事件接到 `loadOriginContext`——用 `@change` 事件：

```vue
          <el-collapse v-model="showRefs" style="margin-top: 10px" @change="(n: string[]) => { if (n.includes('origin')) loadOriginContext() }">
```

- [ ] **Step 4: 跑类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无输出（通过）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api.ts frontend/src/views/DeskView.vue
git commit -m "feat(front): add collapsible origin-context panel to chapter desk"
```

---

### Task 10: 骨架重排前端（按钮 + diff 弹窗）

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/views/WritingDeskView.vue`
- Test: `cd frontend && npx vue-tsc --noEmit`

**Interfaces:**
- Consumes: `POST .../brief/replan` / `replan/apply` / `replan/restore`（Task 7）
- Produces:
  - `api.replanApi.replan(pid, volNo, body)` / `.apply(pid, volNo, body)` / `.restore(pid, volNo)`
  - `interface ReplanDiffRow` —— `{chapter, changed, locked, custom, fields, old, new}`

- [ ] **Step 1: 在 api.ts 加封装**

```typescript
// ============================================================
// 骨架重排（卷级）
// ============================================================
export interface ReplanDiffRow {
  chapter: number
  changed: boolean
  locked: boolean
  custom: boolean
  fields: string[]
  old: any
  new: any
}

export const replanApi = {
  replan: (pid: string, volNo: number, body: { directive?: string; from_chapter?: number | null }) =>
    req<{ ok: boolean; segments: any[]; diff: ReplanDiffRow[]; finalized: number[]; custom: number[] }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/brief/replan`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  apply: (pid: string, volNo: number, body: { accepted: number[]; plan: any[] }) =>
    req<{ ok: boolean; applied: number[]; skipped: number[]; reconcile: any }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/brief/replan/apply`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  restore: (pid: string, volNo: number) =>
    req<{ ok: boolean; restored: boolean; plan: any[]; message?: string }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/brief/replan/restore`,
      { method: 'POST' },
    ),
}
```

- [ ] **Step 2: 在 WritingDeskView.vue 加状态与方法**

在 `<script setup>` 里新增：

```typescript
import { replanApi, type ReplanDiffRow } from '../api'

const replanVisible = ref(false)
const replanVolNo = ref(0)
const replanDirective = ref('')
const replanFrom = ref<number | null>(null)
const replanBusy = ref(false)
const replanDiff = ref<ReplanDiffRow[]>([])
const replanAccepted = ref<number[]>([])
const replanPlan = ref<any[]>([])

function openReplan(volNo: number) {
  replanVolNo.value = volNo
  replanVisible.value = true
  replanDiff.value = []
  replanAccepted.value = []
  replanPlan.value = []
}

async function runReplan() {
  const pid = currentProjectId.value
  if (!pid || !replanVolNo.value) return
  replanBusy.value = true
  try {
    const r = await replanApi.replan(pid, replanVolNo.value, {
      directive: replanDirective.value,
      from_chapter: replanFrom.value,
    })
    replanDiff.value = r.diff || []
    // 已定稿章与作者手写章默认不勾
    replanAccepted.value = replanDiff.value.filter(d => d.changed && !d.locked && !d.custom).map(d => d.chapter)
    replanPlan.value = replanDiff.value.map(d => d.new).filter(Boolean)
  } catch (e: any) {
    ElMessage.error(e?.message || '重排失败')
  } finally {
    replanBusy.value = false
  }
}

async function applyReplan() {
  const pid = currentProjectId.value
  if (!pid || !replanVolNo.value) return
  replanBusy.value = true
  try {
    const r = await replanApi.apply(pid, replanVolNo.value, {
      accepted: replanAccepted.value,
      plan: replanPlan.value,
    })
    ElMessage.success(`已应用 ${r.applied.length} 章`)
    replanVisible.value = false
    await loadSpine()
  } catch (e: any) {
    ElMessage.error(e?.message || '应用失败')
  } finally {
    replanBusy.value = false
  }
}

async function restoreReplan() {
  const pid = currentProjectId.value
  if (!pid || !replanVolNo.value) return
  try {
    const r = await replanApi.restore(pid, replanVolNo.value)
    if (r.restored) {
      ElMessage.success('已回滚上一次骨架')
      replanVisible.value = false
      await loadSpine()
    } else {
      ElMessage.warning(r.message || '没有可回滚的快照')
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '回滚失败')
  }
}
```

- [ ] **Step 3: 在模板里加按钮与弹窗**

在卷头部的「+新章」按钮旁加：

```vue
            <el-button
              class="wd-vol-add"
              type="warning"
              link
              size="small"
              @click="openReplan(v.no)"
            >
              �� 重排骨架
            </el-button>
```

在模板末尾加弹窗：

```vue
    <el-dialog
      v-model="replanVisible"
      title="重排后续章骨架"
      width="900px"
      append-to-body
    >
      <el-alert type="warning" :closable="false" style="margin-bottom: 12px">
        已定稿的章节不会被改动；你手写方向的章节默认不勾选，可以手动勾上。改完会自动同步实体库。
      </el-alert>
      <el-form inline style="margin-bottom: 12px">
        <el-form-item label="从第几章开始">
          <el-input-number v-model="replanFrom" :min="1" :controls="false" style="width: 100px" placeholder="默认全部" />
        </el-form-item>
        <el-form-item label="你的要求">
          <el-input v-model="replanDirective" style="width: 420px" placeholder="例：第 5 章之后节奏太慢，把陆骁的压力提前" />
        </el-form-item>
      </el-form>
      <div style="display: flex; gap: 8px; margin-bottom: 12px">
        <el-button type="primary" :loading="replanBusy" @click="runReplan">生成重排方案</el-button>
        <el-button :loading="replanBusy" :disabled="!replanAccepted.length" @click="applyReplan">
          接受 {{ replanAccepted.length }} 章
        </el-button>
        <el-button @click="restoreReplan">回滚上一次</el-button>
      </div>
      <el-table :data="replanDiff" size="small" max-height="460">
        <el-table-column label="采用" width="60">
          <template #default="s">
            <el-checkbox v-model="replanAccepted" :value="s.row.chapter" :disabled="s.row.locked" />
          </template>
        </el-table-column>
        <el-table-column prop="chapter" label="章" width="60" />
        <el-table-column label="标记" width="120">
          <template #default="s">
            <el-tag v-if="s.row.locked" size="small" type="success">已定稿</el-tag>
            <el-tag v-else-if="s.row.custom" size="small" type="warning">你手写的</el-tag>
            <el-tag v-else-if="!s.row.changed" size="small" type="info">未变</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="改动" width="120">
          <template #default="s">{{ (s.row.fields || []).join('、') || '—' }}</template>
        </el-table-column>
        <el-table-column label="改前 / 改后">
          <template #default="s">
            <div style="font-size: 12px; color: #999">{{ s.row.old?.title }} · {{ s.row.old?.focus }}</div>
            <div style="font-size: 12px; color: #606266">{{ s.row.new?.title }} · {{ s.row.new?.focus }}</div>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
```

- [ ] **Step 4: 跑类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无输出（通过）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api.ts frontend/src/views/WritingDeskView.vue
git commit -m "feat(front): add skeleton replan button and per-chapter diff dialog"
```

---

### Task 11: 知情表编辑入口（前端）

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/views/DeskView.vue`
- Test: `cd frontend && npx vue-tsc --noEmit`

**Interfaces:**
- Consumes: `POST .../knowledge/draft` / `knowledge/apply`（Task 5）
- Produces: `api.knowledgeApi.draft(pid, chapter)` / `.apply(pid, body)`

- [ ] **Step 1: 在 api.ts 加封装**

```typescript
// ============================================================
// 知情表（按章）
// ============================================================
export const knowledgeApi = {
  draft: (pid: string, chapter: number) =>
    req<{ ok: boolean; chapter: number; day: number; characters: Record<string, KnowledgeEntry>; reader: ReaderKnowledge; error?: string }>(
      `/api/projects/${pid}/knowledge/draft`,
      { method: 'POST', body: JSON.stringify({ chapter }) },
    ),
  apply: (pid: string, body: { chapter: number; day: number; characters: Record<string, KnowledgeEntry>; reader: ReaderKnowledge }) =>
    req<{ ok: boolean; chapter: number }>(
      `/api/projects/${pid}/knowledge/apply`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
}
```

- [ ] **Step 2: 在 DeskView.vue 的知情面板加编辑按钮**

在折叠信息窗的「本时点知情」标题行加一个按钮：

```vue
                  <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px">
                    <div style="font-size: 13px; font-weight: 600; flex: 1">本时点知情</div>
                    <el-button size="small" plain :loading="knBusy" @click="genKnowledge">推导知情表</el-button>
                    <el-button size="small" type="primary" plain :disabled="!knDraft" @click="saveKnowledge">保存</el-button>
                  </div>
```

并在 `<script setup>` 里新增状态与方法：

```typescript
const knBusy = ref(false)
const knDraft = ref<{ day: number; characters: Record<string, any>; reader: any } | null>(null)

async function genKnowledge() {
  const pid = currentProjectId.value
  if (!pid || !currentNo.value) return
  knBusy.value = true
  try {
    const r = await knowledgeApi.draft(pid, currentNo.value)
    if (!r.ok) {
      ElMessage.warning(r.error || '生成失败，手动填写即可')
      return
    }
    knDraft.value = { day: r.day, characters: r.characters || {}, reader: r.reader || {} }
    ElMessage.success('已生成知情表草稿，确认后点保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '生成失败')
  } finally {
    knBusy.value = false
  }
}

async function saveKnowledge() {
  const pid = currentProjectId.value
  if (!pid || !currentNo.value || !knDraft.value) return
  knBusy.value = true
  try {
    await knowledgeApi.apply(pid, {
      chapter: currentNo.value,
      day: knDraft.value.day,
      characters: knDraft.value.characters,
      reader: knDraft.value.reader,
    })
    ElMessage.success('知情表已保存')
    knDraft.value = null
    originCtx.value = null
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    knBusy.value = false
  }
}
```

- [ ] **Step 3: 跑类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无输出（通过）

- [ ] **Step 4: 提交**

```bash
git add frontend/src/api.ts frontend/src/views/DeskView.vue
git commit -m "feat(front): add knowledge table derivation and save entry"
```

---

### Task 12: 端到端验证

**Files:**
- Create: `backend/tests/test_skeleton_rewrite_e2e.py`
- Test: `backend/tests/test_skeleton_rewrite_e2e.py`

**Interfaces:**
- Consumes: 全部前置任务的产物
- Produces: 端到端回归测试

- [ ] **Step 1: 写端到端测试**

创建 `backend/tests/test_skeleton_rewrite_e2e.py`：

```python
"""端到端：骨架重排 → 知情表 → 折叠信息窗 → 实体对账。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore
from app.knowledge.store import KnowledgeStore
from app.projects import ProjectStore
from app.wizard import DeskStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr("app.api.main._entities_for",
                        lambda pid: EntityStore(tmp_path / "ent" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._desk_for",
                        lambda pid: DeskStore(tmp_path / "dsk" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main.knowledge_for",
                        lambda pid: KnowledgeStore(tmp_path / "kn" / f"{pid}.json"))
    from app.api.main import app
    return TestClient(app)


class TestEndToEnd:
    def test_full_chain(self, client):
        pid = client.post("/api/projects", json={"title": "端到端"}).json()["id"]

        # ① 建章 + 知情表落库
        r = client.post(f"/api/projects/{pid}/knowledge/apply", json={
            "chapter": 7, "day": 3,
            "characters": {"c1": {"knows": ["献名仪式"], "blinded_to": ["黑胆珠"]}},
            "reader": {"known": ["寄件人就是陆骁"], "misbelieved": ["替帖能断契约"],
                       "unknown": ["第四次供血"], "gaps": ["阮衡知道全部步骤"]},
        })
        assert r.status_code == 200, r.text

        # ② 折叠信息窗能拿到三块
        ctx = client.get(f"/api/projects/{pid}/desk/7/origin-context").json()
        assert ctx["day"] == 3
        assert "characters" in ctx["knowledge"]
        assert ctx["knowledge"]["characters"]["c1"]["knows"] == ["献名仪式"]
        assert ctx["knowledge"]["reader"]["gaps"] == ["阮衡知道全部步骤"]

        # ③ 知情表按章隔离：第 12 章不受第 7 章影响
        ctx12 = client.get(f"/api/projects/{pid}/desk/12/origin-context").json()
        assert ctx12["knowledge"]["characters"] == {}

    def test_apply_replan_marks_rewritten_and_reconciles(self, client, tmp_path):
        from app.book.volume import Volume, VolumeStore
        from app.knowledge.replan import apply_replan

        pid = client.post("/api/projects", json={"title": "重排"}).json()["id"]
        vs = VolumeStore(tmp_path / "vols")
        vol = Volume(volume_no=2, field_no=2, title="雾汀镇", start_chapter_no=1)
        vol.rhythm_blueprint = {"chapter_plan": [
            {"chapter": 1, "day": 1, "title": "第一章", "focus": "旧方向",
             "beat_type": "engine", "words": 4500,
             "characters_on_stage": ["唐迟", "周朔"]},
            {"chapter": 2, "day": 2, "title": "第二章", "focus": "旧方向二",
             "beat_type": "engine", "words": 4500,
             "characters_on_stage": ["唐迟"]},
        ]}
        vs.save(vol)

        desk = DeskStore(tmp_path / "dsk2" / f"{pid}.json")
        store = EntityStore(tmp_path / "ent2" / f"{pid}.json")
        store.add("character", "周朔", chapter=1)

        new_plan = [
            {"chapter": 1, "day": 1, "title": "第一章", "focus": "新方向",
             "beat_type": "hook", "words": 3200, "characters_on_stage": ["唐迟"]},
            {"chapter": 2, "day": 2, "title": "第二章", "focus": "新方向二",
             "beat_type": "engine", "words": 3400, "characters_on_stage": ["唐迟"]},
        ]
        rep = apply_replan(vol, desk, store, new_plan, accepted=[1, 2],
                           start_chapter_no=1, finalized=set())

        assert rep["applied"] == [1, 2]
        assert vol.chapter_plan_rewritten_at
        assert vol.rhythm_blueprint["chapter_plan"][0]["focus"] == "新方向"
        # 快照存在，可回滚
        assert len(vol.chapter_plan_backups) == 1
        # 实体库对账：周朔不再出现在新骨架里 → 软标记
        assert store.find("character", "周朔") is not None, "实体不能被硬删"

    def test_replan_preserves_extra_edits(self, client, tmp_path):
        from app.book.volume import Volume
        from app.knowledge.replan import apply_replan

        vol = Volume(volume_no=2, field_no=2, title="卷", start_chapter_no=1)
        vol.rhythm_blueprint = {"chapter_plan": [
            {"chapter": 1, "day": 1, "title": "第一章", "focus": "旧",
             "beat_type": "engine", "words": 4500}]}
        desk = DeskStore(tmp_path / "dsk3" / "d.json")
        desk.ensure(1)
        desk.get(1).brief = {"selected": {"id": "x", "extra_edits": "必须保留"}}
        desk._flush()
        store = EntityStore(tmp_path / "ent3" / "e.json")

        apply_replan(vol, desk, store,
                     [{"chapter": 1, "day": 1, "title": "第一章", "focus": "新",
                       "beat_type": "hook", "words": 3200}],
                     accepted=[1], start_chapter_no=1, finalized=set())
        assert desk.get(1).brief["selected"]["extra_edits"] == "必须保留"
```

- [ ] **Step 2: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_skeleton_rewrite_e2e.py -v`
Expected: PASS —— 3 passed

- [ ] **Step 3: 跑全量后端测试确认无回归**

Run: `cd backend && python -m pytest tests/ -q 2>&1 | Select-Object -Last 20`
Expected: 不新增失败（改动前基线为 813 passed / 39 failed）

- [ ] **Step 4: 重启后端并验证端点**

Run:
```powershell
cd 'C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio\backend'
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8321
```
另开一个终端：
```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8321/health' | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8321/api/projects/20261014-c6c432/desk/7/origin-context' | ConvertTo-Json -Depth 2
```
Expected: `{"status":"ok"}`；第二个请求返回含 `origin` / `direction` / `knowledge` 三个键

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skeleton_rewrite_e2e.py
git commit -m "test: add end-to-end coverage for skeleton rewrite and knowledge tables"
```

---

## 自检记录

**Spec 覆盖检查**（逐条对照设计文档）：

| 设计文档章节 | 对应任务 |
|---|---|
| 2.1 知情表：按章存储 | Task 2 |
| 2.1.3 自动推导 | Task 4 |
| 2.1.4 反向接回正文链路 | Task 5 |
| 2.2 折叠信息窗（三面板） | Task 9 |
| 2.3.2 分段重算 + diff | Task 6、Task 7 |
| 2.3.3 保护机制 | Task 6（快照）、Task 7（锁定/手写标记） |
| 2.3.4 写入路径 | Task 6（`apply_replan`） |
| 2.4.1 字数分档 | Task 1、Task 8 |
| 2.4.2 场数落数据结构 | Task 6（`rebuild_brief` 带 scenes）、Task 8（门禁） |
| 2.4.3 门禁调整 | Task 8 |
| 2.5 实体库对账 | Task 3、Task 6 |
| 4 端点清单 | Task 5、Task 7 |
| 5 错误处理 | Task 5、Task 7 |
| 6 测试策略 | 全部 Task 的测试步骤 |
| 7 改动清单 | 全部 Task |

**已知缺口**（设计文档「已知边界」里的第 4 条）：读者知情表第一版只做展示，没有「读者已知被提前泄露」的自动检查。计划未包含该检查项——它需要新的判定逻辑，不在本次范围。

**类型一致性核对**：`beat_bounds()` / `chapter_words_target()` / `KnowledgeStore.*` / `merge_knowledge()` / `split_by_day()` / `plan_diff()` / `snapshot_plan()` / `restore_plan()` / `rebuild_brief()` / `apply_replan()` / `scene_count()` / `resolve_target_chars()` —— 全部在首次定义的任务里给出签名，后续任务只调用不改签名。
