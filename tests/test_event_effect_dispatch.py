"""Critical #6: event effect dispatcher preserves live shipped behavior."""
from __future__ import annotations

import math
from random import Random
from unittest.mock import MagicMock

from app import balance as B
from app.domain import events
from app.domain.event_apply import (
    CatastropheResolveCtx,
    InstantMinorCtx,
    apply_instant_minor,
    catastrophe_farm_mult,
    minor_farm_mult,
    minor_fog_ignores_patrol,
    minor_trade_bonus_frac,
    minor_upgrade_cost_mult,
    minor_wedding_gift_grain,
    realm_farm_mult,
    resolve_catastrophe,
)
from app.domain.events import (
    SHIPPED_CATASTROPHE_KEYS,
    SHIPPED_MINOR_KEYS,
    catastrophe_effect,
    minor_effect,
)
from app.engine import Engine


def _fief(
    fid: int,
    *,
    grain: int = 100,
    goods: int = 100,
    might: int = 20,
    name: str | None = None,
    frozen: bool = False,
) -> dict:
    return {
        "id": fid,
        "grain": grain,
        "goods": goods,
        "might": might,
        "name": name or f"F{fid}",
        "frozen": frozen,
    }


def _recording_ctx(
    fiefs: list[dict],
    *,
    barn_levels: dict[int, int] | None = None,
    tiles_by_fief: dict[int, list[dict]] | None = None,
    rng: Random | None = None,
) -> tuple[InstantMinorCtx, list[tuple], list[tuple]]:
    fief_updates: list[tuple] = []
    tile_updates: list[tuple] = []
    barns = barn_levels or {}
    tiles = tiles_by_fief or {}

    def update_fief(fid, **kwargs):
        fief_updates.append((fid, kwargs))
        for f in fiefs:
            if int(f["id"]) == int(fid):
                f.update(kwargs)

    def update_tile(tid, **kwargs):
        tile_updates.append((tid, kwargs))

    ctx = InstantMinorCtx(
        fiefs=list(fiefs),
        barn_level=lambda fid: int(barns.get(int(fid), 0)),
        fief_tiles=lambda fid: list(tiles.get(int(fid), [])),
        update_fief=update_fief,
        update_tile=update_tile,
        rng=rng or Random(0),
    )
    return ctx, fief_updates, tile_updates


def test_unshipped_catastrophes_stay_out_of_live_pool():
    assert "flood" not in SHIPPED_CATASTROPHE_KEYS
    assert "rat_king" not in SHIPPED_CATASTROPHE_KEYS
    assert "dragon_rumors" not in SHIPPED_CATASTROPHE_KEYS
    assert "black_fair" not in SHIPPED_CATASTROPHE_KEYS
    assert SHIPPED_CATASTROPHE_KEYS == frozenset({"bandit_night", "cattle_plague"})


def test_flag_minors_read_effect_tables():
    assert minor_farm_mult("harvest") == float(minor_effect("harvest")["farm_mult"])
    assert minor_farm_mult("harvest") == 1.15
    assert minor_farm_mult("drought") == float(minor_effect("drought")["farm_mult"])
    assert minor_farm_mult("fog") == 1.0
    assert minor_farm_mult("omen") == 1.0
    assert minor_farm_mult(None) == 1.0

    assert minor_upgrade_cost_mult("good_stone") == float(
        minor_effect("good_stone")["upgrade_cost_mult"]
    )
    assert minor_upgrade_cost_mult("good_stone") == 0.75
    assert minor_upgrade_cost_mult("harvest") == 1.0

    assert minor_trade_bonus_frac("fair") == float(
        minor_effect("fair")["trade_bonus_frac"]
    )
    assert minor_trade_bonus_frac("fair") == 0.05
    assert minor_trade_bonus_frac("fog") == 0.0

    assert minor_fog_ignores_patrol("fog") is True
    assert minor_fog_ignores_patrol("harvest") is False

    assert minor_wedding_gift_grain("wedding") == int(
        minor_effect("wedding")["trade_gift_grain"]
    )
    assert minor_wedding_gift_grain("wedding") == 5
    assert minor_wedding_gift_grain("fair") == 0

    assert catastrophe_farm_mult("cattle_plague") == float(
        catastrophe_effect("cattle_plague")["farm_mult"]
    )
    assert catastrophe_farm_mult("cattle_plague") == 0.375
    assert catastrophe_farm_mult("bandit_night") == 1.0

    bandit = catastrophe_effect("bandit_night")
    assert float(bandit["might_per_player"]) == 3.0
    assert int(bandit["loot_goods_per_player"]) == 12
    assert float(bandit["might_per_player"]) == B.BANDIT_NIGHT_MIGHT_PER_PLAYER
    assert int(bandit["loot_goods_per_player"]) == B.BANDIT_NIGHT_LOOT_PER_PLAYER


