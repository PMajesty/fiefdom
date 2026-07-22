"""In-process pending FSM for DM/callback flows."""
from __future__ import annotations

KIND_SEND_PICK = "send_pick"
KIND_SEND_TARGET = "send_target"
KIND_SEND_RESOURCE = "send_resource"
KIND_SEND_AMOUNT = "send_amount"
KIND_SEND_CONFIRM = "send_confirm"
KIND_RAID_MIGHT = "raid_might"
KIND_RAID_CONFIRM = "raid_confirm"
KIND_PACT_NAME = "pact_name"
KIND_PACT_INVITE = "pact_invite"
KIND_DISBAND_KEEP = "disband_keep"


class PendingStore:
    def __init__(self) -> None:
        self._actions: dict[int, dict] = {}

    def get(self, user_id: int) -> dict | None:
        return self._actions.get(user_id)

    def set(self, user_id: int, data: dict) -> None:
        self._actions[user_id] = data

    def clear(self, user_id: int) -> None:
        self._actions.pop(user_id, None)


pending_store = PendingStore()
