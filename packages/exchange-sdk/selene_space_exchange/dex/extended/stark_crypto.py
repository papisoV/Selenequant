"""Stark crypto for Extended (extended.exchange, Starknet perpetuals), zero deps.

Implements: Poseidon (Hades) hash, SNIP-12 (rev 1) order message hash, and
deterministic Stark-curve ECDSA (RFC 6979, StarkWare variant).

The construction is verified byte-for-byte against the official Extended
python SDK test vectors (tests/signing/order_object/test_limit_order_object.py).
``self_test()`` re-runs one official vector and must pass before live trading.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Sequence

# ---- field / curve constants ----

PRIME: int = 2**251 + 17 * 2**192 + 1
EC_ORDER: int = 0x0800000000000010FFFFFFFFFFFFFFFFB781126DCAE7B2321E66A241ADC64D2F
ALPHA: int = 1
EC_GEN: tuple[int, int] = (
    0x1EF15C18599971B7BECED415A40F0C7DEACFD9B0D1819E03D723D8BC943CFCA,
    0x5668060AA49730B7BE4801DF46EC62DE53ECD11ABE43A32873000C36E8DC1F,
)
TWO_251: int = 2**251

# SNIP-12 type hashes (= starknet_keccak of the type strings; verified against
# the on-chain contract constants in starkware-libs/starknet-perpetual).
ORDER_TYPE_HASH: int = 0x36DA8D51815527CABFAA9C982F564C80FA7429616739306036F1F9B608DD112
DOMAIN_TYPE_HASH: int = 0x1FF2F602E42168014D405A94F75E8A93D640751D71D16311266E140D8B0A210

# ---- poseidon (Hades permutation, starknet variant) ----
# Round constants: sha256("Hades" + i) mod PRIME (same as cairo-lang).

N_ROUNDS: int = 91  # 8 full + 83 partial


def _precompute_ark() -> list[list[int]]:
    """Precompute ARK round constants identically to the JS reference."""
    out: list[list[int]] = []
    for i in range(N_ROUNDS):
        row: list[int] = []
        for j in range(3):
            h = hashlib.sha256(f"Hades{3 * i + j}".encode()).digest()
            row.append(int.from_bytes(h, "big") % PRIME)
        out.append(row)
    return out


ARK: list[list[int]] = _precompute_ark()


def _hades(s0: int, s1: int, s2: int) -> tuple[int, int, int]:
    """Hades permutation: 91 rounds (full rounds 0-3 and 87-90, partial 4-86)."""
    for r in range(N_ROUNDS):
        a = ARK[r]
        s0 = (s0 + a[0]) % PRIME
        s1 = (s1 + a[1]) % PRIME
        s2 = (s2 + a[2]) % PRIME
        if r < 4 or r >= 87:  # full rounds
            s0 = (s0 * s0 % PRIME) * s0 % PRIME
            s1 = (s1 * s1 % PRIME) * s1 % PRIME
            s2 = (s2 * s2 % PRIME) * s2 % PRIME
        else:  # partial rounds
            s2 = (s2 * s2 % PRIME) * s2 % PRIME
        t0 = (3 * s0 + s1 + s2) % PRIME
        t1 = (s0 - s1 + s2 + PRIME) % PRIME
        t2 = (s0 + s1 - 2 * s2 + 2 * PRIME) % PRIME
        s0, s1, s2 = t0, t1, t2
    return s0, s1, s2


def poseidon_hash_many(values: Sequence[int]) -> int:
    """Poseidon hash over a sequence of field elements."""
    v = [((x % PRIME) + PRIME) % PRIME for x in values]
    v.append(1)
    if len(v) % 2:
        v.append(0)
    s0 = s1 = s2 = 0
    for i in range(0, len(v), 2):
        s0, s1, s2 = _hades((s0 + v[i]) % PRIME, (s1 + v[i + 1]) % PRIME, s2)
    return s0


def short_string(s: str) -> int:
    """Cairo short string -> felt (ASCII big-endian)."""
    v = 0
    for ch in s.encode("ascii"):
        v = (v << 8) + ch
    return v


# ---- modular / EC helpers ----


def _inv_mod(a: int, m: int) -> int:
    """Modular inverse via extended Euclidean algorithm."""
    old_r, r = ((a % m) + m) % m, m
    old_s, s = 1, 0
    while r != 0:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
    if old_r != 1:
        raise ValueError("not invertible")
    return ((old_s % m) + m) % m


def _ec_add(
    p1: tuple[int, int] | None, p2: tuple[int, int] | None
) -> tuple[int, int] | None:
    """Point addition on the Stark curve (y^2 = x^3 + alpha*x + beta)."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    if p1[0] == p2[0]:
        if (p1[1] + p2[1]) % PRIME == 0:
            return None
        m = (3 * p1[0] * p1[0] + ALPHA) % PRIME * _inv_mod(2 * p1[1], PRIME) % PRIME
    else:
        m = (p1[1] - p2[1] + PRIME) % PRIME * _inv_mod(
            (p1[0] - p2[0] + PRIME) % PRIME, PRIME
        ) % PRIME
    x = ((m * m - p1[0] - p2[0]) % PRIME + PRIME) % PRIME
    y = ((m * ((p1[0] - x + PRIME) % PRIME) - p1[1]) % PRIME + PRIME) % PRIME
    return (x, y)


