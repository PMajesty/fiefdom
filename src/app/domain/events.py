"""Мелкие события и катастрофы: таблицы контента и чистые хелперы."""
from __future__ import annotations

from random import Random
from typing import Any

from app import balance as B

MINOR_EVENTS: dict[str, dict[str, Any]] = {
    "harvest": {
        "id": "harvest",
        "name_ru": "Урожайный день",
        "digest_line": "Урожайный день - фермы дают больше зерна.",
        "canned_narrative": (
            "Солнце встало раньше петухов, и колосья налились так, будто сами "
            "просили серпа. Крестьяне бормочут про удачный знак, но прячут улыбки - "
            "лишнее зерно быстро находит хозяина с острым мечом."
        ),
        "button_labels": None,
        "mechanics": "farm_mult+15%/1t",
        "tone": "good",
    },
    "fog": {
        "id": "fog",
        "name_ru": "Туман",
        "digest_line": "Туман - дозор сегодня почти бесполезен.",
        "canned_narrative": (
            "Долина утонула в молочной мгле: дозорные видят лишь собственные носы. "
            "В тумане ступают тихо те, кому есть что украсть. Сегодня стена и факел "
            "мало чем помогут."
        ),
        "button_labels": None,
        "mechanics": "raids_ignore_patrol/1t",
        "tone": "bad",
    },
    "trader": {
        "id": "trader",
        "name_ru": "Бродячий торговец",
        "digest_line": "Бродячий торговец - особые сделки ждут в личке.",
        "canned_narrative": (
            "У дороги остановился воз с тряпьём и пряностями; хозяин щурится и "
            "называет цены, от которых стынет кровь. Сделки шепчут в личке - "
            "на виду у долины торговаться неприлично."
        ),
        "button_labels": None,
        "mechanics": "dm_deals:2/1t",
        "tone": "good",
    },
    "rats": {
        "id": "rats",
        "name_ru": "Крысы в амбарах",
        "digest_line": "Крысы в амбарах - незащищённое зерно под угрозой.",
        "canned_narrative": (
            "Ночью в закромах зашуршало так, будто молотили невидимым цепом. "
            "Крысы любят богатых: где зерно лежит без охраны, там пир до утра. "
            "Утром считают дыры - и проклятия."
        ),
        "button_labels": None,
        "mechanics": "unprot_grain>80:-20%",
        "tone": "bad",
    },
    "fair": {
        "id": "fair",
        "name_ru": "Ярмарка",
        "digest_line": "Ярмарка - обмены сегодня чуть выгоднее.",
        "canned_narrative": (
            "На площади подняли шатры, зазвенели гири и чужие акценты. "
            "Сегодня всякий обмен чуть щедрее обычного - торговцы пьяны от чужой "
            "жадности и собственного вина."
        ),
        "button_labels": None,
        "mechanics": "trade_bonus+5%/1t",
        "tone": "good",
    },
    "good_stone": {
        "id": "good_stone",
        "name_ru": "Хороший камень",
        "digest_line": "Хороший камень - надстройки сегодня дешевле.",
        "canned_narrative": (
            "В карьере нашли пласт, который ложится в стену сам, будто помнит форму. "
            "Каменщики работают дешевле и злее - боятся, что удача кончится к вечеру. "
            "Сегодня надстройки обходятся дешевле."
        ),
        "button_labels": None,
        "mechanics": "upgrade_cost-25%/1t",
        "tone": "good",
    },
    "drought": {
        "id": "drought",
        "name_ru": "Засуха",
        "digest_line": "Засуха - урожай слабее.",
        "canned_narrative": (
            "Земля потрескалась, как старая кожа, и ручьи стали пыльными бороздами. "
            "Нива отдаст едва ли половину - переждать и молиться на облака."
        ),
        "button_labels": None,
        "mechanics": "farm_mult-45%/1t",
        "tone": "bad",
    },
    "wedding": {
        "id": "wedding",
        "name_ru": "Свадьба в деревне",
        "digest_line": "Свадьба в деревне - завершённый обмен дарит зерно.",
        "canned_narrative": (
            "В деревне гуляют свадьбу: льётся брага, сыплются подарки и чужие долги. "
            "Кто сегодня завершит обмен, получит горсть зерна \"на счастье молодых\". "
            "Завтра счастье кончится, зерно - останется."
        ),
        "button_labels": None,
        "mechanics": "trade_complete:+5_grain/1t",
        "tone": "good",
    },
    "omen": {
        "id": "omen",
        "name_ru": "Знамение",
        "digest_line": "Знамение - долина ждёт беды, но пока тихо.",
        "canned_narrative": (
            "Над холмами прошла тень без тучи, и вороны сели молча, как судьи. "
            "Старики шепчут, какая беда бродит на краю долины, но цифр не называют. "
            "Механики нет - только холод в желудке."
        ),
        "button_labels": None,
        "mechanics": "foreshadow_only",
        "tone": "mixed",
    },
    "blight": {
        "id": "blight",
        "name_ru": "Порча товаров",
        "digest_line": "Порча товаров - часть запасов сгнила в пути.",
        "canned_narrative": (
            "В ларях завелась плесень, будто чужая зависть. "
            "Товары, что лежали без дела, превратились в труху и стыд. "
            "К вечеру считают убытки и ругаются тише обычного."
        ),
        "button_labels": None,
        "mechanics": "goods:-18%",
        "tone": "bad",
    },
    "press_gang": {
        "id": "press_gang",
        "name_ru": "Набор в дружину",
        "digest_line": "Набор в дружину - часть силы ушла с вербовщиками.",
        "canned_narrative": (
            "По дороге прошли люди с барабаном и цепями: \"за короля\" и \"за пайку\". "
            "Кто слабо держал копьё - ушёл с ними. Во дворах стало пустее, "
            "а в головах - злее."
        ),
        "button_labels": None,
        "mechanics": "might:-3",
        "tone": "bad",
    },
    "fire": {
        "id": "fire",
        "name_ru": "Пожар",
        "digest_line": "Пожар - одно здание повреждено и ждёт ремонта.",
        "canned_narrative": (
            "Искра нашлась сама: то ли искра из горна, то ли чужая месть. "
            "Огонь лизнул крышу и ушёл, оставив чёрные брёвна и работу на неделю. "
            "Чинить придётся своими руками и товарами."
        ),
        "button_labels": None,
        "mechanics": "damage_random_building",
        "tone": "bad",
    },
    "toll": {
        "id": "toll",
        "name_ru": "Дорожный побор",
        "digest_line": "Дорожный побор - с каждой усадьбы сняли товары.",
        "canned_narrative": (
            "У моста стоят чужие счётчики с улыбками мытарей. "
            "\"На дорогу\", говорят они, и мешки становятся легче. "
            "Спорить с алебардой - плохая арифметика."
        ),
        "button_labels": None,
        "mechanics": "goods:-12_flat",
        "tone": "bad",
    },
    "spoilage": {
        "id": "spoilage",
        "name_ru": "Гниль в закромах",
        "digest_line": "Гниль в закромах - часть зерна пропала.",
        "canned_narrative": (
            "Зерно согрелось само собой и пошло чёрными пятнами. "
            "Даже амбар не всегда спасает от сырости и чужого глаза. "
            "Утром мешки легче - и совесть тоже."
        ),
        "button_labels": None,
        "mechanics": "grain:-15%",
        "tone": "bad",
    },
}

