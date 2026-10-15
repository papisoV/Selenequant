"""Extended (Starknet) perpetual DEX adapter package."""

from selene_space_exchange.dex.extended.adapter import ExtendedTradingAdapter
from selene_space_exchange.dex.extended.config import ExtendedConfig
from selene_space_exchange.dex.extended.stark_crypto import (
    align_to_step,
    order_msg_hash,
    parse_dec,
    poseidon_hash_many,
    public_key_from_private,
    self_test,
    settlement_amounts,
    stark_sign,
    to_hex,
)

__all__ = [
    "ExtendedConfig",
    "ExtendedTradingAdapter",
    "align_to_step",
    "order_msg_hash",
    "parse_dec",
    "poseidon_hash_many",
    "public_key_from_private",
    "self_test",
    "settlement_amounts",
    "stark_sign",
    "to_hex",
]
