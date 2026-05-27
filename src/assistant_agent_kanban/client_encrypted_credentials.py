from __future__ import annotations

import base64
import json
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydantic import BaseModel, Field, field_validator


GIT_TOKEN_AAD_PURPOSE = "assistant-agent-kanban.git-token"
GIT_TOKEN_ENCRYPTION_VERSION = 1
MIN_PBKDF2_ITERATIONS = 100_000


class ClientEncryptedGitToken(BaseModel):
    version: int = GIT_TOKEN_ENCRYPTION_VERSION
    algorithm: Literal["AES-256-GCM"]
    kdf: Literal["PBKDF2-SHA256"]
    kdf_iterations: int = Field(ge=MIN_PBKDF2_ITERATIONS)
    salt: str
    nonce: str
    ciphertext: str
    aad: str

    @field_validator("salt", "nonce", "ciphertext")
    @classmethod
    def validate_base64(cls, value: str) -> str:
        _decode_base64(value)
        return value

    @field_validator("aad")
    @classmethod
    def validate_aad(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("aad is required")
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("aad must be canonical JSON") from exc
        return value


def decrypt_client_encrypted_git_token(payload: ClientEncryptedGitToken, unlock_key: str) -> str:
    key_text = unlock_key.strip()
    if not key_text:
        raise ValueError("Git token unlock key is required")
    salt = _decode_base64(payload.salt)
    nonce = _decode_base64(payload.nonce)
    ciphertext = _decode_base64(payload.ciphertext)
    key = _derive_key(key_text, salt, payload.kdf_iterations)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, payload.aad.encode("utf-8"))
    except InvalidTag as exc:
        raise ValueError("Git token cannot be decrypted with the provided unlock key") from exc
    return plaintext.decode("utf-8")


def validate_git_token_aad(aad: str, *, user_id: str) -> None:
    try:
        data = json.loads(aad)
    except json.JSONDecodeError as exc:
        raise ValueError("Git token AAD must be canonical JSON") from exc
    if data.get("purpose") != GIT_TOKEN_AAD_PURPOSE:
        raise ValueError("Git token AAD purpose is invalid")
    if int(data.get("version") or 0) != GIT_TOKEN_ENCRYPTION_VERSION:
        raise ValueError("Git token AAD version is invalid")
    if str(data.get("user_id") or "") != user_id:
        raise ValueError("Git token AAD user does not match the signed-in user")


def _derive_key(unlock_key: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(unlock_key.encode("utf-8"))


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError("value must be base64 encoded") from exc
