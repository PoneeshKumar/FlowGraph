# this file will have events for crypto transactions using pydantic models

# neccessary imports
import re
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import Field, field_validator, model_validator

from models.card_events import BasePaymentEvent, EventStatus, EventType, Rail

# enums that are needed
class ChainType(str, Enum):
    BITCOIN = "BITCOIN"
    ETHEREUM = "ETHEREUM"


class CryptoTxStatus(str, Enum):
    PENDING = "PENDING"      # broadcast, 0 confirmations
    CONFIRMED = "CONFIRMED"  # threshold confirmations reached
    FAILED = "FAILED"        # transaction dropped or reverted


# constants according to how bitcoin and etherium work
BTC_CONFIRMATIONS_REQUIRED = 6
ETH_CONFIRMATIONS_REQUIRED = 12


# helper to check to validate adresss
def validate_wallet_address(address: str, chain: ChainType) -> bool:
    """Return True if address is a valid wallet address for the given chain.

    Bitcoin: legacy (1...), P2SH (3...), or bech32 native SegWit (bc1...).
    Ethereum: 0x-prefixed 40-hex-char checksum or lowercase address.
    """
    if chain == ChainType.BITCOIN:
        if re.match(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,33}$", address):
            return True
        if re.match(r"^bc1[ac-hj-np-z02-9]{6,87}$", address):
            return True
        return False
    if chain == ChainType.ETHEREUM:
        return bool(re.match(r"^0x[0-9a-fA-F]{40}$", address))
    return False


# actual crypto model
class CryptoEvent(BasePaymentEvent):
    """Single model for Bitcoin and Ethereum on-chain transactions.

    chain_type is the discriminator between the two chains.
    amount_cents stores satoshis for BTC and wei for ETH — use amount_native for display.
    """

    rail: Rail = Field(default=Rail.CRYPTO)
    event_type: EventType = Field(default=EventType.SETTLEMENT)

    chain_type: ChainType

    # Crypto-specific lifecycle; also mapped to EventStatus in __init__
    crypto_status: CryptoTxStatus

    confirmations: int = Field(default=0, ge=0)

    # On-chain location (both chains)
    block_height: Optional[int] = Field(default=None, ge=0)
    block_hash: Optional[str] = Field(default=None, min_length=1, max_length=128)

    # Bitcoin transaction ID
    txid: Optional[str] = Field(default=None, min_length=1, max_length=128)

    # Ethereum transaction hash (0x-prefixed 32-byte hex)
    tx_hash: Optional[str] = Field(default=None, min_length=1, max_length=128)

    # Ethereum-only: gas consumed * gas price, in gwei
    gas_fee_gwei: Optional[int] = Field(default=None, ge=0)

    # Bitcoin UTXO counters; sender = largest input, receiver = largest output
    total_input_count: Optional[int] = Field(default=None, ge=1)
    total_output_count: Optional[int] = Field(default=None, ge=1)

    # --- Validators ---

    @field_validator("sender_id", "receiver_id", mode="before")
    @classmethod
    def strip_address(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.strip()
        return v

    @model_validator(mode="after")
    def validate_chain_fields(self) -> "CryptoEvent":
        if self.chain_type == ChainType.BITCOIN:
            if not self.txid:
                raise ValueError("txid is required for Bitcoin transactions")
            if not validate_wallet_address(self.sender_id, ChainType.BITCOIN):
                raise ValueError(f"Invalid Bitcoin address: sender_id={self.sender_id!r}")
            if not validate_wallet_address(self.receiver_id, ChainType.BITCOIN):
                raise ValueError(f"Invalid Bitcoin address: receiver_id={self.receiver_id!r}")

        elif self.chain_type == ChainType.ETHEREUM:
            if not self.tx_hash:
                raise ValueError("tx_hash is required for Ethereum transactions")
            if self.gas_fee_gwei is None:
                raise ValueError("gas_fee_gwei is required for Ethereum transactions")
            if not validate_wallet_address(self.sender_id, ChainType.ETHEREUM):
                raise ValueError(f"Invalid Ethereum address: sender_id={self.sender_id!r}")
            if not validate_wallet_address(self.receiver_id, ChainType.ETHEREUM):
                raise ValueError(f"Invalid Ethereum address: receiver_id={self.receiver_id!r}")

        return self


    @property
    def tx_reference(self) -> str:
        """The canonical linking key: txid for BTC, tx_hash for ETH."""
        return self.txid if self.chain_type == ChainType.BITCOIN else self.tx_hash  # type: ignore[return-value]

    @property
    def confirmations_required(self) -> int:
        return (
            BTC_CONFIRMATIONS_REQUIRED
            if self.chain_type == ChainType.BITCOIN
            else ETH_CONFIRMATIONS_REQUIRED
        )

    @property
    def is_settled(self) -> bool:
        return self.confirmations >= self.confirmations_required

    @property
    def amount_native(self) -> float:
        """Human-readable amount: BTC (from satoshis) or ETH (from wei)."""
        if self.chain_type == ChainType.BITCOIN:
            return self.amount_cents / 100_000_000  # satoshis → BTC
        return self.amount_cents / 10**18           # wei → ETH

   # map the lifecycle of the crypto transaction

    def __init__(self, **data: Any) -> None:
        if "status" not in data:
            crypto_status = data.get("crypto_status")
            _map = {
                CryptoTxStatus.PENDING: EventStatus.PENDING,
                CryptoTxStatus.CONFIRMED: EventStatus.SETTLED,
                CryptoTxStatus.FAILED: EventStatus.DECLINED,
                "PENDING": EventStatus.PENDING,
                "CONFIRMED": EventStatus.SETTLED,
                "FAILED": EventStatus.DECLINED,
            }
            if crypto_status in _map:
                data["status"] = _map[crypto_status]
        super().__init__(**data)