def _ec_mult(k: int, pt: tuple[int, int]) -> tuple[int, int] | None:
    """Scalar multiplication via double-and-add."""
    res: tuple[int, int] | None = None
    while k > 0:
        if k & 1:
            res = _ec_add(res, pt)
        pt = _ec_add(pt, pt)  # type: ignore[arg-type]
        k >>= 1
    return res


def public_key_from_private(priv: int) -> int:
    """Public key (x coordinate) from a private key."""
    result = _ec_mult(priv, EC_GEN)
    if result is None:
        raise ValueError("scalar multiplication returned point at infinity")
    return result[0]


# ---- RFC 6979 deterministic k (StarkWare variant) ----


def _big_int_to_bytes(x: int, length: int | None = None) -> bytes:
    """Convert int to big-endian bytes, optionally zero-padded to *length*."""
    hex_str = format(x, "x")
    if len(hex_str) % 2:
        hex_str = "0" + hex_str
    buf = bytes.fromhex(hex_str)
    if length is not None:
        if len(buf) > length:
            raise ValueError("int too large")
        buf = b"\x00" * (length - len(buf)) + buf
    return buf


def _bytes_to_big_int(b: bytes) -> int:
    """Convert big-endian bytes to int."""
    return int.from_bytes(b, "big") if b else 0


def _bit_length(x: int) -> int:
    """Bit length of a non-negative integer."""
    return x.bit_length()


def _bits2int(b: bytes, qlen: int) -> int:
    """RFC 6979 bits2int: interpret byte string as integer, truncate to qlen bits."""
    x = _bytes_to_big_int(b)
    blen = len(b) * 8
    if blen > qlen:
        x >>= blen - qlen
    return x


def _generate_k_rfc6979(msg_hash: int, priv: int, seed: int | None = None) -> int:
    """RFC 6979 deterministic k generation (StarkWare variant).

    StarkWare quirk: pad one-nibble-short hashes (consistency with elliptic.js).
    """
    nbits = _bit_length(msg_hash)
    if 1 <= nbits % 8 <= 4 and nbits >= 248:
        msg_hash *= 16

    extra = b"" if seed is None else _big_int_to_bytes(seed)
    data = _big_int_to_bytes(msg_hash)  # minimal-length big-endian bytes
    qlen = 252
    rolen = 32

    def int2octets(x: int) -> bytes:
        return _big_int_to_bytes(x, rolen)

    def bits2octets(b: bytes) -> bytes:
        return int2octets(_bits2int(b, qlen) % EC_ORDER)

    V = b"\x01" * 32
    K = b"\x00" * 32

    def h(key: bytes, *parts: bytes) -> bytes:
        return hmac.new(key, b"".join(parts), digestmod=hashlib.sha256).digest()

    K = h(K, V, b"\x00", int2octets(priv), bits2octets(data), extra)
    V = h(K, V)
    K = h(K, V, b"\x01", int2octets(priv), bits2octets(data), extra)
    V = h(K, V)

    while True:
        T = b""
        while len(T) < rolen:
            V = h(K, V)
            T += V
        k = _bits2int(T, qlen)
        if 1 <= k < EC_ORDER:
            return k
        K = h(K, V, b"\x00")
        V = h(K, V)


