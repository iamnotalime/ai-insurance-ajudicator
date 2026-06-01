"""
PII Encryption Utilities for Insurance Adjudication System
Provides encryption at rest for sensitive data
"""

import base64
import hashlib
import logging
import os
import secrets
from typing import Optional, Union
from dataclasses import dataclass

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..config.settings import settings


logger = logging.getLogger(__name__)


@dataclass
class EncryptionConfig:
    """Configuration for encryption"""
    # Key derivation iterations (higher = more secure but slower)
    kdf_iterations: int = 480000

    # Nonce size for AES-GCM (96 bits recommended)
    nonce_size: int = 12

    # Salt size for key derivation
    salt_size: int = 16


class EncryptionKeyManager:
    """
    Manages encryption keys for PII protection.

    In production, integrate with:
    - AWS KMS
    - HashiCorp Vault
    - Azure Key Vault
    - Google Cloud KMS
    """

    def __init__(self, master_key: Optional[str] = None):
        """
        Initialize with master key.

        Args:
            master_key: Base64-encoded master key. If not provided,
                       reads from ENCRYPTION_KEY environment variable.
        """
        self.config = EncryptionConfig()

        if master_key:
            self._master_key = base64.urlsafe_b64decode(master_key)
        else:
            key_env = os.getenv("ENCRYPTION_KEY")
            if key_env:
                self._master_key = base64.urlsafe_b64decode(key_env)
            else:
                if settings.is_production:
                    raise ValueError(
                        "ENCRYPTION_KEY environment variable must be set in production"
                    )
                # Generate a random key for development
                logger.warning(
                    "No encryption key configured. Generating random key for development. "
                    "Data will NOT be decryptable after restart!"
                )
                self._master_key = secrets.token_bytes(32)

        # Validate key size (256 bits for AES-256)
        if len(self._master_key) != 32:
            raise ValueError("Master key must be 32 bytes (256 bits)")

    def _derive_key(self, salt: bytes, purpose: str = "encryption") -> bytes:
        """Derive a purpose-specific key from master key"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt + purpose.encode(),
            iterations=self.config.kdf_iterations,
        )
        return kdf.derive(self._master_key)

    @staticmethod
    def generate_master_key() -> str:
        """Generate a new master key (for initial setup)"""
        key = secrets.token_bytes(32)
        return base64.urlsafe_b64encode(key).decode()


class PIIEncryptor:
    """
    Encrypts and decrypts PII using AES-256-GCM.

    Features:
    - Authenticated encryption (confidentiality + integrity)
    - Unique nonce per encryption
    - Key derivation from master key
    - Deterministic encryption option for searchable fields
    """

    def __init__(self, key_manager: Optional[EncryptionKeyManager] = None):
        self.key_manager = key_manager or EncryptionKeyManager()
        self.config = self.key_manager.config

    def encrypt(
        self,
        plaintext: Union[str, bytes],
        associated_data: Optional[bytes] = None,
    ) -> str:
        """
        Encrypt data using AES-256-GCM.

        Args:
            plaintext: Data to encrypt
            associated_data: Optional additional authenticated data (not encrypted)

        Returns:
            Base64-encoded ciphertext (salt + nonce + ciphertext + tag)
        """
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")

        # Generate random salt and nonce
        salt = secrets.token_bytes(self.config.salt_size)
        nonce = secrets.token_bytes(self.config.nonce_size)

        # Derive encryption key
        key = self.key_manager._derive_key(salt)

        # Encrypt
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)

        # Combine: salt + nonce + ciphertext
        combined = salt + nonce + ciphertext

        return base64.urlsafe_b64encode(combined).decode()

    def decrypt(
        self,
        encrypted_data: str,
        associated_data: Optional[bytes] = None,
    ) -> str:
        """
        Decrypt data encrypted with AES-256-GCM.

        Args:
            encrypted_data: Base64-encoded ciphertext
            associated_data: Optional additional authenticated data

        Returns:
            Decrypted plaintext string
        """
        combined = base64.urlsafe_b64decode(encrypted_data)

        # Extract components
        salt = combined[: self.config.salt_size]
        nonce = combined[
            self.config.salt_size : self.config.salt_size + self.config.nonce_size
        ]
        ciphertext = combined[self.config.salt_size + self.config.nonce_size :]

        # Derive key
        key = self.key_manager._derive_key(salt)

        # Decrypt
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data)

        return plaintext.decode("utf-8")

    def encrypt_deterministic(self, plaintext: str, field_name: str) -> str:
        """
        Deterministic encryption for searchable encrypted fields.

        WARNING: Less secure than random encryption. Use only when
        searching encrypted data is required.

        Args:
            plaintext: Data to encrypt
            field_name: Field identifier (ensures different fields produce different ciphertexts)

        Returns:
            Base64-encoded ciphertext
        """
        if not plaintext:
            return ""

        # Use hash of plaintext + field as nonce (deterministic)
        nonce_input = f"{field_name}:{plaintext}".encode()
        nonce = hashlib.sha256(nonce_input).digest()[: self.config.nonce_size]

        # Fixed salt for deterministic encryption
        salt = hashlib.sha256(field_name.encode()).digest()[: self.config.salt_size]

        # Derive key
        key = self.key_manager._derive_key(salt, purpose=f"det_{field_name}")

        # Encrypt
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)

        # Combine: salt + nonce + ciphertext
        combined = salt + nonce + ciphertext

        return base64.urlsafe_b64encode(combined).decode()

    def hash_for_lookup(self, value: str, field_name: str) -> str:
        """
        Create a searchable hash for lookup without decryption.

        Use this to build indexes on encrypted fields.

        Args:
            value: Value to hash
            field_name: Field identifier

        Returns:
            Hex-encoded hash
        """
        # Use HMAC-SHA256 with derived key
        salt = hashlib.sha256(field_name.encode()).digest()[: self.config.salt_size]
        key = self.key_manager._derive_key(salt, purpose=f"lookup_{field_name}")

        h = hashlib.sha256(key + value.encode())
        return h.hexdigest()


class PIIFieldEncryptor:
    """
    Helper class for encrypting specific PII field types.
    Provides consistent encryption across the application.
    """

    def __init__(self, encryptor: Optional[PIIEncryptor] = None):
        self.encryptor = encryptor or PIIEncryptor()

    def encrypt_ssn(self, ssn: str) -> str:
        """Encrypt Social Security Number"""
        # Remove dashes for storage
        clean_ssn = ssn.replace("-", "").replace(" ", "")
        return self.encryptor.encrypt(clean_ssn)

    def decrypt_ssn(self, encrypted_ssn: str) -> str:
        """Decrypt Social Security Number"""
        ssn = self.encryptor.decrypt(encrypted_ssn)
        # Format as XXX-XX-XXXX
        if len(ssn) == 9:
            return f"{ssn[:3]}-{ssn[3:5]}-{ssn[5:]}"
        return ssn

    def encrypt_email(self, email: str) -> str:
        """Encrypt email address"""
        return self.encryptor.encrypt(email.lower())

    def decrypt_email(self, encrypted_email: str) -> str:
        """Decrypt email address"""
        return self.encryptor.decrypt(encrypted_email)

    def encrypt_phone(self, phone: str) -> str:
        """Encrypt phone number"""
        # Remove non-digits for storage
        clean_phone = "".join(c for c in phone if c.isdigit())
        return self.encryptor.encrypt(clean_phone)

    def decrypt_phone(self, encrypted_phone: str) -> str:
        """Decrypt phone number"""
        return self.encryptor.decrypt(encrypted_phone)

    def encrypt_address(self, address: dict) -> str:
        """Encrypt address dictionary"""
        import json

        address_json = json.dumps(address, sort_keys=True)
        return self.encryptor.encrypt(address_json)

    def decrypt_address(self, encrypted_address: str) -> dict:
        """Decrypt address dictionary"""
        import json

        address_json = self.encryptor.decrypt(encrypted_address)
        return json.loads(address_json)

    def hash_email_for_lookup(self, email: str) -> str:
        """Create searchable hash for email lookup"""
        return self.encryptor.hash_for_lookup(email.lower(), "email")

    def mask_ssn(self, ssn: str) -> str:
        """Mask SSN for display (XXX-XX-1234)"""
        clean_ssn = ssn.replace("-", "").replace(" ", "")
        if len(clean_ssn) >= 4:
            return f"XXX-XX-{clean_ssn[-4:]}"
        return "XXX-XX-XXXX"

    def mask_email(self, email: str) -> str:
        """Mask email for display (j***@example.com)"""
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) > 1:
            masked_local = local[0] + "***"
        else:
            masked_local = "***"
        return f"{masked_local}@{domain}"


# Global instances
_key_manager: Optional[EncryptionKeyManager] = None
_encryptor: Optional[PIIEncryptor] = None
_field_encryptor: Optional[PIIFieldEncryptor] = None


def get_encryptor() -> PIIEncryptor:
    """Get the global PII encryptor instance"""
    global _key_manager, _encryptor

    if _encryptor is None:
        _key_manager = EncryptionKeyManager()
        _encryptor = PIIEncryptor(_key_manager)

    return _encryptor


def get_field_encryptor() -> PIIFieldEncryptor:
    """Get the global PII field encryptor instance"""
    global _field_encryptor

    if _field_encryptor is None:
        _field_encryptor = PIIFieldEncryptor(get_encryptor())

    return _field_encryptor
