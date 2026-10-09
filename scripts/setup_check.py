#!/usr/bin/env python3
"""环境自检（Windows / macOS 通用）：工具链、trae-cli 解析、配置完整性、MiniMax Key。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OK, WARN, FAIL = "[OK]  ", "[WARN]", "[FAIL]"


def run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace")
        return r.stdout.strip() or r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    failed = False
    print(f"== Novel Studio 环境自检 ({sys.platform}) ==\n")

    # Python
    v = sys.version_info
    print(f"{OK if v >= (3, 10) else FAIL} Python {v.major}.{v.minor}.{v.micro}"
          + ("" if v >= (3, 10) else "（需要 >= 3.10）"))
    failed |= v < (3, 10)

    # uv / node / pnpm（可选但推荐）
    for tool, label in (("uv", "uv（Python 依赖管理）"),
                        ("node", "Node（前端构建）"),
                        ("pnpm", "pnpm（前端包管理）")):
        ver = run([tool, "--version"]) if shutil.which(tool) else None
        print(f"{OK if ver else WARN} {label}: {ver or '未安装（前端阶段才需要）'}")

    # git
    print(f"{OK if shutil.which('git') else FAIL} git: {run(['git', '--version']) or '未安装'}")

    # trae-cli 解析
    sys.path.insert(0, str(REPO / "spike"))
    try:
        from trae_spike import resolve_trae_cli
        binary = resolve_trae_cli()
        ver = run([binary, "--version"]) or "?"
        print(f"{OK} trae-cli: {binary}  ({ver})")
    except SystemExit as e:
        print(f"{FAIL} trae-cli: {e}")
        failed = True

    # 配置文件
    local = REPO / "config.local.yaml"
    if local.exists():
        print(f"{OK} config.local.yaml 存在（不会提交）")
        import yaml
        cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        key = ((cfg.get("providers") or {}).get("minimax") or {}).get("api_key", "")
        print(f"{OK if key and '在此填入' not in key else WARN} "
              f"MiniMax api_key: {'已配置 ' + key[:8] + '...' if key else '未配置'}")
    else:
        print(f"{WARN} config.local.yaml 不存在（cp config.example.yaml config.local.yaml）")

    # 后端 venv
    venv_py = (REPO / "backend/.venv/Scripts/python.exe" if sys.platform == "win32"
               else REPO / "backend/.venv/bin/python")
    print(f"{OK if venv_py.exists() else WARN} 后端 venv: "
          f"{'存在 ' + str(venv_py) if venv_py.exists() else '未创建（cd backend && uv sync）'}")

    print("\n结论:", "存在失败项，请先处理" if failed else "基础环境就绪")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
