from typing import Union
from models.card_events import CardAuthEvent, CardSettlementEvent, EventType


def normalize_card(payload: dict) -> Union[CardAuthEvent, CardSettlementEvent]:
    raise NotImplementedError
