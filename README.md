# Novel Studio · 长篇小说工坊

人机协同的长篇网文创作工作台。融合 Long-Novel-GPT 的分层交互骨架、ainovel-cli 的质量内核、知识图谱式世界观管理。

- 调研底稿：`../长篇小说生成器-深度调研报告.html`
- 开发计划：`../novel-studio-开发计划.html`

## 架构一览

```
Vue3 工作台 ── REST/SSE ── FastAPI 编排后端 ── Provider 层 ──┬─ trae-cli ACP（主力：流式+会话保温，公司成本）
                                                            ├─ trae-cli exec（批处理兜底）
                                                            └─ MiniMax API（调试/杂活，个人账号）
                                      存储层：Obsidian Vault（纯 Markdown，即事实层）
```

## 环境准备（Windows / macOS 通用）

前置：Python ≥ 3.10、uv（推荐）、Node 20+ 与 pnpm（前端阶段才需要）、TraeCode CLI 并完成企业登录。

```bash
# 1. 本机配置（Key 不入库）
cp config.example.yaml config.local.yaml   # Windows 用 copy，然后填入 MiniMax api_key

# 2. 环境自检（含 trae-cli 解析验证）
python scripts/setup_check.py

# 3. 后端依赖
cd backend && uv sync && cd ..

# 4. Spike 验证（首次必跑）
backend/.venv/Scripts/python spike/trae_spike.py --json      # macOS: backend/.venv/bin/python
backend/.venv/Scripts/python spike/minimax_spike.py          # MiniMax 端点探测 + 流式验证
backend/.venv/Scripts/python spike/acp_spike.py              # ACP 长连接 + 流式 + 会话保温
```

## 目录

```
backend/    FastAPI 编排后端（providers / pipelines / context / quality / vault / prompts / api）
frontend/   Vue 3 + TS 工作台（M1 阶段充实）
spike/      技术验证脚本（trae exec / ACP / MiniMax）
vault-template/  新书 Obsidian Vault 骨架
```

## 安全约定

- `config.local.yaml` / `.env` 永不提交；Key 疑似泄露立即在供应商后台轮换
- 后端是 Vault 的唯一写者；Obsidian 侧改动走 SyncService 接纳
