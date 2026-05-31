# this file will have to do with the ach transactions using pydantic models

# necessary imports

import hashlib
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import Field, field_validator

from models.card_events import BasePaymentEvent, EventStatus, EventType, Rail

# enum classes needed
class ACHEventType(str, Enum):
    ACH_CREDIT = "ACH_CREDIT"  # money pushed to receiver (payroll, direct deposit)
    ACH_DEBIT = "ACH_DEBIT"    # money pulled from sender (bill pay, Venmo backend)


class SECCode(str, Enum):
    PPD = "PPD"  # prearranged payment and deposit (personal)
    CIB = "CIB"  # corporate internet banking
    WEB = "WEB"  # internet-initiated
    TEL = "TEL"  # phone-initiated

# a hashed account number helper
def hash_account_number(account_number: str, routing_number: str) -> str:
    clean_account = account_number.replace(" ", "").replace("-", "")
    clean_routing = routing_number.replace(" ", "").replace("-", "")
    combined = f"{clean_account}:{clean_routing}"
    return hashlib.sha256(combined.encode()).hexdigest()


class BaseACHEvent(BasePaymentEvent):
    rail: Rail = Field(default=Rail.ACH)
    event_type: EventType = Field(default=EventType.PENDING)

    ach_event_type: ACHEventType

    # 15-digit unique trace number per transaction within a NACHA batch
    trace_number: str = Field(..., min_length=15, max_length=15)

    # batch grouping from NACHA file
    batch_id: str = Field(..., min_length=1, max_length=128)
    batch_submission_time: datetime

    # originating depository financial institution routing number
    odfi_routing: str = Field(..., min_length=9, max_length=9)

    # receiving depository financial institution routing number
    rdfi_routing: str = Field(..., min_length=9, max_length=9)

    sec_code: SECCode
    sender_account_hash:   str = Field(..., min_length=64, max_length=64)
    receiver_account_hash: str = Field(..., min_length=64, max_length=64)
    # when to expect settlement
    expected_settlement_date: date | None = Field(default=None)

    # only populated when the transaction is returned
    return_code: Optional[str] = Field(default=None, min_length=3, max_length=3)
    return_reason: Optional[str] = Field(default=None, min_length=1, max_length=256)

    @field_validator("trace_number")
    @classmethod
    def validate_trace_number(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("trace_number must be exactly 15 digits")
        return v

    @field_validator("odfi_routing", "rdfi_routing")
    @classmethod
    def validate_routing_number(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("routing number must be exactly 9 digits")
        return v

    @field_validator("batch_submission_time", mode="before")
    @classmethod
    def enforce_utc(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("batch_submission_time must be timezone-aware (UTC)")
        return v

    def __init__(self, **data: Any) -> None:
        if "status" not in data:
            # returned transactions are treated as declined; everything else is pending
            return_code = data.get("return_code")
            data["status"] = EventStatus.DECLINED if return_code else EventStatus.PENDING
        super().__init__(**data)


class ACHCreditEvent(BaseACHEvent):
    """Money pushed to receiver — payroll, direct deposit, Venmo/CashApp payout."""

    ach_event_type: ACHEventType = Field(default=ACHEventType.ACH_CREDIT)


class ACHDebitEvent(BaseACHEvent):
    """Money pulled from sender — bill pay, subscription, Venmo/CashApp collection."""

    ach_event_type: ACHEventType = Field(default=ACHEventType.ACH_DEBIT)