CATASTROPHES: dict[str, dict[str, Any]] = {
    "bandit_night": {
        "id": "bandit_night",
        "name_ru": "Ночь бандитов",
        "canned_narrative": (
            "С холмов сползли огоньки факелов - чужие, слишком ровные для пастухов. "
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
            "Чужая беда - чужая лишь до тех пор, пока волна не придёт к тебе."
        ),
        "button_labels": None,
        "mechanics": "river+adj:building-1",
    },
    "cattle_plague": {
        "id": "cattle_plague",
        "name_ru": "Мор скота",
        "canned_narrative": (
            "На выгонах падают коровы, а воздух пахнет сладкой гнилью. "
            "Поля без тягла дают половину, пока мор сам не отступит."
        ),
        "button_labels": None,
        "mechanics": "farm_mult-50%/window",
    },
    "rat_king": {
        "id": "rat_king",
        "name_ru": "Крысиный король",
        "canned_narrative": (
            "Тот, кто чаще всех стучал в чужие ворота, носит теперь невидимую корону. "
            "Его добыча жирнее, а имя произносят вслух - чтобы охотники знали цель. "
            "Первый удачный набег на короля срывает награду долины."
        ),
        "button_labels": None,
        "mechanics": "top_raider:+30%_loot/4t;bounty:players*5_goods",
    },
    "dragon_rumors": {
        "id": "dragon_rumors",
        "name_ru": "Драконьи слухи",
        "canned_narrative": (
            "Пастухи клянутся, что в расселине дышит золотом нечто огромное. "
            "Нужны смельчаки и общая сила - иначе вернутся голодными и позорными. "
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
            "Каждому суют одно грязное предложение - за товары, без свидетелей. "
            "Честь здесь товар самый дешёвый."
        ),
        "button_labels": ["Открыть предложение"],
        "mechanics": "dm_shady_offer:1/1t",
    },
}

# Живой ролл: только события с реальной механикой/UI (omen - намеренный flavor).
# Вне пула, но в таблицах: trader - до проводки DM-сделок.
SHIPPED_MINOR_KEYS: frozenset[str] = frozenset(
    {
        "harvest",
        "fog",
        "rats",
        "fair",
        "good_stone",
        "drought",
        "wedding",
        "omen",
        "blight",
        "press_gang",
        "fire",
        "toll",
        "spoilage",
    }
)

# Веса ролла: больше плохих, меньше "подарочных".
MINOR_EVENT_WEIGHTS: dict[str, int] = {
    "harvest": 3,
    "fog": 5,
    "rats": 6,
    "fair": 3,
    "good_stone": 3,
    "drought": 6,
    "wedding": 2,
    "omen": 3,
    "blight": 6,
    "press_gang": 5,
    "fire": 5,
    "toll": 5,
    "spoilage": 6,
}

