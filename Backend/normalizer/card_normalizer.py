from models.card_events import CardAuthEvent, CardSettlementEvent, EventType


def normalize_card(payload: dict) -> CardAuthEvent | CardSettlementEvent:
    raise NotImplementedError
