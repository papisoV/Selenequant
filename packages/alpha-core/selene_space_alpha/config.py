"""Alpha-core configuration."""

import logging
import os

import torch

logger = logging.getLogger(__name__)

# Torch intra-op threads for the mining workload. Measured default of 16
# (torch's cpu-count default) is both slower in wall-clock AND ~11x more CPU
# than 4 on a 256-batch REINFORCE step: the hot loop is per-sample pure Python
# (StackVM + MemeBacktest), where torch's parallel regions cannot help and the
# thread pool merely oversubscribes. 4 was the fastest measured cap.
_DEFAULT_TORCH_NUM_THREADS = 4
_TORCH_THREADS_ENV = "SELENE_ALPHA_TORCH_THREADS"


def resolve_torch_num_threads(override: int | None = None) -> int:
    """Resolve the torch intra-op thread cap.

    Priority: explicit ``override`` > ``SELENE_ALPHA_TORCH_THREADS`` > default.
    Non-integer or < 1 values fall back to the default with a warning (0 is a
    config error, not "one thread").
    """
    if override is not None:
        try:
            value = int(override)
        except (TypeError, ValueError):
            value = -1
        if value >= 1:
            return value
        logger.warning(
            "Invalid TORCH_NUM_THREADS override %r; using default %d",
            override,
            _DEFAULT_TORCH_NUM_THREADS,
        )
        return _DEFAULT_TORCH_NUM_THREADS
    raw = os.environ.get(_TORCH_THREADS_ENV)
    if raw is None:
        return _DEFAULT_TORCH_NUM_THREADS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = -1
    if value >= 1:
        return value
    logger.warning(
        "Invalid %s=%r; using default %d",
        _TORCH_THREADS_ENV,
        raw,
        _DEFAULT_TORCH_NUM_THREADS,
    )
    return _DEFAULT_TORCH_NUM_THREADS


class AlphaCoreConfig:
    """Configuration for the alpha-core factor mining system."""

    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Training
    BATCH_SIZE = 8192
    TRAIN_STEPS = 1000
    MAX_FORMULA_LEN = 12

    # Torch intra-op threads (process-wide; applied by AlphaEngine.__init__).
    TORCH_NUM_THREADS = _DEFAULT_TORCH_NUM_THREADS

    # Backtest evaluation
    TRADE_SIZE_USD = 1000.0
    MIN_LIQUIDITY = 1000.0
    BASE_FEE = 0.001  # 0.1% (CEX taker fee, adjustable for DEX)

    # Database
    DB_URL: str = (
        f"postgresql://{os.getenv('DB_USER', 'postgres')}"
        f":{os.getenv('DB_PASSWORD', 'password')}"
        f"@{os.getenv('DB_HOST', 'localhost')}"
        f":5432/{os.getenv('DB_NAME', 'crypto_quant')}"
    )

    # Signal thresholds
    BUY_THRESHOLD = 0.85
    SELL_THRESHOLD = 0.45

    # LoRD regularization
    LORD_DECAY_RATE = 1e-3
    LORD_NUM_ITERATIONS = 5
    LORD_TARGET_KEYWORDS = ["q_proj", "k_proj", "attention", "qk_norm"]

    def __init__(
        self,
        TRAIN_STEPS: int | None = None,
        BATCH_SIZE: int | None = None,
        MAX_FORMULA_LEN: int | None = None,
        TORCH_NUM_THREADS: int | None = None,
    ) -> None:
        """Allow per-run overrides; defaults keep the class-level constants."""
        if TRAIN_STEPS is not None:
            self.TRAIN_STEPS = TRAIN_STEPS
        if BATCH_SIZE is not None:
            self.BATCH_SIZE = BATCH_SIZE
        if MAX_FORMULA_LEN is not None:
            self.MAX_FORMULA_LEN = MAX_FORMULA_LEN
        self.TORCH_NUM_THREADS = resolve_torch_num_threads(TORCH_NUM_THREADS)