# Живой ролл катастроф: contribute/resolve UI.
SHIPPED_CATASTROPHE_KEYS: frozenset[str] = frozenset(
    {"bandit_night", "cattle_plague"}
)

_MINOR_EFFECTS: dict[str, dict[str, Any]] = {
    "harvest": {"farm_mult": 1.15, "duration_ticks": 1},
    "fog": {"raids_ignore_patrol": True, "duration_ticks": 1},
    "trader": {"dm_deals": 2, "duration_ticks": 1},
    "rats": {"unprot_grain_threshold": 80, "loss_frac": 0.20},
    "fair": {"trade_bonus_frac": 0.05, "duration_ticks": 1},
    "good_stone": {"upgrade_cost_mult": 0.75, "duration_ticks": 1},
    "drought": {
        "farm_mult": 0.55,
        "duration_ticks": 1,
    },
    "wedding": {"trade_gift_grain": 5, "duration_ticks": 1},
    "omen": {"foreshadow": True},
    "blight": {"goods_loss_frac": 0.18},
    "press_gang": {"might_loss": 3},
    "fire": {"damage_random_building": True},
    "toll": {"goods_flat_loss": 12},
    "spoilage": {"grain_loss_frac": 0.15},
}

_CATASTROPHE_EFFECTS: dict[str, dict[str, Any]] = {
    "bandit_night": {
        "might_per_player": B.BANDIT_NIGHT_MIGHT_PER_PLAYER,
        "loot_goods_per_player": B.BANDIT_NIGHT_LOOT_PER_PLAYER,
        "fail_unprot_grain_frac": B.BANDIT_NIGHT_FAIL_GRAIN_FRAC,
        "fail_lowest_defense_count": 2,
        "fail_worst_building_delta": -1,
    },
    "flood": {
        "targets": "river_and_orthogonal_neighbors",
        "building_delta": -1,
    },
    "cattle_plague": {
        "farm_mult": 0.50,
    },
    "rat_king": {
        "loot_bonus_frac": 0.30,
        "duration_ticks": 4,
        "select": "most_raid_attempts_this_week",
        "bounty_goods_per_player": 5,
    },
    "dragon_rumors": {
        "pledge_might": 10,
        "min_players": 3,
        "success_might_threshold": 40,
        "fail_hunger_ticks": 2,
    },
    "black_fair": {
        "dm_offers": 1,
        "duration_ticks": 1,
        "offer_kinds": ("smoke_bomb", "denunciation"),
    },
}


def event_digest_line(meta: dict[str, Any]) -> str:
    """Игровая строка сводки: никогда не отдаёт mechanics."""
    line = meta.get("digest_line")
    if line:
        return str(line)
    name = meta.get("name_ru")
    if name:
        return str(name)
    return str(meta.get("id") or "событие")


def _shipped_minor_pool() -> list[str]:
    return [k for k in SHIPPED_MINOR_KEYS if k in MINOR_EVENTS]


def _shipped_catastrophe_pool() -> list[str]:
    return [k for k in SHIPPED_CATASTROPHE_KEYS if k in CATASTROPHES]


def roll_minor_event(rng: Random) -> str | None:
    """С вероятностью MINOR_EVENT_CHANCE возвращает ключ отгруженного события, иначе тихий день."""
    if rng.random() >= B.MINOR_EVENT_CHANCE:
        return None
    pool = _shipped_minor_pool()
    if not pool:
        return None
    weights = [max(1, int(MINOR_EVENT_WEIGHTS.get(k, 1))) for k in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def pick_catastrophe(rng: Random, last_key: str | None) -> str:
    """Выбирает отгруженную катастрофу; повторяет last, если в пуле один ключ."""
    shipped = _shipped_catastrophe_pool()
    if not shipped:
        raise RuntimeError("Нет отгруженных катастроф для ролла")
    keys = [k for k in shipped if k != last_key]
    if not keys:
        keys = shipped
    return rng.choice(keys)


def next_catastrophe_delay_ticks(rng: Random) -> int:
    return rng.randint(B.CATASTROPHE_MIN_TICKS, B.CATASTROPHE_MAX_TICKS)


def minor_effect(key: str) -> dict[str, Any]:
    if key not in _MINOR_EFFECTS:
        raise KeyError(f"Неизвестное мелкое событие: {key}")
    return dict(_MINOR_EFFECTS[key])


def catastrophe_effect(key: str) -> dict[str, Any]:
    if key not in _CATASTROPHE_EFFECTS:
        raise KeyError(f"Неизвестная катастрофа: {key}")
    return dict(_CATASTROPHE_EFFECTS[key])


def event_name_ru(kind: str, key: str) -> str:
    if kind == "catastrophe":
        meta = CATASTROPHES.get(key) or {}
    else:
        meta = MINOR_EVENTS.get(key) or {}
    return str(meta.get("name_ru") or key)
