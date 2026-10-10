"""拆书工具单测：python test_chaishu.py（零依赖，unittest）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import chaishu
import render


def mk_ch(no: int, chars: int = 4000, volume: str = "正文") -> dict:
    return {"no": no, "title": f"第{no}章", "volume": volume, "body": "x" * chars, "chars": chars}


class TestSplitBook(unittest.TestCase):
    def test_arabic_and_cn(self):
        text = "第一章 觉醒\n正文A\n第12章 进城\n正文B\n第一百二十三章 大战\n正文C"
        out = chaishu.split_book(text)
        self.assertEqual([c["no"] for c in out], [1, 12, 123])
        self.assertEqual(out[1]["title"], "进城")
        self.assertEqual(out[2]["no"], 123)

    def test_volume_head_belongs_to_first_chapter(self):
        text = "第一章 起\n正文A\n第二章 承\n正文B\n第二卷 风云\n第三章 转\n正文C\n第四章 合\n正文D"
        out = chaishu.split_book(text)
        by_no = {c["no"]: c for c in out}
        self.assertEqual(by_no[2]["volume"], "正文")
        self.assertEqual(by_no[3]["volume"], "第二卷 风云")
        self.assertEqual(by_no[4]["volume"], "第二卷 风云")  # 后续章继承卷名

    def test_no_labels_whole_text(self):
        out = chaishu.split_book("就是一段没有任何章节标记的正文。")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["no"], 0)

    def test_cn_num(self):
        self.assertEqual(chaishu.cn_num_to_int("一百二十三"), 123)
        self.assertEqual(chaishu.cn_num_to_int("一万零一"), 10001)
        self.assertEqual(chaishu.cn_num_to_int("99"), 99)


class TestSample(unittest.TestCase):
    def test_full_when_limit_ge(self):
        self.assertEqual(chaishu.stratified_sample(10, 20), list(range(10)))

    def test_layered(self):
        idx = chaishu.stratified_sample(300, 20)
        self.assertEqual(len(idx), 20)
        self.assertEqual(idx, sorted(set(idx)))
        self.assertLess(min(idx), 30)      # 前段有样本
        self.assertGreater(max(idx), 270)  # 后段有样本
        mid = [i for i in idx if 90 <= i <= 210]
        self.assertTrue(mid)               # 中段有样本


class TestParseLabel(unittest.TestCase):
    def test_plain_json(self):
        obj = chaishu._parse_label('{"summary":"主角进城","hook":true,"power_up":false,"events":[{"type":"shuangdian","note":"打脸"}]}')
        self.assertEqual(obj["summary"], "主角进城")
        self.assertTrue(obj["hook"])
        self.assertEqual(obj["events"][0]["type"], "shuangdian")

    def test_fenced_json(self):
        raw = "```json\n{\"summary\":\"测试\",\"hook\":false,\"power_up\":false,\"events\":[]}\n```"
        obj = chaishu._parse_label(raw)
        self.assertEqual(obj["summary"], "测试")

    def test_truncated_repair(self):
        raw = '{"summary":"修仙者渡劫","hook":true,"power_up":true,"events":[{"type":"upgrade","note":"突破'
        obj = chaishu._parse_label(raw)
        self.assertEqual(obj["summary"], "修仙者渡劫")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            chaishu._parse_label("这不是JSON")


class TestCollectAndRender(unittest.TestCase):
    def setUp(self):
        self.chapters = [mk_ch(i) for i in range(1, 31)]
        self.labels = {}
        for i in range(1, 31):
            evs = []
            if i % 3 == 0:
                evs.append({"type": "shuangdian", "note": "爽"})
            if i == 2:
                evs.append({"type": "foreshadow", "note": "神秘老者"})
            self.labels[i] = {"no": i, "summary": f"第{i}章概要", "hook": i % 2 == 0,
                              "power_up": i % 10 == 0, "events": evs}

    def test_collect(self):
        s = render.collect(self.chapters, self.labels)
        self.assertEqual(s["n_chapters"], 30)
        self.assertEqual(s["n_labeled"], 30)
        self.assertEqual(s["shuang_gaps"], [3] * 9)
        self.assertEqual(s["hook_rate"], 15)
        self.assertEqual(_median_int(s["upgrade_gaps"]), 10)
        self.assertEqual(s["buildup"], [])  # 每3章一爽，无蓄力段
        self.assertEqual(s["foreshadow_gaps"], [1])  # 第2章埋，第3章回收

    def test_buildup_detection(self):
        labels = {}
        for i in range(1, 31):
            evs = [{"type": "shuangdian", "note": "爽"}] if i in (1, 10, 20, 30) else []
            labels[i] = {"no": i, "summary": "", "hook": False, "power_up": False, "events": evs}
        s = render.collect(self.chapters, labels)
        self.assertEqual(s["buildup"], [(2, 9), (11, 19), (21, 29)])
        self.assertEqual(s["burst_dist"], [1, 1, 1])

    def test_report_redline(self):
        s = render.collect(self.chapters, self.labels)
        rpt = render.render_report("测试书", s, False)
        self.assertIn("爽点章距", rpt)
        self.assertIn("断章钩子率", rpt)
        self.assertNotIn("神秘老者", rpt)   # 红线：情节词不入报告
        self.assertNotIn("第2章概要", rpt)

    def test_arc_contains_summary(self):
        arc = render.render_arc("测试书", self.chapters, self.labels)
        self.assertIn("第5章概要", arc)
        self.assertIn("神秘老者", arc)      # arc 允许情节（作者自用）

    def test_report_without_labels(self):
        s = render.collect(self.chapters, {})
        rpt = render.render_report("测试书", s, False)
        self.assertIn("总量与体格", rpt)
        self.assertIn("语义层未跑", rpt)

    def test_render_all_end_to_end(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            render.render_all(out, "测试书", self.chapters, self.labels,
                              sampled=None, skip_diagnose=True)
            self.assertTrue((out / "report.md").exists())
            self.assertTrue((out / "arc.md").exists())
            rpt = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("断章钩子率", rpt)
            self.assertIn("（--skip-diagnose 已跳过）", rpt)


class TestReceipts(unittest.TestCase):
    def test_load_tolerates_bad_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "labels.jsonl"
            f.write_text('{"no":1,"summary":"a"}\n坏行\n{"no":2,"sum', encoding="utf-8")
            done = chaishu.load_receipts(f)
            self.assertEqual(list(done), [1])


def _median_int(values):
    return render._median(values)


if __name__ == "__main__":
    unittest.main(verbosity=2)
