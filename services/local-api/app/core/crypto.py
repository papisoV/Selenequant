"""RSA key pair management for encrypted secret transport.

Generates an ephemeral RSA-2048 key pair at startup.  The frontend encrypts
sensitive values (API keys, secrets) with the public key before sending them
over HTTP.  The backend decrypts with the private key, then stores plaintext
in the OS keyring.

The key pair lives only in process memory and is never persisted to disk.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

logger = logging.getLogger(__name__)

# Marker prefix that the frontend wraps around ciphertext so the backend
# can distinguish encrypted values from plaintext.
ENCRYPTED_PREFIX = "enc:"

#: 公钥指数固定 65537(RSA 常规选择)。
_PUBLIC_EXPONENT = 65537
#: 密钥长度 2048 位, 与前端 Web Crypto 导入口径一致。
_KEY_SIZE = 2048
#: OAEP 用 SHA-256 作 MGF1 与主摘要。
_OAEP_ALGORITHM = hashes.SHA256()
#: JWK 中 RSA 指数 e 的定长字节数(65537 占 3 字节)。
_EXPONENT_BYTES = 3
#: JWK 固定声明。
_JWK_KEY_TYPE = "RSA"
_JWK_ALGORITHM = "RSA-OAEP-256"
_JWK_USE = "enc"


def _uint_to_b64url(value: int, length: int) -> str:
    """把非负整数按定长大端字节编码, 再去掉 padding 做 base64url。"""
    raw = value.to_bytes(length, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    """解码前端传来的 base64(标准与 URL-safe 都收)。"""
    return base64.b64decode(data)


class KeyPairManager:
    """Ephemeral RSA-2048 key pair for frontend-to-backend secret transport."""

    def __init__(self) -> None:
        self._private_key: RSAPrivateKey = rsa.generate_private_key(
            public_exponent=_PUBLIC_EXPONENT,
            key_size=_KEY_SIZE,
        )
        self._public_key: RSAPublicKey = self._private_key.public_key()
        logger.info("Generated ephemeral RSA-2048 key pair for secret transport")

    def _oaep_scheme(self) -> padding.OAEP:
        """本项目固定的 OAEP 参数(MGF1/SHA-256, 无 label)。"""
        return padding.OAEP(
            mgf=padding.MGF1(algorithm=_OAEP_ALGORITHM),
            algorithm=_OAEP_ALGORITHM,
            label=None,
        )

    def _decrypt_bytes(self, raw: bytes) -> bytes:
        """私钥解密; 任何底层失败统一收敛成 ValueError。"""
        try:
            return self._private_key.decrypt(raw, self._oaep_scheme())
        except Exception as exc:
            raise ValueError("RSA-OAEP decryption failed") from exc

    def public_key_jwk(self) -> dict[str, Any]:
        """Export the public key as a JWK dict (for Web Crypto API import)."""
        numbers = self._public_key.public_numbers()
        # 模数 n 按密钥字节宽度定长编码。
        modulus_bytes = (self._public_key.key_size + 7) // 8
        return {
            "kty": _JWK_KEY_TYPE,
            "alg": _JWK_ALGORITHM,
            "use": _JWK_USE,
            "n": _uint_to_b64url(numbers.n, modulus_bytes),
            "e": _uint_to_b64url(numbers.e, _EXPONENT_BYTES),
        }

    def decrypt(self, ciphertext_b64: str) -> str:
        """Decrypt a Base64-encoded RSA-OAEP-256 ciphertext back to plaintext.

        Args:
            ciphertext_b64: Base64 (standard or URL-safe) encoded ciphertext
                produced by the frontend using the matching public key.

        Raises:
            ValueError: If decryption fails (wrong key, corrupt data, etc.).
        """
        try:
            raw = _b64decode(ciphertext_b64)
        except Exception as exc:
            raise ValueError("Invalid base64 ciphertext") from exc

        return self._decrypt_bytes(raw).decode("utf-8")


def decrypt_if_encrypted(manager: KeyPairManager | None, value: str) -> str:
    """Return the plaintext of *value*, decrypting if it carries the ``enc:`` prefix.

    If *manager* is ``None`` or the value is not prefixed, returns *value* as-is.
    """
    if manager is None or not value.startswith(ENCRYPTED_PREFIX):
        return value
    return manager.decrypt(value[len(ENCRYPTED_PREFIX) :])