def test_realm_farm_mult_stacks_like_engine():
    plague = float(catastrophe_effect("cattle_plague")["farm_mult"])
    drought = float(minor_effect("drought")["farm_mult"])
    harvest = float(minor_effect("harvest")["farm_mult"])
    assert realm_farm_mult(active_minor_key=None, active_catastrophe_keys=[]) == 1.0
    assert (
        realm_farm_mult(
            active_minor_key=None, active_catastrophe_keys=["cattle_plague"]
        )
        == plague
    )
    assert (
        realm_farm_mult(
            active_minor_key="drought", active_catastrophe_keys=["cattle_plague"]
        )
        == drought * plague
    )
    assert (
        realm_farm_mult(
            active_minor_key="harvest", active_catastrophe_keys=["cattle_plague"]
        )
        == harvest * plague
    )


def test_idle_flag_minors_do_not_mutate_resources():
    fiefs = [_fief(1, grain=200, goods=200, might=30)]
    for key in (
        "harvest",
        "drought",
        "fog",
        "fair",
        "good_stone",
        "wedding",
        "omen",
    ):
        ctx, updates, tile_updates = _recording_ctx(fiefs)
        apply_instant_minor(key, ctx)
        assert updates == [], key
        assert tile_updates == [], key
        assert fiefs[0]["grain"] == 200
        assert fiefs[0]["goods"] == 200
        assert fiefs[0]["might"] == 30


def test_rats_matches_effect_table_amounts():
    eff = minor_effect("rats")
    threshold = int(eff["unprot_grain_threshold"])
    loss_frac = float(eff["loss_frac"])
    assert threshold == 80
    assert loss_frac == 0.25

    rich = _fief(1, grain=200)
    poor = _fief(2, grain=50)
    ctx, updates, _ = _recording_ctx([rich, poor], barn_levels={1: 0, 2: 0})
    apply_instant_minor("rats", ctx)

    unprot_rich = int(200 * (1.0 - B.barn_protect_frac(0)))
    assert unprot_rich > threshold
    expected_loss = max(1, int(unprot_rich * loss_frac))
    assert rich["grain"] == 200 - expected_loss
    assert poor["grain"] == 50
    assert updates == [(1, {"grain": 200 - expected_loss})]


def test_blight_spoilage_toll_press_gang_table_amounts():
    blight_frac = float(minor_effect("blight")["goods_loss_frac"])
    spoil_frac = float(minor_effect("spoilage")["grain_loss_frac"])
    toll_flat = int(minor_effect("toll")["goods_flat_loss"])
    might_loss = int(minor_effect("press_gang")["might_loss"])
    assert blight_frac == 0.225
    assert spoil_frac == 0.1875
    assert toll_flat == 15
    assert might_loss == 4

    f = _fief(1, grain=160, goods=80, might=12)
    ctx, _, _ = _recording_ctx([f])
    apply_instant_minor("blight", ctx)
    assert f["goods"] == 80 - max(1, int(80 * blight_frac))

    f = _fief(1, grain=160, goods=80, might=12)
    ctx, _, _ = _recording_ctx([f])
    apply_instant_minor("spoilage", ctx)
    assert f["grain"] == 160 - max(1, int(160 * spoil_frac))

    f = _fief(1, grain=160, goods=80, might=12)
    ctx, _, _ = _recording_ctx([f])
    apply_instant_minor("toll", ctx)
    assert f["goods"] == 80 - toll_flat

    f = _fief(1, grain=160, goods=80, might=12)
    ctx, _, _ = _recording_ctx([f])
    apply_instant_minor("press_gang", ctx)
    assert f["might"] == 12 - might_loss


