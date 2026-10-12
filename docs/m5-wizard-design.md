# M5 技术方案：引导式写作（主路向导 + 今日章台）

> 依据：2026-10-09 圆桌讨论结论（存档 `notes/20261009T165252--圆桌-novel-studio好用化__roundtable.org`）。
> 全局宪法：**机器提议 · 来源标注 · 冲突待核 · 作者签收**。本方案所有生成均为候选，采纳必须由作者动作触发（复用现有 adopt 版本链）。

## 1. 目标

解决「运行台看不懂、对非技术作者不友好」：主路走分步引导，连载期走今日章台，运行台与创作导航降级为高级侧门保留。

双时间尺度：
- **开书期**：六站向导（选题 → 脑洞 → 架构 → 剧情 → 成稿 → 润色），走一次即毕业；
- **连载期**：今日章台单章微循环（剧情 → 成稿 → 审稿 → 润色 → 定稿飞轮），日更天天循环。

## 2. 六站定义与完成判据（数据推导，不存死状态）

| 站 | 名称 | 输入 | 产出落点 | 完成判据 |
|---|---|---|---|---|
| topic | 选题 | 情境/欲望/阻碍三格 + 一句话点子 | AI 扩写简介 → 采纳 intro 层 | intro 层有已采纳版本 |
| brainstorm | 脑洞 | 种子（类型/主角/爽点） | 5 张方向卡（名+理由+梗概），选/改/自写 | direction 非空或 skipped |
| architecture | 架构 | 章数 + 方向卡 | 全书大纲 → 采纳 outline 层 | outline 层有已采纳版本 |
| plotplan | 剧情 | 第 1 章大纲 | 章台生成首章剧情 | desk 第 1 章 plot 非空 |
| writing | 成稿 | 首章剧情 | 首章正文 + 审稿 | desk 第 1 章 draft 非空且 review verdict=pass |
| polish | 润色 | 首章正文 | 润色 + 定稿飞轮 | desk 第 1 章 finalized |

规则：
- **开工判据**（三格）：topic 站要求情境/欲望/阻碍三格齐（或显式跳过）才放行后续站；
- 站点状态 = 由项目数据实时推导（intro/outline 版本 + desk 记录 + skipped 列表），**当前站 = 第一个未完成且未跳过的站**，永不失同步；
- 作者可跳站（侧门原则），跳过的站标黄色提醒，可随时回来补。

## 3. 后端设计

### 3.1 新增模块 `app/wizard/`

**`store.py` — WizardStore + DeskStore（纯簿记，JSON 持久化）**

```
backend/data/projects/{pid}/wizard.json    # idea{context,desire,obstacle,text}, seed, direction, n_chapters, skipped[], graduated_at
backend/data/projects/{pid}/desk.json      # chapters{no: {title, plot{...}, draft{...}, polish{...}, review{...}, finalized_at}}
```

每章工作记录：`{text, run_id, updated_at}`；review 存 `review_chapter().as_dict()`。

**`brainstorm.py` — 方向卡生成**

- 走 chore 便宜通道（模型路由三问：杂活不烧贵模型）；
- 新增 PromptStore 层 `brainstorm`（可进工坊编辑），X 头「种子」；
- 输出 JSON 数组 `[{"direction","reason","hook","premise"}]`，用 `parse_repaired_json` 修复，解析失败整体重试 1 次，仍失败 502。

**`desk.py` — 单章微循环编排（复用现有引擎，不重造）**

`run_chapter_stage()`：
| stage | x | materials | 特殊 |
|---|---|---|---|
| plot | 该章大纲文本（outline 拆章） | intro FINALIZED + direction REFERENCE | x_chunk_length=章大纲长度 |
| draft | desk 中本章剧情 | 本章大纲 FINALIZED + direction REFERENCE | 滚动锚点=上一章结尾500字；注入本章 pending 必现标记 |
| polish | desk 中本章正文 | — | — |

幂等：`compute_run_key` + `RunJournal`（项目隔离）；`force=True` 时 journal=None 绕过收据重生成。
审稿：复用 `review_chapter`（七项清单，第七项跨章联结对照上一章结尾），结果入 desk。
`finalize_chapter()`（保鲜飞轮）：
1. 定稿文本 = polish 优先，否则 draft；
2. 飞轮：`extract_entities`（LLM 抽取实体，可关）+ `markers.verify` 标记核验；
3. 整本合成：全部章按号取最新文本（polish > draft）拼成全书正文，采纳为新 draft 层版本（保持 Vault 导出兼容，版本链留痕）；
4. desk 记录 finalized_at，返回三十秒校正清单（新增/更新实体、标记核验结果）。

### 3.2 新增 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects/{pid}/wizard` | 六站状态（数据推导）+ 向导数据 |
| PUT | `/api/projects/{pid}/wizard` | 写 idea/seed/direction/n_chapters/skipped |
| POST | `/api/projects/{pid}/wizard/brainstorm` | 生成 5 张方向卡 |
| GET | `/api/projects/{pid}/desk` | 章台总览（章来源 = outline 拆章 ∪ desk ∪ draft 拆章） |
| POST | `/api/projects/{pid}/desk/{chapter}/run` | `{stage, directive?, force?}` 单章生成 |
| POST | `/api/projects/{pid}/desk/{chapter}/review` | 单章审稿 |
| POST | `/api/projects/{pid}/desk/{chapter}/finalize` | 定稿 + 飞轮 `{auto_entities}` |

`GET /api/projects` 响应附加 `wizard_summary`（六站完成数/毕业/章台定稿进度），项目卡进度条免 N+1 请求。

## 4. 前端设计

- **App.vue**：tab 顺序调整为 项目管理 / **创作向导** / **今日章台** / 创作导航 / 实体库 / 导入拆解 / 审稿与标记 / 运行台 / Prompt 工坊 / 写作模块 / 范文语料；
- **WizardView.vue**（创作向导）：el-steps 六站条（当前站高亮、跳过站黄色）+ 每站面板；
  - 每站同构三出口：选 AI 的 / 改 AI 的 / 自己写（自写不藏，一级公民）；
  - topic：三格 + 点子 → AI 扩写简介 → 编辑 → 采纳；
  - brainstorm：种子 → 5 张方向卡（附理由）→ 选/改/自写；
  - architecture：章数 → 生成大纲 → 编辑 → 采纳；
  - plotplan/writing/polish：显示首章进度 + 「去今日章台」按钮；
  - 毕业：完成面板 + 进入章台。
- **DeskView.vue**（今日章台）：左侧章列表（四态标签：剧情/正文/审稿/定稿），右侧单章工作区：逐步生成按钮 + 每步编辑框 + 审稿七项结果 + 定稿飞轮报告；定稿后自动定位下一章（明日章）；
- **ProjectsView.vue**：项目卡进度条改为六站进度 + 章台定稿进度（x/y 章）；
- **api.ts**：新增 `wizardApi` + `deskApi`。

## 5. 明确不做（后续里程碑）

- 全书脉搏漂移盘（需多章数据积累后做统计）；
- 基线双册（声音册/结构册）；
- 夜里错峰批量生成（定时任务）；
- 圈段定向返工（段级重生成）。

## 6. 测试

- 单元：WizardStore/DeskStore 簿记（持久化、判据推导、跳站）、finalize 整本合成；
- API：mock Router（方向卡生成/审稿/实体抽取/finalize 飞轮）、幂等重跑、force 重生成；
- 全量 pytest + 前端 build + urllib 冒烟。
