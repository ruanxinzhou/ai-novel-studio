"""真实通道冒烟：简介→大纲（2 次真实调用）。仅手动执行。"""
import sys
import time

sys.path.insert(0, ".")

from app.config import load_config
from app.generation import ModelLimits
from app.pipeline import SPECS, PipelineRunner
from app.providers.minimax import MiniMaxProvider


def main() -> None:
    cfg = load_config()
    provider = MiniMaxProvider(
        api_key=cfg.minimax.api_key,
        base_url=cfg.minimax.base_url,
        default_model=cfg.minimax.default_model,
    )
    runner = PipelineRunner(provider, model=cfg.minimax.default_model, limits=ModelLimits(200_000, 8_192))

    t0 = time.time()
    intro = runner.run_layer(SPECS["intro"], [("废土少年捡到一枚能吞噬怪物的黑色戒指，戒指里关着一个前代王者。", "")])
    print(f"=== 简介（{time.time()-t0:.1f}s, {len(intro.calls)} 次调用, 状态 {intro.calls[0].completion_status}）===")
    print(intro.y[:400])

    t0 = time.time()
    outline = runner.run_layer(
        SPECS["outline"], [(intro.y, "")], materials=[], user_directive="共 3 章。", x_chunk_length=len(intro.y)
    )
    print(f"\n=== 大纲（{time.time()-t0:.1f}s, {len(outline.calls)} 次调用, 状态 {outline.calls[0].completion_status}）===")
    print(outline.y[:600])
    print(f"\n[usage] 输入 {sum(c.decision.prompt_tokens for c in intro.calls + outline.calls)}tok(估)")


if __name__ == "__main__":
    main()
