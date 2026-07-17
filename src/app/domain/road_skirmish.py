"""Дорожный бой и коалиции перед осадой (чистая логика без БД)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app import balance as B


@dataclass(frozen=True)
class RaidStack:
    intent_id: int
    fief_id: int
    might: int
    pact_id: int | None = None
    open_truce: bool = False


@dataclass
class Coalition:
    key: str
    members: list[RaidStack] = field(default_factory=list)

    @property
    def might(self) -> int:
        return sum(max(0, int(m.might)) for m in self.members)

    @property
    def lead_fief_id(self) -> int:
        """Лид коалиции: наибольший вклад, при равенстве меньший fief_id."""
        best = self.members[0]
        for m in self.members[1:]:
            if m.might > best.might or (
                m.might == best.might and m.fief_id < best.fief_id
            ):
                best = m
        return int(best.fief_id)


@dataclass(frozen=True)
class MemberRoadFate:
    intent_id: int
    fief_id: int
    commit: int
    road_deaths: int
    fled: bool
    siege_eligible: bool


@dataclass
class RoadContestResult:
    """Итог дороги по одной жертве."""

    member_fates: list[MemberRoadFate]
    siege_coalition_key: str | None
    siege_pool: int
    public_road_line: str = ""


def coalition_key_for(stack: RaidStack) -> str:
    if stack.pact_id is not None:
        return f"pact:{int(stack.pact_id)}"
    if stack.open_truce:
        return "truce"
    return f"solo:{int(stack.fief_id)}"


def build_coalitions(stacks: list[RaidStack]) -> list[Coalition]:
    """Пакт важнее open_truce; без пакта opt-in truce сливаются в одну кучу."""
    by_key: dict[str, Coalition] = {}
    for stack in stacks:
        key = coalition_key_for(stack)
        coal = by_key.get(key)
        if coal is None:
            coal = Coalition(key=key)
            by_key[key] = coal
        coal.members.append(stack)
    # Стабильный порядок: по ключу, внутри - по intent_id.
    out = sorted(by_key.values(), key=lambda c: c.key)
    for coal in out:
        coal.members.sort(key=lambda m: int(m.intent_id))
    return out


def _loser_deaths(commit: int) -> int:
    if commit <= 0:
        return 0
    raw = int(round(commit * float(B.RAID_ROAD_LOSS_FRAC)))
    return min(commit, max(1, raw))


def _split_pool_proportional(
    commits: list[int], pool: int
) -> list[int]:
    """Крупнейший остаток: суммы долей = pool, каждая доля ≤ commit."""
    n = len(commits)
    if n == 0:
        return []
    total = sum(commits)
    if pool <= 0 or total <= 0:
        return [0] * n
    pool = min(pool, total)
    exact = [pool * c / total for c in commits]
    base = [min(commits[i], int(exact[i])) for i in range(n)]
    rem = pool - sum(base)
    order = sorted(
        range(n),
        key=lambda i: (-(exact[i] - base[i]), -commits[i], i),
    )
    for i in order:
        if rem <= 0:
            break
        room = commits[i] - base[i]
        if room <= 0:
            continue
        take = min(room, rem)
        base[i] += take
        rem -= take
    return base


def resolve_road_contest(
    coalitions: list[Coalition],
    *,
    victim_label: str = "",
) -> RoadContestResult:
    """Бегство < half max; tie bounce 25%; иначе лидер минус смерти проигравших."""
    if not coalitions:
        return RoadContestResult([], None, 0)

    live = [c for c in coalitions if c.might > 0]
    if not live:
        return RoadContestResult([], None, 0)

    max_might = max(c.might for c in live)
    flee_cut = float(B.RAID_ROAD_FLEE_FRAC) * max_might
    fled = [c for c in live if c.might < flee_cut]
    rivals = [c for c in live if c.might >= flee_cut]

    fates: list[MemberRoadFate] = []
    for coal in fled:
        for m in coal.members:
            fates.append(
                MemberRoadFate(
                    intent_id=int(m.intent_id),
                    fief_id=int(m.fief_id),
                    commit=int(m.might),
                    road_deaths=0,
                    fled=True,
                    siege_eligible=False,
                )
            )

    if not rivals:
        line = ""
        if fled and victim_label:
            line = f"К хутору {victim_label} мелкие отряды развернулись на дороге"
        return RoadContestResult(fates, None, 0, line)

    if len(rivals) == 1:
        leader = rivals[0]
        for m in leader.members:
            fates.append(
                MemberRoadFate(
                    intent_id=int(m.intent_id),
                    fief_id=int(m.fief_id),
                    commit=int(m.might),
                    road_deaths=0,
                    fled=False,
                    siege_eligible=True,
                )
            )
        return RoadContestResult(
            fates, leader.key, leader.might, ""
        )

    top = max(c.might for c in rivals)
    tied = [c for c in rivals if c.might == top]
    if len(tied) > 1:
        # Ничья за лидерство: все tied платят налог, осады нет.
        loser_set = {c.key for c in rivals}
        for coal in rivals:
            for m in coal.members:
                deaths = _loser_deaths(int(m.might)) if coal.key in loser_set else 0
                fates.append(
                    MemberRoadFate(
                        intent_id=int(m.intent_id),
                        fief_id=int(m.fief_id),
                        commit=int(m.might),
                        road_deaths=deaths,
                        fled=False,
                        siege_eligible=False,
                    )
                )
        line = (
            f"На дороге к хутору {victim_label} отряды столлись вничью"
            if victim_label
            else "На дороге отряды сошлись вничью"
        )
        return RoadContestResult(fates, None, 0, line)

    leader = tied[0]
    losers = [c for c in rivals if c.key != leader.key]
    loser_death_sum = 0
    for coal in losers:
        for m in coal.members:
            deaths = _loser_deaths(int(m.might))
            loser_death_sum += deaths
            fates.append(
                MemberRoadFate(
                    intent_id=int(m.intent_id),
                    fief_id=int(m.fief_id),
                    commit=int(m.might),
                    road_deaths=deaths,
                    fled=False,
                    siege_eligible=False,
                )
            )

    siege_pool = max(0, leader.might - loser_death_sum)
    commits = [int(m.might) for m in leader.members]
    survivors = _split_pool_proportional(commits, siege_pool)
    for m, surv in zip(leader.members, survivors):
        deaths = max(0, int(m.might) - int(surv))
        fates.append(
            MemberRoadFate(
                intent_id=int(m.intent_id),
                fief_id=int(m.fief_id),
                commit=int(m.might),
                road_deaths=deaths,
                fled=False,
                siege_eligible=siege_pool > 0,
            )
        )

    if siege_pool <= 0:
        line = (
            f"На дороге к хутору {victim_label} все силы выбиты"
            if victim_label
            else "На дороге все силы выбиты"
        )
        return RoadContestResult(fates, None, 0, line)

    line = ""
    if losers and victim_label:
        line = f"На дороге к хутору {victim_label} отряды схватились"
    return RoadContestResult(fates, leader.key, siege_pool, line)


def split_loot_by_commit(
    commits: dict[int, int],
    stolen: dict[str, int],
) -> dict[int, dict[str, int]]:
    """Делёж добычи по доле commit; остаток - наибольшему commit, затем меньшему id."""
    ids = sorted(
        commits.keys(),
        key=lambda i: (-int(commits[i]), int(i)),
    )
    total = sum(max(0, int(commits[i])) for i in ids)
    if total <= 0 or not ids:
        return {i: {k: 0 for k in stolen} for i in ids}

    out: dict[int, dict[str, int]] = {
        i: {k: 0 for k in stolen} for i in ids
    }
    for key, amount in stolen.items():
        amt = max(0, int(amount))
        if amt <= 0:
            continue
        shares = _split_pool_proportional(
            [max(0, int(commits[i])) for i in ids], amt
        )
        for i, share in zip(ids, shares):
            out[i][key] = int(share)
    return out