def test_blight_zero_goods_and_press_gang_floor():
    f = _fief(1, goods=0, might=2)
    ctx, updates, _ = _recording_ctx([f])
    apply_instant_minor("blight", ctx)
    assert updates == []
    assert f["goods"] == 0

    ctx, _, _ = _recording_ctx([f])
    apply_instant_minor("press_gang", ctx)
    assert f["might"] == 0


def test_fire_damages_one_eligible_building():
    f = _fief(1)
    tiles = [
        {"id": 10, "building": B.BLD_MANOR, "is_overgrown": False, "damaged": False},
        {"id": 11, "building": B.BLD_FARM, "is_overgrown": False, "damaged": False},
        {"id": 12, "building": B.BLD_FARM, "is_overgrown": False, "damaged": True},
        {"id": 13, "building": B.BLD_BARN, "is_overgrown": True, "damaged": False},
    ]
    ctx, _, tile_updates = _recording_ctx(
        [f], tiles_by_fief={1: tiles}, rng=Random(1)
    )
    apply_instant_minor("fire", ctx)
    assert tile_updates == [(11, {"damaged": True})]


def test_fire_no_eligible_tiles_is_noop():
    f = _fief(1)
    tiles = [
        {"id": 10, "building": B.BLD_MANOR, "is_overgrown": False, "damaged": False},
    ]
    ctx, updates, tile_updates = _recording_ctx([f], tiles_by_fief={1: tiles})
    apply_instant_minor("fire", ctx)
    assert updates == []
    assert tile_updates == []


def test_every_shipped_minor_is_dispatchable():
    fiefs = [_fief(1, grain=200, goods=80, might=10)]
    tiles = [
        {"id": 11, "building": B.BLD_FARM, "is_overgrown": False, "damaged": False},
    ]
    for key in sorted(SHIPPED_MINOR_KEYS):
        assert key in events.MINOR_EVENTS
        ctx, _, _ = _recording_ctx(
            [dict(fiefs[0])],
            tiles_by_fief={1: [dict(tiles[0])]},
            rng=Random(0),
        )
        apply_instant_minor(key, ctx)


def test_unknown_minor_key_still_raises():
    ctx, _, _ = _recording_ctx([_fief(1)])
    try:
        apply_instant_minor("not_a_real_event", ctx)
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown minor key")