def stark_sign(msg_hash: int, priv: int) -> dict[str, int]:
    """Deterministic Stark-curve ECDSA. Returns {r, s} as int."""
    if not (0 <= msg_hash < TWO_251):
        raise ValueError("message hash not signable")
    seed: int | None = None
    while True:
        k = _generate_k_rfc6979(msg_hash, priv, seed)
        seed = 1 if seed is None else seed + 1
        r_point = _ec_mult(k, EC_GEN)
        if r_point is None:
            continue
        r = r_point[0]
        if not (1 <= r < TWO_251):
            continue
        t = (msg_hash + r * priv) % EC_ORDER
        if t == 0:
            continue
        w = k * _inv_mod(t, EC_ORDER) % EC_ORDER
        if not (1 <= w < TWO_251):
            continue
        return {"r": r, "s": _inv_mod(w, EC_ORDER)}


# ---- SNIP-12 (rev 1) order message hash ----


def _to_felt(v: int) -> int:
    """Encode possibly-negative amount as a field element."""
    return ((v % PRIME) + PRIME) % PRIME


def domain_hash(
    name: str, version: str, chain_id: str, revision: int
) -> int:
    """Compute SNIP-12 domain hash."""
    return poseidon_hash_many(
        [
            DOMAIN_TYPE_HASH,
            short_string(name),
            short_string(version),
            short_string(chain_id),
            revision,
        ]
    )


def order_msg_hash(
    *,
    position_id: int,
    base_asset_id: int,
    base_amount: int,
    quote_asset_id: int,
    quote_amount: int,
    fee_asset_id: int,
    fee_amount: int,
    expiration_sec: int,
    salt: int,
    public_key: int,
    domain: dict[str, Any],
) -> int:
    """SNIP-12 message hash for the perpetuals Order struct.

    Amounts are scaled integers (signed: base/quote have opposite signs).
    """
    # Local import to avoid circular at module level; Any used above.
    struct_hash = poseidon_hash_many(
        [
            ORDER_TYPE_HASH,
            position_id,
            base_asset_id,
            _to_felt(base_amount),
            quote_asset_id,
            _to_felt(quote_amount),
            fee_asset_id,
            fee_amount,
            expiration_sec,
            salt,
        ]
    )
    dh = domain_hash(
        name=domain["name"],
        version=domain["version"],
        chain_id=domain["chainId"],
        revision=domain["revision"],
    )
    return poseidon_hash_many(
        [short_string("StarkNet Message"), dh, public_key, struct_hash]
    )


# ---- exact decimal-string arithmetic (int) ----


class _ParsedDec:
    """Parsed decimal: "12.34" -> i=1234, scale=2."""

    __slots__ = ("i", "scale")

    def __init__(self, i: int, scale: int) -> None:
        self.i = i
        self.scale = scale


def parse_dec(s: str | int | float) -> _ParsedDec:
    """Parse a decimal string into {i, scale} for exact BigInt arithmetic."""
    m = re.match(r"^(-?)(\d+)(?:\.(\d+))?$", str(s).strip())
    if not m:
        raise ValueError(f"cannot parse number: {s}")
    sign, whole, frac = m.group(1), m.group(2), m.group(3) or ""
    return _ParsedDec(int(sign + whole + frac), len(frac))


def _div_round(num: int, den: int, mode: str) -> int:
    """Floor/ceil division for positive operands. mode: 'down' | 'up'."""
    q = num // den
    if num % den == 0:
        return q
    return q + 1 if mode == "up" else q


