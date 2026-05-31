from models.ach_events import ACHCreditEvent, ACHDebitEvent


def normalize_ach(payload: dict) -> ACHCreditEvent | ACHDebitEvent:
    raise NotImplementedError
