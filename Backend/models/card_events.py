# this file will have to do with the card payment events such as 3 main classes:
# BasePaymentEvent, CardAuthEvent, CardSettlementEvent
# these are event pydantic models

# imports requried

from email.policy import default
import hashlib
from datetime import datetime, timezone
from enum import Enum
from locale import currency
from mimetypes import init
from sqlite3.dbapi2 import Timestamp
from timeit import default_timer
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

# class repersenting all of the different rails of payment (ACH, WIRE, CRYPTO, CARD)
class Rail(str, Enum):
    ACH = "ACH"
    WIRE = "WIRE"
    CARD = "CARD"
    CRYPTO = "CRYPTO"


# class for the types of event it is in (auth or settlement)

class EventType(str, Enum):
    AUTH = "AUTH"
    SETTLEMENT = "SETTLEMENT"

# class regarding event status is in in pending, settled, declined, orphaned
class EventStatus(str, Enum):
    PENDING = "PENDING" # auth received but not settled
    SETTLED = "SETTLED" # settlement received and matched
    DECLINED = "DECLINED" # auth was declined
    ORPHANED = "ORPHANED" # settlement arrived but did not have a mathing auth


# Base, this is shared across all of the rails (card, crypto, wire, ACH)

class BasePaymentEvent(BaseModel):
    event_id: UUID=Field(default_factory=uuid4)
    schema_version: int=Field(default=1)
    rail: Rail
    event_type: EventType
    status: EventStatus

    # sender and receivers
    sender_id: str=Field(..., min_length=1, max_length=256)
    receiver_id: str=Field(..., min_length=1, max_length=256)

    # have the amount in the smallest denomination possible for most accurate repersentation
    amount_cents: int=Field(..., gt=0)
    currency: str=Field(default="CAD", min_length=3, max_length=3)

    # always have UTC time for timestamps
    timestamp_utc: datetime

    # the original untouched payload
    raw_payload: dict[str, Any] = Field(default_factor=dict)

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def enforcu_utc(cls, v: Any) -> datetime:
        # only have UTC no native times
        if isinstance(v, datetime):
            if v.tzinfo is None:
                raise ValueError(
                    "timestamp_utc must be timezone-aware (UTC)."
                )
        return v

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.upper()
    
    @property
    def amount_dollars(self) -> float:
        return self.amount_cents / 100
    
    # uses Rail.card instead of card
    class Config:
        use_enum_values = False

# card auth event, this happenes when the card transaction happens (tap/swipe)
class CardAuthEvent(BasePaymentEvent):
    rail: Rail=Field(default=Rail.CARD)
    event_type: EventType=Field(default=EventType.AUTH)

    # auth code generated to auth time shown to settlement event
    authorization_code: str=Field(min_length=6, max_length=6)

    # if approved is false, means declined
    approved: bool

    #where is the card used irl or online
    terminal_id: str=Field(..., min_length=1, max_length=64)

    # merchant category code
    merchant_category_code: str=Field(..., min_length=4, max_length=4)

    # merchant name readable by a human
    merchant_name: str=Field(..., min_length=1, max_length=128) # 128 is here for now we can change later

    @field_validator("authorization_code")
    @classmethod
    def upper_auth(cls, v: str) -> str:
        return v.upper()

    
    @field_validator("merchant_category_code")
    @classmethod
    def validate_merchant_code(cls, v:str) -> str:
        if not v.isdigit():
            raise ValueError("Merchant Category Code must be 4 digits")
        return v

    def __init__(self, **data: Any) -> None:
        if "status" not in data:
            data["status"] = (
                EventStatus.PENDING if data.get("approved", True)
                else EventStatus.DECLINED
            )
        super().__init__(**data)


# card settlement event after auth has been changed to approved. Generally happens 1-3 days after the auth (this is when the money actually moves)
# if no matching auth then orphaned

class CardSettlementEvent(BasePaymentEvent):
    rail: Rail=Field(default=Rail.CARD)
    event_type: EventType=Field(default=EventType.SETTLEMENT)
    status: EventStatus=Field(default=EventStatus.SETTLED)

    # auth codes must match
    authorization_code: str=Field(..., min_length=6, max_length=6)

    # this is the final amount to be paid
    settlement_amount_cents: int=Field(..., gt=0)

    # when settlement is actualy processed
    settled_at_utc: datetime

    @field_validator("settled_at_utc", mode="before")
    @classmethod
    def enforcu_utc(cls, v: Any) -> datetime:
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("settled at utc must be timezone aware (UTC)")
        return v

    
    @property
    def amount_difference_cents(self) -> int:
        # this helpes with knowing diff between the two amounts. if > 0 added tip or more then auth. if < 0 then rounding or refund
        return self.settlement_amount_cents - self.amount_cents

# PAN hashing, one way hash of a card number 
def hash_pan(pan: str) -> str:
    clean = pan.replace(" ", "").replace("-", "")
    return hashlib.sha256(clean.encode()).hexdigest()


    

