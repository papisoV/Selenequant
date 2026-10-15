"""Alpha-core configuration."""

import os
import torch


class AlphaCoreConfig:
    """Configuration for the alpha-core factor mining system."""

    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Training
    BATCH_SIZE = 8192
    TRAIN_STEPS = 1000
    MAX_FORMULA_LEN = 12

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
    ) -> None:
        """Allow per-run overrides; defaults keep the class-level constants."""
        if TRAIN_STEPS is not None:
            self.TRAIN_STEPS = TRAIN_STEPS
        if BATCH_SIZE is not None:
            self.BATCH_SIZE = BATCH_SIZE
        if MAX_FORMULA_LEN is not None:
            self.MAX_FORMULA_LEN = MAX_FORMULA_LEN
