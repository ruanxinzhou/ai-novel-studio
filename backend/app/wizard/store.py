"""M5 引导式写作：向导状态机 + 章台簿记。

全局宪法：机器提议 · 来源标注 · 冲突待核 · 作者签收。wizard/desk 只做
簿记，站点完成与否永远由项目数据（已采纳版本、章台记录、跳过清单）
实时推导，不存机器说了算的死状态；作者可跳站，可随时回来补。

布局：
    backend/data/projects/{pid}/wizard.json   # 三格点子/种子/方向卡/章数/跳过清单
    backend/data/projects/{pid}/desk.json     # 每章 剧情/正文/润色/审稿/定稿 记录
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

STATIONS = ["topic", "brainstorm", "architecture", "plotplan", "writing", "polish"]
STATION_LABELS = {
    "topic": "选题",
    "brainstorm": "脑洞",
    "architecture": "故事架构",
    "plotplan": "剧情规划",
    "writing": "成稿",
    "polish": "润色",
}
STAGES = ("plot", "draft", "polish")

# 兑现四选一（P2③ 天蚕土豆：定稿时顺手点选，攒出自家节奏量纲）
PAYOFF_SCALES = ("none", "small", "medium", "climax")
PAYOFF_LABELS = {"none": "无", "small": "小爽点", "medium": "中兑现", "climax": "卷高潮"}

# 三线标签（Strand 节奏：定稿时顺手多选，本章推进了哪些线）
# 常量归 context.ledger（推导逻辑所有者），这里引用做入参校验
from app.context.ledger import STRANDS, STRAND_LABELS  # noqa: E402


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_ts() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


# ---- 向导状态 ----

@dataclass
class WizardData:
    idea: dict = field(default_factory=lambda: {"context": "", "desire": "", "obstacle": "", "text": ""})
    seed: str = ""
    direction: str = ""          # 作者选/改/自写后的最终方向
    direction_source: str = ""   # picked | edited | manual
    n_chapters: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def gates_ok(self) -> bool:
        """开工判据：情境+欲望+阻碍三格齐。"""
        return all(str(self.idea.get(k, "")).strip() for k in ("context", "desire", "obstacle"))

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WizardData":
        idea = d.get("idea") or {}
        return cls(
            idea={k: str(idea.get(k, "") or "") for k in ("context", "desire", "obstacle", "text")},
            seed=str(d.get("seed", "") or ""),
            direction=str(d.get("direction", "") or ""),
            direction_source=str(d.get("direction_source", "") or ""),
            n_chapters=int(d.get("n_chapters") or 0),
            skipped=[s for s in d.get("skipped", []) if s in STATIONS],
        )


class WizardStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: WizardData | None = None

    def get(self) -> WizardData:
        if self._data is None:
            if self.path.exists():
                self._data = WizardData.from_dict(
                    json.loads(self.path.read_text(encoding="utf-8")))
            else:
                self._data = WizardData()
        return self._data

    def _flush(self) -> None:
        assert self._data is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data.as_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    def update(self, idea: dict | None = None, seed: str | None = None,
               direction: str | None = None, direction_source: str | None = None,
               n_chapters: int | None = None) -> WizardData:
        d = self.get()
        if idea is not None:
            for k in ("context", "desire", "obstacle", "text"):
                if k in idea and idea[k] is not None:
                    d.idea[k] = str(idea[k])
        if seed is not None:
            d.seed = seed
        if direction is not None:
            d.direction = direction
        if direction_source is not None:
            d.direction_source = direction_source
        if n_chapters is not None:
            if n_chapters < 0:
                raise ValueError("章数不能为负")
            d.n_chapters = n_chapters
        self._flush()
        return d

    def toggle_skip(self, station: str) -> WizardData:
        if station not in STATIONS:
            raise ValueError(f"未知站：{station}")
        d = self.get()
        if station in d.skipped:
            d.skipped.remove(station)
        else:
            d.skipped.append(station)
        self._flush()
        return d

    # ---- 站点状态推导 ----

    def stations_state(self, pid: str, project, desk: "DeskStore") -> dict:
        """六站状态由数据实时推导（done 判据见 docs/m5-wizard-design.md）。

        current_station = 第一个未完成且未跳过的站；全部完成后向导毕业。
        """
        d = self.get()
        ch1 = desk.get(1)
        plot_ok = bool(ch1 and ch1.plot and ch1.plot["text"].strip())
        draft_ok = bool(ch1 and ch1.draft and ch1.draft["text"].strip())
        review_pass = bool(ch1 and ch1.review and ch1.review.get("verdict") == "pass")
        finalized = bool(ch1 and ch1.finalized_at)
        done_map = {
            "topic": project.current(pid, "intro") is not None,
            "brainstorm": bool(d.direction.strip()),
            "architecture": project.current(pid, "outline") is not None,
            "plotplan": plot_ok,
            "writing": draft_ok and review_pass,
            "polish": finalized,
        }
        states = []
        for st in STATIONS:
            states.append({
                "station": st,
                "label": STATION_LABELS[st],
                "done": done_map[st],
                "skipped": st in d.skipped,
            })
        current = next((s["station"] for s in states if not s["done"] and not s["skipped"]), None)
        return {
            "stations": states,
            "current_station": current,
            "graduated": all(s["done"] or s["skipped"] for s in states),
            "gates_ok": d.gates_ok,
        }


# ---- 章台簿记 ----

def _work(text: str, run_id: str = "") -> dict:
    return {"text": text, "run_id": run_id, "updated_at": _now()}


@dataclass
class Scene:
    """章内单场（M4 plot 回转寿司使用，M1 先建字段）。"""
    idx: int
    summary: str = ""
    emotion: str = ""
    hook: str = ""
    pov: str = "唐迟"
    target_words: int = 600
    status: str = "planned"   # planned | editing | locked
    locked_at: str = ""
    locked_by_msg_id: str | None = None

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        return cls(
            idx=int(d.get("idx", 0) or 0),
            summary=str(d.get("summary", "") or ""),
            emotion=str(d.get("emotion", "") or ""),
            hook=str(d.get("hook", "") or ""),
            pov=str(d.get("pov", "唐迟") or "唐迟"),
            target_words=int(d.get("target_words", 600) or 600),
            status=str(d.get("status", "planned") or "planned"),
            locked_at=str(d.get("locked_at", "") or ""),
            locked_by_msg_id=d.get("locked_by_msg_id"),
        )


@dataclass
class ChapterWork:
    no: int
    title: str = ""
    plot: dict = field(default_factory=dict)     # {text, run_id, updated_at}
    draft: dict = field(default_factory=dict)
    polish: dict = field(default_factory=dict)
    review: dict | None = None                   # review_chapter().as_dict()
    finalized_at: str = ""
    payoff: str = ""                             # 本章兑现规模：none/small/medium/climax，空=未记录
    strands: list[str] = field(default_factory=list)  # 本章推进的线：quest/fire/bond 多选，空=未记录
    # 书化字段（《第七日的亡者》四层结构）
    field_no: int | None = None                  # 所属场号（None = 未分配到具体场）
    day: int | None = None                       # 1-7 唐迟入戏第 N 天（T-6→T-0，写作节拍，非原剧本日历天）；None = 现实间章 / 未分配
    chapter_kind: str = "dungeon"               # 'mainline' | 'dungeon' | 'interlude'
    is_interlude: bool = False                   # 是否现实间章（由 chapter_kind 同步，保留兼容）
    interlude_kind: str = ""                     # 间章类型：爽点/羁绊/伏笔/污染征兆/杀青账
    # M1 新增
    scenes: list[Scene] = field(default_factory=list)
    state_snapshot_at_lock: dict = field(default_factory=dict)  # 定稿时刻实体状态快照
    # 章前 5 分钟卡：{direction, hooks, carryover, entities_present} 或 AI 3 张方向卡选中结果
    brief: dict | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["scenes"] = [s.as_dict() for s in self.scenes]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterWork":
        fn = d.get("field_no")
        dy = d.get("day")
        brief_raw = d.get("brief")
        # chapter_kind 回填：显式有效值 → is_interlude 老数据 → 默认 dungeon
        _valid_kinds = ("mainline", "dungeon", "interlude")
        raw_kind = d.get("chapter_kind")
        if raw_kind in _valid_kinds:
            chapter_kind = raw_kind
        elif d.get("is_interlude"):
            chapter_kind = "interlude"
        else:
            chapter_kind = "dungeon"
        is_interlude = (chapter_kind == "interlude")
        return cls(
            no=int(d["no"]), title=str(d.get("title", "") or ""),
            plot=dict(d.get("plot") or {}), draft=dict(d.get("draft") or {}),
            polish=dict(d.get("polish") or {}), review=d.get("review"),
            finalized_at=str(d.get("finalized_at", "") or ""),
            payoff=str(d.get("payoff", "") or ""),
            strands=[s for s in (d.get("strands") or []) if s in STRANDS],
            field_no=(int(fn) if fn is not None else None),
            day=(int(dy) if dy is not None else None),
            chapter_kind=chapter_kind,
            is_interlude=is_interlude,
            interlude_kind=str(d.get("interlude_kind", "") or ""),
            scenes=[Scene.from_dict(s) for s in (d.get("scenes") or [])],
            state_snapshot_at_lock=dict(d.get("state_snapshot_at_lock") or {}),
            brief=(brief_raw if isinstance(brief_raw, dict) else None),
        )


class DeskStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._chapters: dict[int, ChapterWork] | None = None

    # ---- 读写 ----

    def _load(self) -> dict[int, ChapterWork]:
        if self._chapters is None:
            self._chapters = {}
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._chapters = {int(k): ChapterWork.from_dict({**v, "no": k})
                                  for k, v in raw.get("chapters", {}).items()}
        return self._chapters

    def _flush(self) -> None:
        chapters = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"chapters": {str(k): c.as_dict() for k, c in chapters.items()}},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, no: int) -> ChapterWork | None:
        return self._load().get(no)

    def ensure(self, no: int, title: str = "", *, field_no: int | None = None,
               chapter_kind: str | None = None) -> ChapterWork:
        chapters = self._load()
        _valid_kinds = ("mainline", "dungeon", "interlude")
        created = False
        if no not in chapters:
            init_kind = chapter_kind if chapter_kind in _valid_kinds else "dungeon"
            chapters[no] = ChapterWork(
                no=no,
                field_no=field_no,
                chapter_kind=init_kind,
                is_interlude=(init_kind == "interlude"),
            )
            created = True
        work = chapters[no]
        mutated = created
        if title and not work.title:
            work.title = title
            mutated = True
        if field_no is not None and work.field_no != field_no:
            work.field_no = field_no
            mutated = True
        if chapter_kind is not None and chapter_kind in _valid_kinds and work.chapter_kind != chapter_kind:
            work.chapter_kind = chapter_kind
            work.is_interlude = (chapter_kind == "interlude")
            mutated = True
        if mutated:
            self._flush()
        return work

    def all_nos(self) -> list[int]:
        return sorted(self._load())

    def record(self, no: int, stage: str, text: str, run_id: str = "", title: str = "") -> ChapterWork:
        if stage not in STAGES:
            raise ValueError(f"未知阶段：{stage}")
        work = self.ensure(no, title)
        setattr(work, stage, _work(text, run_id))
        work.finalized_at = ""   # 定稿后再改动则退出定稿态
        self._flush()
        return work

    def record_review(self, no: int, review: dict) -> ChapterWork:
        work = self.ensure(no)
        work.review = review
        self._flush()
        return work

    def set_brief(self, no: int, brief: dict | None) -> ChapterWork:
        """整章写入章前 5 分钟卡；传 None 清空。"""
        work = self.ensure(no)
        work.brief = brief if brief else None
        self._flush()
        return work

    def update_brief_field(self, no: int, **updates) -> ChapterWork:
        """PATCH 章前卡字段（与前端 partial update 对齐）。"""
        work = self.ensure(no)
        if not isinstance(work.brief, dict):
            work.brief = {}
        for k, v in updates.items():
            work.brief[k] = v
        self._flush()
        return work

    def mark_finalized(self, no: int) -> ChapterWork:
        work = self.ensure(no)
        work.finalized_at = _now()
        self._flush()
        return work

    def unfinalize(self, no: int) -> ChapterWork:
        """反定稿：清除 finalized_at，但保留正文/剧情/润色/审稿结果——
        作者可以改字后重新定稿，不会丢失已写内容。"""
        work = self.ensure(no)
        if not work.finalized_at:
            raise ValueError(f"第 {no} 章尚未定稿，无需反定稿")
        work.finalized_at = ""
        self._flush()
        return work

    def record_payoff(self, no: int, payoff: str) -> ChapterWork:
        """记录/修改本章兑现规模（四选一）；正文改动不清除，作者随时可改。"""
        if payoff not in PAYOFF_SCALES:
            raise ValueError(f"未知兑现规模：{payoff}（可选 {PAYOFF_SCALES}）")
        work = self.ensure(no)
        work.payoff = payoff
        self._flush()
        return work

    def record_strands(self, no: int, strands: list[str]) -> ChapterWork:
        """记录/修改本章推进的三线标签（可多选可清空）；供节奏占比与断档检查消费。"""
        bad = [s for s in strands if s not in STRANDS]
        if bad:
            raise ValueError(f"未知线标签：{bad}（可选 {STRANDS}）")
        work = self.ensure(no)
        work.strands = list(dict.fromkeys(strands))   # 去重保序
        self._flush()
        return work

    def assign_field(self, no: int, field_no: int | None,
                     day: int | None = None, is_interlude: bool = False,
                     interlude_kind: str = "") -> ChapterWork:
        """绑定本章到某场某日（或标记为间章）；day=None 且 is_interlude=True 即现实间章。"""
        work = self.ensure(no)
        work.field_no = field_no
        if is_interlude:
            work.is_interlude = True
            work.chapter_kind = "interlude"
            work.day = None
            work.interlude_kind = interlude_kind or work.interlude_kind
        else:
            work.is_interlude = False
            work.chapter_kind = "dungeon"
            work.interlude_kind = ""
            work.day = day if day is not None and 1 <= day <= 7 else None
        self._flush()
        return work

    # ---- M1 新增：场次锁 + 状态快照 ----

    def _find_scene(self, work: ChapterWork, idx: int) -> Scene:
        for s in work.scenes:
            if s.idx == idx:
                return s
        raise ValueError(f"章{work.no}不存在场{idx}")

    def ensure_scene(self, no: int, idx: int, summary: str = "",
                     emotion: str = "", hook: str = "",
                     pov: str = "唐迟", target_words: int = 600) -> Scene:
        work = self.ensure(no)
        for s in work.scenes:
            if s.idx == idx:
                if summary:
                    s.summary = summary
                if emotion:
                    s.emotion = emotion
                if hook:
                    s.hook = hook
                if pov:
                    s.pov = pov
                if target_words:
                    s.target_words = target_words
                self._flush()
                return s
        s = Scene(idx=idx, summary=summary, emotion=emotion, hook=hook,
                  pov=pov, target_words=target_words, status="planned")
        work.scenes.append(s)
        work.scenes.sort(key=lambda x: x.idx)
        self._flush()
        return s

    def lock_scene(self, no: int, idx: int, summary: str,
                   source_msg_id: str | None = None) -> Scene:
        work = self.ensure(no)
        s = self._find_scene(work, idx)
        s.summary = summary
        s.status = "locked"
        s.locked_at = _now()
        s.locked_by_msg_id = source_msg_id
        self._flush()
        return s

    def save_snapshot(self, no: int, snapshot: dict) -> ChapterWork:
        work = self.ensure(no)
        work.state_snapshot_at_lock = dict(snapshot)
        self._flush()
        return work

    # ---- Task 2：章节管理 ----

    def _backup(self) -> None:
        """在写操作前将当前 desk.json 复制一份为 .bak.YYYYMMDD-HHMMSS；全新项目无文件则跳过。"""
        if not self.path.exists():
            return
        bak_path = self.path.with_suffix(self.path.suffix + f".bak.{_now_ts()}")
        shutil.copy2(self.path, bak_path)

    def max_no(self) -> int:
        chapters = self._load()
        return max(chapters.keys()) if chapters else 0

    def next_available_no(self) -> int:
        return self.max_no() + 1

    def all_sorted(self) -> list[ChapterWork]:
        chapters = self._load()
        return [chapters[n] for n in sorted(chapters)]

    def list_by_field(self, field_no: int | None) -> list[ChapterWork]:
        chapters = self._load()
        return [chapters[n] for n in sorted(chapters) if chapters[n].field_no == field_no]

    def rename(self, no: int, title: str) -> ChapterWork:
        chapters = self._load()
        if no not in chapters:
            raise ValueError(f"第 {no} 章不存在")
        self._backup()
        chapters[no].title = title
        self._flush()
        return chapters[no]

    def renumber(self, from_no: int, delta: int) -> None:
        """把 no >= from_no 的章号整体偏移 delta（仅改内存，不写盘）。delta>0 从大到小改键，delta<0 从小到大。"""
        chapters = self._load()
        if delta > 0:
            order = sorted((n for n in chapters if n >= from_no), reverse=True)
        else:
            order = sorted(n for n in chapters if n >= from_no)
        for old in order:
            work = chapters.pop(old)
            new = old + delta
            if new <= 0:
                # 把刚才 pop 掉的放回去再抛错，避免破坏 dict
                chapters[old] = work
                raise ValueError(f"renumber 后章号 {new} <= 0，非法")
            work.no = new
            chapters[new] = work

    def insert_after(self, after_no: int, *, title: str, field_no: int | None = None,
                     chapter_kind: str = "dungeon") -> int:
        _valid_kinds = ("mainline", "dungeon", "interlude")
        kind = chapter_kind if chapter_kind in _valid_kinds else "dungeon"
        self._backup()
        if after_no == 0:
            self.renumber(1, +1)
            new_no = 1
        else:
            self.renumber(after_no + 1, +1)
            new_no = after_no + 1
        chapters = self._load()
        chapters[new_no] = ChapterWork(
            no=new_no, title=title, field_no=field_no,
            chapter_kind=kind, is_interlude=(kind == "interlude"),
        )
        self._flush()
        return new_no

    def delete(self, no: int, *, force: bool = False) -> None:
        chapters = self._load()
        if no not in chapters:
            raise ValueError(f"第 {no} 章不存在")
        work = chapters[no]
        if work.finalized_at and not force:
            raise ValueError(f"第 {no} 章已定稿，需 force=True 才能删除")
        self._backup()
        del chapters[no]
        self._flush()

    def move(self, no: int, *, new_field_no: int | None = None,
             new_chapter_kind: str | None = None,
             new_day: int | None = None) -> ChapterWork:
        chapters = self._load()
        if no not in chapters:
            raise ValueError(f"第 {no} 章不存在")
        _valid_kinds = ("mainline", "dungeon", "interlude")
        if new_chapter_kind is not None and new_chapter_kind not in _valid_kinds:
            raise ValueError(f"未知章节类型：{new_chapter_kind}（可选 {_valid_kinds}）")
        self._backup()
        work = chapters[no]
        if new_field_no is not None:
            work.field_no = new_field_no
        if new_chapter_kind is not None:
            work.chapter_kind = new_chapter_kind
            work.is_interlude = (new_chapter_kind == "interlude")
            if new_chapter_kind == "interlude":
                work.day = None
        # new_day 显式传入才覆盖；interlude 模式已在上面清 day
        if new_day is not None and work.chapter_kind != "interlude":
            work.day = new_day if 1 <= new_day <= 7 else None
        self._flush()
        return work
