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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
class ChapterWork:
    no: int
    title: str = ""
    plot: dict = field(default_factory=dict)     # {text, run_id, updated_at}
    draft: dict = field(default_factory=dict)
    polish: dict = field(default_factory=dict)
    review: dict | None = None                   # review_chapter().as_dict()
    finalized_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterWork":
        return cls(
            no=int(d["no"]), title=str(d.get("title", "") or ""),
            plot=dict(d.get("plot") or {}), draft=dict(d.get("draft") or {}),
            polish=dict(d.get("polish") or {}), review=d.get("review"),
            finalized_at=str(d.get("finalized_at", "") or ""),
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

    def ensure(self, no: int, title: str = "") -> ChapterWork:
        chapters = self._load()
        if no not in chapters:
            chapters[no] = ChapterWork(no=no)
        if title and not chapters[no].title:
            chapters[no].title = title
        return chapters[no]

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

    def mark_finalized(self, no: int) -> ChapterWork:
        work = self.ensure(no)
        work.finalized_at = _now()
        self._flush()
        return work