def settlement_amounts(
    *,
    qty: str,
    price: str,
    fee_rate: str,
    syn_res: int,
    col_res: int,
    is_buy: bool,
) -> dict[str, int]:
    """Stark settlement amounts for an order, mirroring the official SDK exactly.

    synthetic = qty * synRes              (buy: round up,  sell: round down)
    collateral = qty * price * colRes     (same rounding; negated for buys)
    fee = feeRate * qty * price * colRes  (always round up)
    synthetic negated for sells.
    """
    q = parse_dec(qty)
    p = parse_dec(price)
    f = parse_dec(fee_rate)
    mode = "up" if is_buy else "down"
    syn = _div_round(q.i * syn_res, 10**q.scale, mode)
    col = _div_round(q.i * p.i * col_res, 10 ** (q.scale + p.scale), mode)
    fee = _div_round(
        f.i * q.i * p.i * col_res, 10 ** (f.scale + q.scale + p.scale), "up"
    )
    return {
        "synthetic_amount": syn if is_buy else -syn,
        "collateral_amount": -col if is_buy else col,
        "fee_amount": fee,
    }


def align_to_step(value: float, step: str, mode: str = "nearest") -> str:
    """Align a float to a decimal step (e.g. price 61827.73 with step "0.1").

    mode: 'nearest' | 'down'. Returns a clean decimal string.
    """
    st = parse_dec(step)
    if st.i <= 0:
        raise ValueError("step must be positive")
    scaled = value * float(10**st.scale)
    if mode == "down":
        units = int(scaled / st.i + 1e-9)
    else:
        units = round(scaled / st.i)
    total = units * st.i  # scaled integer at st.scale decimals
    return _format_scaled(total, st.scale)


def _format_scaled(i: int, scale: int) -> str:
    """Format a scaled integer as a decimal string."""
    neg = i < 0
    if neg:
        i = -i
    s = str(i).rjust(scale + 1, "0")
    if scale:
        out = f"{s[:-scale]}.{s[-scale:]}"
    else:
        out = s
    # Trim trailing zeros ONLY in the fractional part.
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    if out in ("", "-"):
        out = "0"
    return ("-" if neg else "") + out


def to_hex(v: int) -> str:
    """Convert int to hex string with 0x prefix."""
    return hex(v)


# ---- self test (official Extended python SDK vector) ----


def self_test() -> None:
    """Throw if the implementation doesn't reproduce the official SDK signature."""
    priv = 0x7A7FF6FD3CAB02CCDCD4A572563F5976F8976899B03A39773795A3C486D4986
    pub = 0x61C5E7E8339B7D56F197F54EA91B776776690E3232313DE0F2ECBD0EF76F466
    amounts = settlement_amounts(
        qty="0.00100000",
        price="43445.11680000",
        fee_rate="0.0005",
        syn_res=1000000,
        col_res=1000000,
        is_buy=False,
    )
    if (
        amounts["synthetic_amount"] != -1000
        or amounts["collateral_amount"] != 43445116
        or amounts["fee_amount"] != 21723
    ):
        raise ValueError("starkcrypto selfTest: amounts mismatch")
    h = order_msg_hash(
        position_id=10002,
        base_asset_id=0x4254432D3600000000000000000000,
        base_amount=amounts["synthetic_amount"],
        quote_asset_id=0x31857064564ED0FF978E687456963CBA09C2C6985D8F9300A1DE4962FAFA054,
        quote_amount=amounts["collateral_amount"],
        fee_asset_id=0x31857064564ED0FF978E687456963CBA09C2C6985D8F9300A1DE4962FAFA054,
        fee_amount=amounts["fee_amount"],
        expiration_sec=1706836137,
        salt=1473459052,
        public_key=pub,
        domain={
            "name": "Perpetuals",
            "version": "v0",
            "chainId": "SN_SEPOLIA",
            "revision": 1,
        },
    )
    expected_hash = 2969335148777495210033041829700798003994871688044444919524700744667647811801
    if h != expected_hash:
        raise ValueError(
            "starkcrypto selfTest failed: signing implementation does not match "
            "official SDK test vector. Live trading blocked for safety."
        )
