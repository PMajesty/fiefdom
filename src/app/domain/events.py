"""Мелкие события и катастрофы: таблицы контента и чистые хелперы."""
from __future__ import annotations

from random import Random
from typing import Any

from app import balance as B

MINOR_EVENTS: dict[str, dict[str, Any]] = {
    "harvest": {
        "id": "harvest",
        "name_ru": "Урожайный день",
        "canned_narrative": (
            "Солнце встало раньше петухов, и колосья налились так, будто сами "
            "просили серпа. Крестьяне бормочут про удачный знак, но прячут улыбки — "
            "лишнее зерно быстро находит хозяина с острым мечом."
        ),
        "button_labels": None,
        "mechanics": "farm_mult+25%/1d",
    },
    "fog": {
        "id": "fog",
        "name_ru": "Туман",
        "canned_narrative": (
            "Долина утонула в молочной мгле: дозорные видят лишь собственные носы. "
            "В тумане ступают тихо те, кому есть что украсть. Сегодня стена и факел "
            "мало чем помогут."
        ),
        "button_labels": None,
        "mechanics": "raids_ignore_patrol/1d",
    },
    "trader": {
        "id": "trader",
        "name_ru": "Бродячий торговец",
        "canned_narrative": (
            "У дороги остановился воз с тряпьём и пряностями; хозяин щурится и "
            "называет цены, от которых стынет кровь. Сделки шепчут в личке — "
            "на виду у долины торговаться неприлично."
        ),
        "button_labels": None,
        "mechanics": "dm_deals:2/24h",
    },
    "rats": {
        "id": "rats",
        "name_ru": "Крысы в амбарах",
        "canned_narrative": (
            "Ночью в закромах зашуршало так, будто молотили невидимым цепом. "
            "Крысы любят богатых: где зерно лежит без охраны, там пир до утра. "
            "Утром считают дыры — и проклятия."
        ),
        "button_labels": None,
        "mechanics": "unprot_grain>150:-10%",
    },
    "fair": {
        "id": "fair",
        "name_ru": "Ярмарка",
        "canned_narrative": (
            "На площади подняли шатры, зазвенели гири и чужие акценты. "
            "Сегодня всякий обмен чуть щедрее обычного — торговцы пьяны от чужой "
            "жадности и собственного вина."
        ),
        "button_labels": None,
        "mechanics": "trade_bonus+5%/1d",
    },
    "deserter": {
        "id": "deserter",
        "name_ru": "Дезертир",
        "canned_narrative": (
            "Из леса вышел оборванец с чужим щитом и взглядом человека, который "
            "уже однажды предал. Он готов служить первому, кто крикнет громче. "
            "Кто успел — тот и хозяин десяти копий."
        ),
        "button_labels": ["Взять в дружину"],
        "mechanics": "first_claim:+10_might",
    },
    "good_stone": {
        "id": "good_stone",
        "name_ru": "Хороший камень",
        "canned_narrative": (
            "В карьере нашли пласт, который ложится в стену сам, будто помнит форму. "
            "Каменщики работают дешевле и злее — боятся, что удача кончится к вечеру. "
            "Сегодня надстройки обходятся дешевле."
        ),
        "button_labels": None,
        "mechanics": "upgrade_cost-25%/1d",
    },
    "drought": {
        "id": "drought",
        "name_ru": "Засуха",
        "canned_narrative": (
            "Земля потрескалась, как старая кожа, и ручьи стали пыльными бороздами. "
            "Без полива нива отдаст едва ли две трети. У кого есть товары на воду — "
            "ещё может спасти свой край."
        ),
        "button_labels": ["Полив (10 товаров)"],
        "mechanics": "farm_mult-30%/24h;mitigate:10_goods",
    },
    "wedding": {
        "id": "wedding",
        "name_ru": "Свадьба в деревне",
        "canned_narrative": (
            "В деревне гуляют свадьбу: льётся брага, сыплются подарки и чужие долги. "
            "Кто сегодня завершит обмен, получит горсть зерна «на счастье молодых». "
            "Завтра счастье кончится, зерно — останется."
        ),
        "button_labels": None,
        "mechanics": "trade_complete:+8_grain/1d",
    },
    "omen": {
        "id": "omen",
        "name_ru": "Знамение",
        "canned_narrative": (
            "Над холмами прошла тень без тучи, и вороны сели молча, как судьи. "
            "Старики шепчут, какая беда бродит на краю долины, но цифр не называют. "
            "Механики нет — только холод в желудке."
        ),
        "button_labels": None,
        "mechanics": "foreshadow_only",
    },
}

