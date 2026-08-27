"""
Token Encryption/Decryption Utility

This module provides a class for encrypting and decrypting tokens using RSA keys.
Keys are loaded from files and cached in memory for performance.
"""

import base64
import hashlib
import pickle
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Union
from urllib.parse import quote

from logging_config import get_logger

# Import local RSA library
from rsa import PrivateKey, PublicKey, decrypt, encrypt, newkeys

# Module logger
logger = get_logger(__name__)

REALM = "Splunk_MCP_Server"
KEY_CREDENTIAL_USERNAME = "private_key"
PASSWORDS_ENDPOINT = "servicesNS/nobody/Splunk_MCP_Server/storage/passwords"


@dataclass
class _KeyReloadState:
    last_loaded_monotonic: float = 0.0
    last_decrypt_error_reload_monotonic: float = 0.0


class TokenCrypto:
    """
    A singleton class for encrypting and decrypting tokens using RSA public/private key pairs.

    The class loads the private key from Splunk secure storage and derives the public key
    from it. Keys are cached in memory for efficient repeated use.

    Attributes:
        private_key: The cached private key for decryption
        public_key: Property that derives the public key from the private key
    """

    _instance: Optional["TokenCrypto"] = None
    _initialized: bool = False

    def __init__(self):
        self.public_key: Optional[PublicKey] = None
        self.private_key: Optional[PrivateKey] = None

        # Key reload behavior
        self._reload_interval_seconds: float = 0.0
        self._keys: _KeyReloadState = _KeyReloadState()
        self._system_authtoken: Optional[str] = None

    def __new__(cls) -> "TokenCrypto":
        """
        Create or return the singleton instance.
        """
        if cls._instance is None:
            cls._instance = super(TokenCrypto, cls).__new__(cls)
        return cls._instance

    def configure_reload_interval(self, seconds: float) -> None:
        """Configure how often `initialize()`/`encrypt()` will refresh keys.

        Set to 0 to disable periodic reloads.
        """
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        self._reload_interval_seconds = max(0.0, seconds)

    def initialize(self, system_authtoken: str) -> bool:
        """
        Initialize the TokenCrypto class.

        Read the private key from Splunk secure storage.
        If it is not found, generate a new key pair and store the private key.
        Handles race conditions for saving the token.

        If `initialize()` is called again after the configured reload interval,
        keys will be reloaded from storage.
        """
        # Keep the token for future reload attempts (periodic or decrypt error).
        self._system_authtoken = system_authtoken

        now = time.monotonic()

        # If already initialized, optionally refresh keys based on interval.
        if TokenCrypto._initialized:
            self._maybe_reload_keys(now=now, reason="periodic")
            return True

        # First try to load existing keys from Splunk secure storage
        if not self._load_keys_from_splunk_storage(system_authtoken):
            # If no keys exist or loading fails, generate new ones
            logger.info("No existing keys found, generating new key pair")
            self._generate_key_pair()
            stored = self._store_keys_in_splunk_storage(system_authtoken)
            if not stored:
                # Race condition: another process may have stored the key first
                logger.warning("Failed to store key, retrying load from storage")
                if self._load_keys_from_splunk_storage(system_authtoken):
                    logger.info("Loaded key from storage after race condition")
                else:
                    logger.error("Failed to store or load key after race condition")
                    return False

        self._keys.last_loaded_monotonic = now
        TokenCrypto._initialized = True

        logger.info("TokenCrypto initialized")

        return True

    def _maybe_reload_keys(
        self, *, now: Optional[float] = None, reason: str = "periodic"
    ) -> bool:
        """Reload keys from storage if enough time has passed.

        Returns True if a reload was attempted and succeeded.
        """
        if not self._system_authtoken:
            return False

        if now is None:
            now = time.monotonic()

        # periodic reload disabled
        if self._reload_interval_seconds <= 0:
            return False

        last = self._keys.last_loaded_monotonic
        # If last_loaded is unset, treat as needing reload.
        if last and (now - last) < self._reload_interval_seconds:
            return False

        try:
            if self._load_keys_from_splunk_storage(self._system_authtoken):
                self._keys.last_loaded_monotonic = now
                logger.info("Reloaded TokenCrypto keys (%s)", reason)
                return True
        except Exception as e:
            logger.warning("Failed reloading TokenCrypto keys (%s): %s", reason, str(e))

        return False

    def reset(self) -> None:
        """
        Reset cached keys to force reload or regeneration.
        """
        self.public_key = None
        self.private_key = None
        TokenCrypto._initialized = False
        self._keys = _KeyReloadState()
        # Keep configured interval, but forget token.
        self._system_authtoken = None

    @classmethod
    def get_instance(cls) -> "TokenCrypto":
        """
        Get the singleton instance of TokenCrypto.

        Returns:
            TokenCrypto: The singleton instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_keys_from_splunk_storage(self, system_authtoken: str) -> bool:
        """
        Load existing RSA private key (and timestamp) from Splunk's secure credential storage.
        Returns True if key was successfully loaded, False otherwise.
        """
        try:
            # Import here to avoid circular imports
            from splunk_api import call_splunk_api

            credential_name = f"{REALM}:{KEY_CREDENTIAL_USERNAME}:"
            credential_path = quote(credential_name, safe="")

            response = call_splunk_api(
                session_key=system_authtoken,
                method="GET",
                api=f"{PASSWORDS_ENDPOINT}/{credential_path}",
                params={"output_mode": "json"},
            )

            private_key_data = None
            if response.status_code == 200:
                data = response.json()
                entry = data.get("entry", [])
                if entry and len(entry) > 0:
                    password = entry[0].get("content", {}).get("clear_password")
                    if password:
                        private_key_data = password

            elif response.status_code == 404:
                # If the direct GET misses, fall back to listing and matching by entry name.
                logger.info("Credential not found by direct GET, falling back to list")
                list_response = call_splunk_api(
                    session_key=system_authtoken,
                    method="GET",
                    api=f"{PASSWORDS_ENDPOINT}",
                    params={"output_mode": "json", "count": 0},
                )
                if list_response.status_code == 200:
                    data = list_response.json()
                    for ent in data.get("entry", []) or []:
                        # Splunk stores the entry name as "<realm>:<username>:"
                        if ent.get("name") == credential_name:
                            private_key_data = (ent.get("content", {}) or {}).get(
                                "clear_password"
                            )
                            if private_key_data:
                                break

            if not private_key_data:
                logger.info("Private key not found in Splunk storage")
                return False

            # Deserialize the private key structure from base64-encoded pickled data
            private_key_bytes = base64.b64decode(private_key_data.encode("utf-8"))
            loaded_obj = pickle.loads(private_key_bytes)
            if isinstance(loaded_obj, dict) and "private_key" in loaded_obj:
                self.private_key = loaded_obj["private_key"]
                self.private_key_created_at = loaded_obj.get("created_at")
                logger.info(
                    "Successfully loaded RSA private key and timestamp from Splunk secure storage"
                )
            else:
                logger.warning("Invalid private key structure in Splunk storage")
                return False

            self.public_key = PublicKey(self.private_key.n, self.private_key.e)

            logger.info(
                "Successfully loaded RSA private key from Splunk secure storage"
            )
            return True

        except Exception as e:
            logger.warning("Failed to load private key from Splunk storage: %s", str(e))
            return False

    def encrypt(self, data: Union[str, bytes]) -> str:
        """
        Encrypt data using the public key.

        Supports multi-block encryption: if the data length exceeds the
        maximum per-block size (key_bytes - 11 for PKCS#1 v1.5) it will be
        chunked and each chunk encrypted separately. The resulting ciphertext
        is a '.'-separated list of base64 blocks.

        Args:
            data: The data to encrypt (string or bytes)

        Returns:
            Base64-encoded encrypted data (single block) or multi-block joined by '.'

        Raises:
            ValueError: If public key is not loaded or encryption fails
        """
        # Opportunistically reload keys if we're past the interval.
        self._maybe_reload_keys(reason="periodic-encrypt")

        if not self.public_key:
            raise ValueError("Public key not loaded. Cannot encrypt data.")

        if isinstance(data, str):
            data = data.encode("utf-8")

        key_bytes = (self.public_key.n.bit_length() + 7) // 8
        block_size = key_bytes - 11  # PKCS#1 v1.5 padding overhead

        try:
            if len(data) <= block_size:
                encrypted = encrypt(data, self.public_key)
                return base64.b64encode(encrypted).decode("utf-8")
            # Multi-block path
            blocks = []
            for i in range(0, len(data), block_size):
                chunk = data[i : i + block_size]
                encrypted_chunk = encrypt(chunk, self.public_key)
                blocks.append(base64.b64encode(encrypted_chunk).decode("utf-8"))
            return ".".join(blocks)
        except Exception as e:
            raise ValueError(f"Encryption failed: {str(e)}")

    def decrypt(self, encrypted_data: str) -> str:
        """
        Decrypt data using the private key.

        Supports multi-block decryption: if the ciphertext contains '.' it is
        treated as multiple base64-encoded RSA blocks, each decrypted and
        concatenated.

        Args:
            encrypted_data: Base64-encoded encrypted data (single or multi-block)

        Returns:
            Decrypted data as a UTF-8 string, or the original encrypted_data on failure
        """
        if not self.private_key:
            return encrypted_data

        def _decrypt_with_current_key(data: str) -> str:
            segments = data.split(".")
            plaintext_parts = []
            for segment in segments:
                encrypted_bytes = base64.b64decode(segment.encode("utf-8"))
                decrypted_bytes = decrypt(encrypted_bytes, self.private_key)
                plaintext_parts.append(decrypted_bytes)
            combined = b"".join(plaintext_parts)
            logger.info(
                "Encrypted token decrypted successfully (%d block(s)).", len(segments)
            )
            return combined.decode("utf-8")

        try:
            return _decrypt_with_current_key(encrypted_data)
        except Exception as e:
            # If decryption fails, try reloading keys at most once every 5 seconds,
            # then retry once with the reloaded key.
            now = time.monotonic()
            if self._system_authtoken:
                if (now - self._keys.last_decrypt_error_reload_monotonic) >= 5.0:
                    self._keys.last_decrypt_error_reload_monotonic = now
                    try:
                        reloaded = self._load_keys_from_splunk_storage(
                            self._system_authtoken
                        )
                        if reloaded:
                            self._keys.last_loaded_monotonic = now
                            try:
                                # Retry exactly once using the newly loaded key.
                                return _decrypt_with_current_key(encrypted_data)
                            except Exception:
                                # Retry failed; fall through to raising original error.
                                pass
                    except Exception as reload_exc:
                        # Fall through to raising the original failure (wrapped).
                        logger.warning(
                            "Decrypt failed and reload attempt did not help: %s",
                            str(reload_exc),
                        )

            raise ValueError(f"Decryption failed: {str(e)}")

    def get_encryption_config(self) -> dict:
        """
        Export the public key and the configuration required for the custom
        raw RSA encryption used by this service.

        NOTE:
            The encrypt()/decrypt() methods perform a direct RSA operation
            (PKCS#1 v1.5 padding) over the UTF-8 bytes of the plaintext and
            return / expect a single base64 encoded ciphertext string. This is
            NOT a JWE (no content encryption key, no authenticated data, no
            separate content cipher). Previous versions incorrectly returned a
            JWE-style configuration (RSA-OAEP-256 + A128GCM) which did not match
            the actual implementation and caused decryption failures.

        Returns:
            Dictionary containing:
              - public_key: RSA public key parameters (JWK style n/e)
              - encryption_config: description of the raw RSA scheme
        Raises:
            ValueError: If public key is not loaded
        """
        if not self.public_key:
            raise ValueError("Public key not loaded.")

        # Extract RSA components from the public key
        n = self.public_key.n  # modulus
        e = self.public_key.e  # exponent

        # Convert integer RSA components to base64url without padding
        def int_to_base64url(value: int) -> str:
            byte_length = (value.bit_length() + 7) // 8
            value_bytes = value.to_bytes(byte_length, byteorder="big")
            encoded = base64.urlsafe_b64encode(value_bytes).decode("utf-8")
            return encoded.rstrip("=")

        response_data = {
            "public_key": {
                "kty": "RSA",
                "use": "enc",
                "n": int_to_base64url(n),
                "e": int_to_base64url(e),
            },
            "encryption_config": {
                "alg": "RSA1_5",  # Direct RSA (PKCS#1 v1.5 padding)
                "mode": "RAW",  # No JWE wrapper
                "padding": "PKCS1v1_5",
                "cipher": "RSA",
                "multi_block": True,
                "block_size": ((n.bit_length() + 7) // 8) - 11,
                "output_encoding": "base64",
                "separator": ".",
                "notes": "Data larger than block_size bytes is chunked, each chunk encrypted separately and base64 encoded; ciphertext blocks joined by '.'",
            },
            "description": "Configuration for raw RSA encryption compatible with encrypt()/decrypt() methods (supports multi-block).",
        }

        return response_data

    def _generate_key_pair(self, key_size: int = 2048):
        """
        Generate a new RSA key pair.

        Args:
            key_size: Size of the key in bits (default: 2048)

        Returns:
            None
        """
        logger.info("Generating new RSA key pair.")
        # Generate RSA key pair using local RSA library
        self.public_key, self.private_key = newkeys(key_size)

        logger.info("RSA key pair generated successfully.")

    def _store_keys_in_splunk_storage(
        self, session_key: str, allow_overwrite: bool = False
    ) -> Optional[str]:
        """
        Store the generated RSA private key and timestamp in Splunk's secure credential storage.
        Uses Splunk's storage/passwords API endpoint to securely store the
        private key with appropriate realm naming.

        Args:
            session_key: Splunk system auth token for storage access.
            allow_overwrite: If True, will delete and overwrite any existing key on conflict (409).
        Returns:
            True if the key and timestamp were successfully stored, False otherwise.
        """
        if not self.private_key:
            logger.error("No private key available to store")
            return None

        try:
            # Store private key and timestamp as base64-encoded pickled data
            key_struct = {
                "private_key": self.private_key,
                "created_at": self._current_timestamp(),
            }
            private_key_bytes = pickle.dumps(key_struct)
            private_key_data = base64.b64encode(private_key_bytes).decode("utf-8")

            stored = TokenCrypto._store_password_credential(
                session_key,
                KEY_CREDENTIAL_USERNAME,
                private_key_data,
                allow_overwrite,
            )
            if stored:
                logger.info(
                    "Successfully stored RSA private key and timestamp in Splunk secure storage"
                )
                return key_struct["created_at"]

            logger.error("Failed to store RSA private key in Splunk secure storage")
            return None

        except Exception as e:
            logger.error("Failed to store keys in Splunk storage: %s", str(e))
            return None

    def rotate_keys(self, system_authtoken: str, key_size: int = 2048) -> str:
        """
        Rotate RSA key pair and persist the new private key.

        Args:
            system_authtoken: Splunk system auth token for storage access.
            key_size: Size of the key in bits (default: 2048).
        Returns:
            Rotation timestamp in UTC ISO-8601 format.
        """
        self.reset()
        self._generate_key_pair(key_size=key_size)
        update_time = self._store_keys_in_splunk_storage(
            system_authtoken, allow_overwrite=True
        )
        if not update_time:
            raise ValueError("Failed to store rotated private key")
        TokenCrypto._initialized = True
        return update_time

    def get_public_key_fingerprint(self) -> str:
        """
        Return a stable fingerprint of the current public key.
        """
        if not self.public_key:
            raise ValueError("Public key not loaded.")
        fingerprint_input = f"{self.public_key.n}:{self.public_key.e}".encode("utf-8")
        return hashlib.sha256(fingerprint_input).hexdigest()

    @staticmethod
    def _current_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _store_password_credential(
        session_key: str, username: str, password: str, allow_overwrite: bool = False
    ) -> bool:
        """
        Store or replace a credential in Splunk secure storage.
        If allow_delete is True, delete the existing credential on 409 error.
        """
        try:
            from splunk_api import call_splunk_api

            response = call_splunk_api(
                session_key=session_key,
                method="POST",
                api=f"{PASSWORDS_ENDPOINT}",
                data={"name": username, "realm": REALM, "password": password},
            )

            if response.status_code in (200, 201):
                return True

            # 409 can happen during startup if two processes generate+store concurrently.
            # Treat that as an expected race unless we explicitly asked to overwrite.
            if response.status_code == 409 and not allow_overwrite:
                logger.warning("Credential already exists (expected race)")
                return False

            if response.status_code == 409 and allow_overwrite:
                credential_name = f"{REALM}:{username}:"
                credential_path = quote(credential_name, safe="")
                delete_response = call_splunk_api(
                    session_key=session_key,
                    method="DELETE",
                    api=f"{PASSWORDS_ENDPOINT}/{credential_path}",
                )
                # Proceed regardless of delete status; POST will be authoritative.
                _ = delete_response

                response = call_splunk_api(
                    session_key=session_key,
                    method="POST",
                    api=f"{PASSWORDS_ENDPOINT}",
                    data={"name": username, "realm": REALM, "password": password},
                )
                return response.status_code in (200, 201)

            logger.error(
                "Failed to store credential %s in realm %s: %s - %s",
                username,
                REALM,
                response.status_code,
                response.text,
            )
            return False
        except Exception as e:
            logger.error(
                "Failed to store credential %s in realm %s: %s", username, REALM, str(e)
            )
            return False
