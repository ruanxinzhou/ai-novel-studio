"""小说项目（M3a）：一本小说 = 一个自包含目录 + 五层版本链。

布局：
    backend/data/projects/{pid}/
        project.json                  # 元数据 + 各层当前版本指针
        versions/{layer}/v0001.json   # 每次采纳一个版本（x_pairs/y/note）
        markers.json                  # 该项目的必现标记（M2 复用）
        runs/                         # 该项目的断点收据

流程：生成（沿用断点收据/模块/范文注入）→ 人工编辑 → 采纳（adopt，
版本留痕）→ 下一层。current 指针永远指向最新采纳版本；历史可回看。
孤儿版本无害：先写版本文件再更新索引，崩溃最多多一个未入索引文件。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

LAYERS = ["intro", "outline", "plot", "draft", "polish"]


@dataclass
class LayerVersion:
    seq: int
    layer: str
    x_pairs: list[tuple[str, str]]
    y: str
    note: str = ""
    timestamp: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["x_pairs"] = [list(p) for p in self.x_pairs]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LayerVersion":
        return cls(
            seq=d["seq"], layer=d["layer"],
            x_pairs=[tuple(p) for p in d.get("x_pairs", [])],
            y=d.get("y", ""), note=d.get("note", ""),
            timestamp=d.get("timestamp", ""),
        )

    def as_summary(self) -> dict:
        return {
            "seq": self.seq, "layer": self.layer, "note": self.note,
            "timestamp": self.timestamp, "y_chars": len(self.y),
            "x_preview": (self.x_pairs[0][0][:50] if self.x_pairs else ""),
        }


@dataclass
class ProjectMeta:
    id: str
    title: str
    intro: str = ""
    kind: str = "own"        # own=自己的小说项目；reference=拆书参考项目（仅借鉴，不写作）
    created_at: str = ""
    updated_at: str = ""
    current: dict[str, int] = field(default_factory=dict)   # layer -> seq
    layer_seqs: dict[str, list[int]] = field(default_factory=dict)  # layer -> [seq...]

    def as_dict(self, with_layers: bool = False) -> dict:
        d = asdict(self)
        if not with_layers:
            d.pop("layer_seqs")
        return d


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ProjectStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 路径 ----

    def _dir(self, pid: str) -> Path:
        return self.root / pid

    def _meta_path(self, pid: str) -> Path:
        return self._dir(pid) / "project.json"

    def _version_path(self, pid: str, layer: str, seq: int) -> Path:
        return self._dir(pid) / "versions" / layer / f"v{seq:04d}.json"

    # ---- CRUD ----

    def create(self, title: str, intro: str = "", kind: str = "own") -> ProjectMeta:
        title = title.strip()
        if not title:
            raise ValueError("书名不能为空")
        pid = f"{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        meta = ProjectMeta(id=pid, title=title, intro=intro.strip(), kind=kind,
                           created_at=_now(), updated_at=_now())
        self._dir(pid).mkdir(parents=True, exist_ok=True)
        self._write_meta(meta)
        return meta

    def find_by_title(self, title: str, kind: str | None = None) -> ProjectMeta | None:
        """按书名找项目（拆书重复导入复用同一参考项目）；kind 限定类型。"""
        title = title.strip()
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            try:
                meta = self._read_meta(d.name)
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if meta.title == title and (kind is None or meta.kind == kind):
                return meta
        return None

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            try:
                meta = self._read_meta(d.name)
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            out.append(meta.as_dict())
        return out

    def get(self, pid: str) -> ProjectMeta:
        return self._read_meta(pid)

    def update(self, pid: str, title: str | None = None, intro: str | None = None) -> ProjectMeta:
        meta = self._read_meta(pid)
        if title is not None:
            if not title.strip():
                raise ValueError("书名不能为空")
            meta.title = title.strip()
        if intro is not None:
            meta.intro = intro.strip()
        meta.updated_at = _now()
        self._write_meta(meta)
        return meta

    def delete(self, pid: str) -> None:
        import shutil
        d = self._dir(pid)
        if not d.exists():
            raise FileNotFoundError(f"项目不存在：{pid}")
        shutil.rmtree(d)

    # ---- 版本链 ----

    def adopt(self, pid: str, layer: str, x_pairs: list[tuple[str, str]],
              y: str, note: str = "") -> LayerVersion:
        """采纳一层产出：写入新版本并把 current 指向它。"""
        if layer not in LAYERS:
            raise ValueError(f"未知层：{layer}")
        meta = self._read_meta(pid)
        seq = max(meta.layer_seqs.get(layer, [0])) + 1
        version = LayerVersion(seq=seq, layer=layer, x_pairs=x_pairs, y=y,
                               note=note, timestamp=_now())
        path = self._version_path(pid, layer, seq)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(version.as_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)
        meta.layer_seqs.setdefault(layer, []).append(seq)
        meta.current[layer] = seq
        meta.updated_at = _now()
        self._write_meta(meta)
        return version

    def current(self, pid: str, layer: str) -> LayerVersion | None:
        meta = self._read_meta(pid)
        seq = meta.current.get(layer)
        return self._load_version(pid, layer, seq) if seq else None

    def version(self, pid: str, layer: str, seq: int) -> LayerVersion:
        v = self._load_version(pid, layer, seq)
        if v is None:
            raise FileNotFoundError(f"版本不存在：{layer} v{seq}")
        return v

    def history(self, pid: str, layer: str) -> list[LayerVersion]:
        meta = self._read_meta(pid)
        out = []
        for seq in meta.layer_seqs.get(layer, []):
            v = self._load_version(pid, layer, seq)
            if v is not None:
                out.append(v)
        return out

    def rollback(self, pid: str, layer: str, seq: int) -> LayerVersion:
        """回滚 = 把历史版本原样采纳为新版本（不改写历史，可再滚回来）。"""
        v = self.version(pid, layer, seq)
        return self.adopt(pid, layer, v.x_pairs, v.y, note=f"回滚自 v{seq}")

    # ---- 内部 ----

    def _read_meta(self, pid: str) -> ProjectMeta:
        data = json.loads(self._meta_path(pid).read_text(encoding="utf-8"))
        return ProjectMeta(
            id=data["id"], title=data["title"], intro=data.get("intro", ""),
            kind=data.get("kind", "own"),
            created_at=data.get("created_at", ""), updated_at=data.get("updated_at", ""),
            current=data.get("current", {}), layer_seqs=data.get("layer_seqs", {}),
        )

    def _write_meta(self, meta: ProjectMeta) -> None:
        self._meta_path(meta.id).parent.mkdir(parents=True, exist_ok=True)
        tmp = self._meta_path(meta.id).with_suffix(".tmp")
        tmp.write_text(json.dumps(meta.as_dict(with_layers=True), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._meta_path(meta.id))

    def _load_version(self, pid: str, layer: str, seq: int) -> LayerVersion | None:
        path = self._version_path(pid, layer, seq)
        if not path.exists():
            return None
        return LayerVersion.from_dict(json.loads(path.read_text(encoding="utf-8")))