CATASTROPHES: dict[str, dict[str, Any]] = {
    "bandit_night": {
        "id": "bandit_night",
        "name_ru": "Ночь бандитов",
        "canned_narrative": (
            "С холмов сползли огоньки факелов — чужие, слишком ровные для пастухов. "
            "Долина должна сложить силу в общий котёл, иначе грабёж выберет слабых "
            "и тех, кто отсиделся в тени."
        ),
        "button_labels": ["Внести силу"],
        "mechanics": "contribute_might;success:split_goods;fail:loot+building-1",
    },
    "flood": {
        "id": "flood",
        "name_ru": "Наводнение",
        "canned_narrative": (
            "Река вышла из берегов без спроса и лижет стены усадеб у воды. "
            "Мешки с песком стоят товаров, а чужая беда — чужая лишь до тех пор, "
            "пока волна не придёт к тебе. Платить можно и за соседа."
        ),
        "button_labels": ["Мешки с песком (15 товаров)", "Помочь соседу"],
        "mechanics": "river+adj:building-1;mitigate:15_goods;donate_ok",
    },
    "cattle_plague": {
        "id": "cattle_plague",
        "name_ru": "Мор скота",
        "canned_narrative": (
            "На выгонах падают коровы, а воздух пахнет сладкой гнилью. "
            "Поля без тягла дают половину; кто забьёт больной скот за зерно — "
            "оборвёт мор у себя раньше остальных."
        ),
        "button_labels": ["Забить скот (20 зерна)"],
        "mechanics": "farm_mult-50%/48h;mitigate:20_grain",
    },
    "rat_king": {
        "id": "rat_king",
        "name_ru": "Крысиный король",
        "canned_narrative": (
            "Тот, кто чаще всех стучал в чужие ворота, носит теперь невидимую корону. "
            "Его добыча жирнее, а имя произносят вслух — чтобы охотники знали цель. "
            "Первый удачный набег на короля срывает награду долины."
        ),
        "button_labels": None,
        "mechanics": "top_raider:+30%_loot/48h;bounty:players*5_goods",
    },
    "dragon_rumors": {
        "id": "dragon_rumors",
        "name_ru": "Драконьи слухи",
        "canned_narrative": (
            "Пастухи клянутся, что в расселине дышит золотом нечто огромное. "
            "Нужны смельчаки и общая сила — иначе вернутся голодными и позорными. "
            "Сокровище делят только выжившие в общем замысле."
        ),
        "button_labels": ["Вступить в поход (10 силы)"],
        "mechanics": "pledge:10_might;min_players:3;threshold:40_might",
    },
    "black_fair": {
        "id": "black_fair",
        "name_ru": "Чёрная ярмарка",
        "canned_narrative": (
            "Под мостом шепчутся про шашки дыма и чужие запасы, записанные на ладони. "
            "Каждому суют одно грязное предложение — за товары, без свидетелей. "
            "Честь здесь товар самый дешёвый."
        ),
        "button_labels": ["Открыть предложение"],
        "mechanics": "dm_shady_offer:1/24h",
    },
}

_MINOR_EFFECTS: dict[str, dict[str, Any]] = {
    "harvest": {"farm_mult": 1.25, "duration_hours": 24},
    "fog": {"raids_ignore_patrol": True, "duration_hours": 24},
    "trader": {"dm_deals": 2, "duration_hours": 24},
    "rats": {"unprot_grain_threshold": 150, "loss_frac": 0.10},
    "fair": {"trade_bonus_frac": 0.05, "duration_hours": 24},
    "deserter": {"first_claim_might": 10, "group_message": True},
    "good_stone": {"upgrade_cost_mult": 0.75, "duration_hours": 24},
    "drought": {
        "farm_mult": 0.70,
        "duration_hours": 24,
        "mitigate": {"goods": 10, "action": "полив"},
    },
    "wedding": {"trade_gift_grain": 8, "duration_hours": 24},
    "omen": {"foreshadow": True},
}

_CATASTROPHE_EFFECTS: dict[str, dict[str, Any]] = {
    "bandit_night": {
        "might_per_player": B.BANDIT_NIGHT_MIGHT_PER_PLAYER,
        "loot_goods_per_player": B.BANDIT_NIGHT_LOOT_PER_PLAYER,
        "fail_unprot_grain_frac": 0.15,
        "fail_lowest_defense_count": 2,
        "fail_worst_building_delta": -1,
    },
    "flood": {
        "targets": "river_and_orthogonal_neighbors",
        "building_delta": -1,
        "mitigate": {"goods": 15, "action": "мешки_с_песком"},
        "donate_allowed": True,
    },
    "cattle_plague": {
        "farm_mult": 0.50,
        "duration_hours": 48,
        "mitigate": {"grain": 20, "action": "забить_скот"},
    },
    "rat_king": {
        "loot_bonus_frac": 0.30,
        "duration_hours": 48,
        "select": "most_raid_attempts_this_week",
        "bounty_goods_per_player": 5,
    },
    "dragon_rumors": {
        "pledge_might": 10,
        "min_players": 3,
        "success_might_threshold": 40,
        "fail_hunger_hours": 24,
    },
    "black_fair": {
        "dm_offers": 1,
        "duration_hours": 24,
        "offer_kinds": ("smoke_bomb", "denunciation"),
    },
}


def roll_minor_event(rng: Random) -> str | None:
    """С вероятностью MINOR_EVENT_CHANCE возвращает ключ события, иначе тихий день."""
    if rng.random() >= B.MINOR_EVENT_CHANCE:
        return None
    return rng.choice(list(MINOR_EVENTS.keys()))


def pick_catastrophe(rng: Random, last_key: str | None) -> str:
    keys = [k for k in CATASTROPHES if k != last_key]
    if not keys:
        keys = list(CATASTROPHES.keys())
    return rng.choice(keys)


def next_catastrophe_delay_days(rng: Random) -> int:
    return rng.randint(B.CATASTROPHE_MIN_DAYS, B.CATASTROPHE_MAX_DAYS)


def minor_effect(key: str) -> dict[str, Any]:
    if key not in _MINOR_EFFECTS:
        raise KeyError(f"Неизвестное мелкое событие: {key}")
    return dict(_MINOR_EFFECTS[key])


def catastrophe_effect(key: str) -> dict[str, Any]:
    if key not in _CATASTROPHE_EFFECTS:
        raise KeyError(f"Неизвестная катастрофа: {key}")
    return dict(_CATASTROPHE_EFFECTS[key])
