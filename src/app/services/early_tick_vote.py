"""Голосование за досрочный тик континента."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app import balance as B
from app.config import tick_slots
from app.domain.early_tick_vote import (
    can_consume_next_wall_slot,
    early_tick_deadline,
    is_active_voter,
    midpoint_override_on_lock,
    next_wall_slot_target,
    quorum_needed,
    vote_button_visible,
    votes_meet_quorum,
)
from app.domain.tick_schedule import (
    next_tick_datetime,
    play_window_bounds,
    raid_declare_midpoint,
)
from app.engine import _as_date, _utcnow
from app.repos import EarlyTickVoteRepos


@dataclass(frozen=True)
class EarlyTickVoteView:
    """Снимок для кнопки дома и ответа на callback."""

    show_button: bool
    voted: bool
    vote_count: int
    active_count: int
    quorum: int
    early_locked: bool
    label: str


@dataclass(frozen=True)
class EarlyTickVoteResult:
    """Итог cast/unvote: текст алерта и опциональная рассылка при lock."""

    alert: str
    locked: bool = False
    notify_user_ids: tuple[int, ...] = ()
    early_tick_at: datetime | None = None


class EarlyTickVoteService:
    def __init__(self, engine, db: EarlyTickVoteRepos) -> None:
        self._engine = engine
        self._db = db

    def _world_for_fief(self, fief: dict) -> dict:
        realm = self._db.get_realm(int(fief["realm_id"])) or {}
        world_id = realm.get("world_id")
        if world_id is None:
            raise ValueError("Долина не привязана к континенту")
        world = self._db.get_world(int(world_id))
        if not world:
            raise ValueError("Континент не найден")
        return world

    def active_fiefs(self, world_id: int) -> list[dict]:
        out: list[dict] = []
        for fief in self._db.list_fiefs_by_world(int(world_id)):
            if fief.get("frozen"):
                continue
            if is_active_voter(
                int(fief.get("actions") or 0),
                actions_max=int(B.ACTIONS_BANK_MAX),
            ):
                out.append(fief)
        return out

    def scheduled_next_tick_local(self, world: dict) -> datetime | None:
        local_now = self._engine._world_local_now(world)
        return next_tick_datetime(
            local_now=local_now,
            last_tick_local_date=_as_date(world.get("last_tick_local_date")),
            last_tick_slot=(
                int(world["last_tick_slot"])
                if world.get("last_tick_slot") is not None
                else None
            ),
            slots=tick_slots(),
        )

    def vote_view(self, fief_id: int) -> EarlyTickVoteView:
        fief = self._db.get_fief(int(fief_id))
        if not fief:
            return EarlyTickVoteView(
                show_button=False,
                voted=False,
                vote_count=0,
                active_count=0,
                quorum=2,
                early_locked=False,
                label="",
            )
        world = self._world_for_fief(fief)
        wid = int(world["id"])
        local_now = self._engine._world_local_now(world)
        early_locked = world.get("early_tick_at") is not None
        active = self.active_fiefs(wid)
        active_ids = {int(f["user_id"]) for f in active}
        votes = [
            uid
            for uid in self._db.list_early_tick_votes(wid)
            if uid in active_ids
        ]
        user_id = int(fief["user_id"])
        player_active = user_id in active_ids
        scheduled = self.scheduled_next_tick_local(world)
        show = player_active and vote_button_visible(
            next_tick_at=scheduled,
            now=local_now,
            early_locked=early_locked,
        )
        voted = user_id in set(votes)
        quorum = quorum_needed(len(active))
        if voted:
            label = f"Отменить голос ({len(votes)}/{quorum})"
        else:
            label = f"Тик раньше ({len(votes)}/{quorum})"
        return EarlyTickVoteView(
            show_button=show,
            voted=voted,
            vote_count=len(votes),
            active_count=len(active),
            quorum=quorum,
            early_locked=early_locked,
            label=label,
        )

    def toggle_vote(self, fief_id: int, user_id: int) -> EarlyTickVoteResult:
        fief = self._engine.require_owned_fief(int(fief_id), int(user_id))
        world = self._world_for_fief(fief)
        wid = int(world["id"])
        if world.get("early_tick_at") is not None:
            return EarlyTickVoteResult(
                alert="Досрочный тик уже назначен. Ждите колокола."
            )
        local_now = self._engine._world_local_now(world)
        scheduled = self.scheduled_next_tick_local(world)
        if not vote_button_visible(
            next_tick_at=scheduled,
            now=local_now,
            early_locked=False,
        ):
            return EarlyTickVoteResult(
                alert="До планового тика меньше 20 минут - голосовать поздно."
            )
        if not is_active_voter(
            int(fief.get("actions") or 0),
            actions_max=int(B.ACTIONS_BANK_MAX),
        ):
            return EarlyTickVoteResult(
                alert="Голосовать могут только те, у кого запас действий не полон."
            )

        votes = set(self._db.list_early_tick_votes(wid))
        if int(user_id) in votes:
            self._db.remove_early_tick_vote(wid, int(user_id))
            view = self.vote_view(int(fief_id))
            return EarlyTickVoteResult(
                alert=f"Голос снят ({view.vote_count}/{view.quorum})."
            )

        self._db.add_early_tick_vote(wid, int(user_id))
        return self._maybe_lock(wid)

    def reconcile_quorum(self, world_id: int) -> EarlyTickVoteResult | None:
        """Если кворум уже есть (в т.ч. после сужения активных) - закрепить досрок."""
        world = self._db.get_world(int(world_id)) or {}
        if world.get("early_tick_at") is not None:
            return None
        result = self._maybe_lock(int(world_id))
        return result if result.locked else None

    def _maybe_lock(self, world_id: int) -> EarlyTickVoteResult:
        world = self._db.get_world(int(world_id)) or {}
        if world.get("early_tick_at") is not None:
            return EarlyTickVoteResult(
                alert="Досрочный тик уже назначен. Ждите колокола."
            )
        local_now = self._engine._world_local_now(world)
        scheduled = self.scheduled_next_tick_local(world)
        # Нельзя ставить now+20 поверх более раннего планового слота.
        if not vote_button_visible(
            next_tick_at=scheduled,
            now=local_now,
            early_locked=False,
        ):
            active = self.active_fiefs(int(world_id))
            votes = [
                uid
                for uid in self._db.list_early_tick_votes(int(world_id))
                if uid in {int(f["user_id"]) for f in active}
            ]
            quorum = quorum_needed(len(active))
            return EarlyTickVoteResult(
                alert=f"Голос учтён ({len(votes)}/{quorum})."
            )
        active = self.active_fiefs(int(world_id))
        active_ids = {int(f["user_id"]) for f in active}
        votes = [
            uid
            for uid in self._db.list_early_tick_votes(int(world_id))
            if uid in active_ids
        ]
        quorum = quorum_needed(len(active))
        if not votes_meet_quorum(len(votes), len(active)):
            return EarlyTickVoteResult(
                alert=f"Голос учтён ({len(votes)}/{quorum})."
            )

        now = _utcnow()
        early_at = early_tick_deadline(now)
        # Середину считаем по плановому окну (до записи early_tick_at).
        scheduled_bounds = self._scheduled_play_bounds(world)
        current_mid = (
            raid_declare_midpoint(scheduled_bounds) if scheduled_bounds else None
        )
        # Сжимаем до +10м, иначе закрепляем исходную середину, чтобы укороченное
        # окно play не сдвинуло half-tick геометрией.
        override_local = midpoint_override_on_lock(
            now=local_now,
            current_midpoint=current_mid,
        )
        if override_local is None and current_mid is not None and local_now < current_mid:
            override_local = current_mid
        override_utc = (
            override_local.astimezone(timezone.utc)
            if override_local is not None
            else None
        )
        with self._db.transaction():
            fresh = self._db.get_world(int(world_id)) or {}
            if fresh.get("early_tick_at") is not None:
                return EarlyTickVoteResult(
                    alert="Досрочный тик уже назначен. Ждите колокола."
                )
            fields: dict = {"early_tick_at": early_at}
            if override_utc is not None:
                fields["declare_midpoint_at"] = override_utc
            self._db.update_world(int(world_id), **fields)

        notify = tuple(sorted(active_ids))
        local_early = early_at.astimezone(local_now.tzinfo)
        when = local_early.strftime("%H:%M")
        return EarlyTickVoteResult(
            alert=f"Кворум! Следующий тик в {when}.",
            locked=True,
            notify_user_ids=notify,
            early_tick_at=early_at,
        )

    def clear_vote_state(self, world_id: int) -> None:
        """Сброс голосов и досрочных меток после любого тика."""
        with self._db.transaction():
            self._db.clear_early_tick_votes(int(world_id))
            self._db.update_world(
                int(world_id),
                early_tick_at=None,
                declare_midpoint_at=None,
                early_tick_pending_slot=None,
            )

    def early_tick_due(self, world: dict, *, utc_now: datetime | None = None) -> bool:
        early = self._as_aware_utc(world.get("early_tick_at"))
        if early is None:
            return False
        now = utc_now if utc_now is not None else _utcnow()
        return now >= early

    def tick_slot_for_early_fire(self, world: dict) -> int | None:
        """Индекс слота, если досрок съедает конец текущего окна; иначе None."""
        local_now = self._engine._world_local_now(world)
        slots = tick_slots()
        last_date = _as_date(world.get("last_tick_local_date"))
        last_slot = (
            int(world["last_tick_slot"])
            if world.get("last_tick_slot") is not None
            else None
        )
        if not can_consume_next_wall_slot(
            local_now=local_now,
            last_tick_local_date=last_date,
            last_tick_slot=last_slot,
            slots=slots,
        ):
            return None
        target = next_wall_slot_target(
            local_now=local_now,
            last_tick_local_date=last_date,
            last_tick_slot=last_slot,
            slots=slots,
        )
        if target is None:
            return None
        return int(target[1])

    def arm_early_tick_fire(self, world_id: int, tick_slot: int | None) -> None:
        """Запомнить слот досрок-тика на случай crash mid-resolve."""
        self._db.update_world(
            int(world_id),
            early_tick_pending_slot=(
                int(tick_slot) if tick_slot is not None else None
            ),
        )

    def pending_early_tick_slot(self, world: dict) -> int | None:
        raw = world.get("early_tick_pending_slot")
        if raw is None:
            return None
        return int(raw)

    def lock_announcement_text(self, early_tick_at: datetime, world: dict) -> str:
        local_now = self._engine._world_local_now(world)
        local_early = early_tick_at.astimezone(local_now.tzinfo)
        when = local_early.strftime("%H:%M")
        return (
            f"📯 Досрочный тик: все активные проголосовали. "
            f"Следующий ход континента около {when} "
            f"(через 20 минут)."
        )

    def _scheduled_play_bounds(
        self, world: dict
    ) -> tuple[datetime, datetime] | None:
        """Окно play без early_tick_at - для решения о mid-override при lock."""
        world = self._engine.ensure_play_opened_at(int(world["id"]))
        local_now = self._engine._world_local_now(world)
        opened = self._as_aware_utc(world.get("play_opened_at"))
        if opened is None:
            return None
        opened_local = opened.astimezone(local_now.tzinfo)
        next_at = self.scheduled_next_tick_local(world)
        return play_window_bounds(opened_local, next_at)

    def _as_aware_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