def test_bandit_night_success_threshold_and_loot():
    eff = catastrophe_effect("bandit_night")
    fiefs = [_fief(1, goods=10), _fief(2, goods=10)]
    players = 2
    threshold = int(math.ceil(float(eff["might_per_player"]) * players))
    assert threshold == int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))

    event_updates: list[tuple] = []
    fief_updates: list[tuple] = []

    def update_event(eid, **kwargs):
        event_updates.append((eid, kwargs))

    def update_fief(fid, **kwargs):
        fief_updates.append((fid, kwargs))
        for f in fiefs:
            if int(f["id"]) == int(fid):
                f.update(kwargs)

    def get_fief(fid):
        return next(f for f in fiefs if int(f["id"]) == int(fid))

    text = resolve_catastrophe(
        "bandit_night",
        CatastropheResolveCtx(
            event_id=9,
            fiefs=fiefs,
            event_actions=[
                {"fief_id": 1, "amount": threshold},
            ],
            get_fief=get_fief,
            update_fief=update_fief,
            update_event=update_event,
        ),
    )
    assert "отбита" in text
    assert event_updates == [(9, {"status": "resolved"})]
    loot_each = int(eff["loot_goods_per_player"])
    share = max(1, int((loot_each * players) // 1))
    assert fiefs[0]["goods"] == 10 + share
    assert fiefs[1]["goods"] == 10


def test_bandit_night_fail_punishes_non_contributors():
    eff = catastrophe_effect("bandit_night")
    loss_frac = float(eff["fail_unprot_grain_frac"])
    assert loss_frac == B.BANDIT_NIGHT_FAIL_GRAIN_FRAC

    fiefs = [
        _fief(1, grain=100, name="A"),
        _fief(2, grain=80, name="B"),
        _fief(3, grain=60, name="C", frozen=True),
    ]
    event_updates: list[tuple] = []

    def update_event(eid, **kwargs):
        event_updates.append((eid, kwargs))

    def update_fief(fid, **kwargs):
        for f in fiefs:
            if int(f["id"]) == int(fid):
                f.update(kwargs)

    text = resolve_catastrophe(
        "bandit_night",
        CatastropheResolveCtx(
            event_id=3,
            fiefs=fiefs,
            event_actions=[{"fief_id": 1, "amount": 1}],
            get_fief=lambda fid: next(f for f in fiefs if f["id"] == fid),
            update_fief=update_fief,
            update_event=update_event,
        ),
    )
    assert "провал" in text
    assert fiefs[0]["grain"] == 100
    assert fiefs[1]["grain"] == 80 - max(1, int(80 * loss_frac))
    assert fiefs[2]["grain"] == 60
    assert event_updates == [(3, {"status": "resolved"})]


def test_cattle_plague_resolve_message_only():
    fiefs = [_fief(1, grain=100, goods=50, might=9)]
    updates: list[tuple] = []

    text = resolve_catastrophe(
        "cattle_plague",
        CatastropheResolveCtx(
            event_id=5,
            fiefs=fiefs,
            event_actions=[],
            get_fief=lambda fid: fiefs[0],
            update_fief=lambda *a, **k: updates.append(("fief", a, k)),
            update_event=lambda eid, **kwargs: updates.append((eid, kwargs)),
        ),
    )
    assert "Мор скота отступил" in text
    assert updates == [(5, {"status": "resolved"})]
    assert fiefs[0]["grain"] == 100
    assert fiefs[0]["goods"] == 50
    assert fiefs[0]["might"] == 9


def test_engine_farm_mult_uses_dispatcher():
    db = MagicMock()
    db.get_active_events.return_value = [{"id": 1, "event_key": "cattle_plague"}]
    engine = Engine(db)
    plague = float(catastrophe_effect("cattle_plague")["farm_mult"])
    drought = float(minor_effect("drought")["farm_mult"])
    assert plague == 0.375
    assert engine._realm_farm_mult({"id": 1, "active_minor_key": None}) == plague
    assert (
        engine._realm_farm_mult({"id": 1, "active_minor_key": "drought"})
        == drought * plague
    )

    db.get_active_events.return_value = [{"id": 2, "event_key": "bandit_night"}]
    assert engine._realm_farm_mult({"id": 1, "active_minor_key": None}) == 1.0
    assert (
        engine._realm_farm_mult({"id": 1, "active_minor_key": "harvest"}) == 1.15
    )

    db.get_active_events.return_value = [
        {"id": 1, "event_key": "cattle_plague"},
        {"id": 2, "event_key": "bandit_night"},
    ]
    assert engine._realm_farm_mult({"id": 1, "active_minor_key": None}) == plague


def test_engine_instant_minor_idle_keys_no_db_writes():
    db = MagicMock()
    db.list_fiefs.return_value = [_fief(1, grain=50, goods=50, might=5)]
    engine = Engine(db)
    for key in ("harvest", "fog", "fair", "good_stone", "wedding", "omen", "drought"):
        db.reset_mock()
        db.list_fiefs.return_value = [_fief(1, grain=50, goods=50, might=5)]
        engine._apply_instant_minor(1, key)
        db.update_fief.assert_not_called()
        db.update_tile.assert_not_called()


def test_engine_instant_toll_matches_table():
    db = MagicMock()
    fief = _fief(1, goods=40)
    db.list_fiefs.return_value = [fief]

    def update_fief(fid, **kwargs):
        fief.update(kwargs)

    db.update_fief.side_effect = update_fief
    engine = Engine(db)
    engine._apply_instant_minor(1, "toll")
    flat = int(minor_effect("toll")["goods_flat_loss"])
    assert fief["goods"] == 40 - flat
