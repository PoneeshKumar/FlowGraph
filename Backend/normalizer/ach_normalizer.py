from typing import Union
from models.ach_events import ACHCreditEvent, ACHDebitEvent


def normalize_ach(payload: dict) -> Union[ACHCreditEvent, ACHDebitEvent]:
    raise NotImplementedError
