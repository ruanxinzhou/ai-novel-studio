# SDD Ledger: 蓝星开篇卷 + 全局写作台 + 4000-5000字/章

- Plan: `docs/superpowers/plans/2026-10-19-mainline-volume-and-writing-desk.md`
- Spec: `docs/superpowers/specs/2026-10-19-mainline-volume-and-writing-desk-design.md`
- BASE commit: `e4f8bfe`
- Start: 2026-10-19

## Tasks

| # | Title | Status | Commit | Notes |
|---|-------|--------|--------|-------|
| 0 | SDD setup (pre-flight + BASE) | ✅ | e4f8bfe | craft/engine/prompts 字数+黄金三章已提交 |
| 1 | Volume/ChapterWork 数据模型扩展 | ✅ | 0af6634 | - |
| 2 | DeskStore 章节管理方法 | ✅ | 2a9e1d0 | - |
| 3 | EntityStore.shift_chapter_refs | ✅ | aaf3be6 | - |
| 4 | REST API (desk_routes + mainline_routes + POST /volumes) | ✅ | 253677d | - |
| 5 | rhythm 字数+day=0蓝星+start_chapter_no偏移+brief advance | ✅ | 8cb1c1a | - |
| 6 | 写作材料按 chapter_kind 分支注入 | ✅ | c30f559 | - |
| 7 | phase 判定 + writing-desk + 迁移脚本 | ✅ | (合入 Task 4-6) | - |
| 8 | 字数全链路扫尾 | ✅ | 03196c6 | 后端无残留默认章节字数；engine SPECS plot=1800/draft=4800/polish=5000；prompts draft 要求 4000-5000；前端 DeskView/VolumeReviewPanel 字数阈值 <3500 黄 / 4000-5000 绿 / >5500 橙；pytest 175 核心用例全绿 |
| 9 | 前端 ChapterEditor + WritingDeskView + MainlineLanding + NewVolumeDialog | ✅ | 77e6947 | api.ts 扩展 deskApi.spine/insertChapter/deleteChapter/patchChapter + volumesApi.create + mainlineApi；新建 WritingDeskView（左栏树+DeskView 嵌入）/MainlineLandingView（蓝星开篇引导）/NewVolumeDialog（新建卷对话框）；vite build 通过 |
| 10 | SpineNav + HomeView 接入 | ⏳ pending | - | - |
| 11 | 重启 + e2e + push | ⏳ pending | - | - |

## Global Conventions

- Git path: `C:\Program Files\Git\cmd\git.exe` (set PATH at start of every command)
- Python tests: `cd backend ; python -m pytest tests/test_wizard.py -v` (or specific test class)
- 所有写 desk/entities/volumes 必须先 .bak.TIMESTAMP 备份
- chapter_kind ∈ {mainline, dungeon, interlude}，老数据无此字段按 field_no→volume.volume_kind 回填
- volume_kind ∈ {mainline, dungeon, interlude}，老数据默认 dungeon
- Volume.start_chapter_no: int = 1；rhythm LLM 输出的 chapter 是卷内相对号，落盘 +(start_chapter_no-1)
- 每章 target_chars：plot=1800 / draft=4800 / polish=5000；LLM 指令硬卡 4000-5000
- 前端不引入新 npm 包；拖拽改序本版返回 501（按钮置灰）
