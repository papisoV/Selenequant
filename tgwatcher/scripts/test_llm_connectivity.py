"""30-second LLM connectivity test — bypasses the full signal pipeline.

Directly invokes SignalLLMClient.refine() with a fake message, prints the
validated factor dict. Verifies the active provider in config.yaml is
actually reachable end-to-end.

Usage:
    python test_llm_connectivity.py
    python test_llm_connectivity.py --provider astron   # override active provider
"""
import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

from tgwatcher.signal_llm import LLMConfig, SignalLLMClient, LLMRefineError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("llm-test")

CONFIG_PATH = Path.cwd() / "config.yaml"

FAKE_MESSAGE = "比特币突破10万美元历史新高，机构资金持续流入，ETF通过带来大量买盘。"

FAKE_PRELIMINARY = {
    "matched_categories": ["bullish", "event_macro", "event_listing"],
    "estimated_urgency": "high",
}


def load_llm_config(override_provider: str | None = None) -> LLMConfig:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    llm_cfg = cfg.get("signal", {}).get("llm", {})
    if override_provider:
        llm_cfg = {**llm_cfg, "provider": override_provider}
    return LLMConfig.from_dict(llm_cfg)


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM connectivity test")
    parser.add_argument("--provider", default=None, help="Override active provider")
    args = parser.parse_args()

    print(f"Config: {CONFIG_PATH}")
    try:
        llm_config = load_llm_config(args.provider)
    except ValueError as e:
        print(f"[FAIL] Config error: {e}")
        return 2

    print(f"Provider: {llm_config.provider}")
    print(f"Model:    {llm_config.model}")
    print(f"Base URL: {llm_config.base_url}")
    print(f"API key:  {llm_config.api_key[:8]}...{llm_config.api_key[-4:]}" if llm_config.api_key else "API key: (empty)")
    print()

    print("Sending test message:")
    print(f"  {FAKE_MESSAGE}")
    print()

    client = SignalLLMClient(llm_config)
    t0 = time.time()
    try:
        result = client.refine(FAKE_MESSAGE, FAKE_PRELIMINARY)
    except LLMRefineError as e:
        elapsed = time.time() - t0
        print(f"[FAIL] LLM refine failed after {elapsed:.1f}s: {e}")
        return 1
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[FAIL] Unexpected error after {elapsed:.1f}s: {type(e).__name__}: {e}")
        return 1

    elapsed = time.time() - t0
    print(f"[OK] Response in {elapsed:.1f}s:")
    for k in ("symbols", "direction", "magnitude", "urgency", "confidence",
              "halflife_min", "event_type"):
        print(f"  {k:15}: {result.get(k)}")
    print(f"  {'reasoning':15}: {result.get('reasoning', '')[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
