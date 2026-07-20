"""Composition root: сборка Database, Engine и графа сервисов.

build_app - единственная явная точка compose для продакшена (get_engine).
compose_services вешает сервисы на Engine; прямой Engine(db) в тестах
вызывает тот же compose_services, чтобы фасады и once-compose совпадали.
Хендлеры/планировщик берут только get_engine().
Прямой доступ к БД и приватным членам Engine отсюда не раздаётся.
"""
from __future__ import annotations

from app.database import Database, get_db
from app.engine import Engine

_engine: Engine | None = None


def compose_services(engine: Engine, db: Database) -> None:
    """Собирает сервисы один раз и вешает на engine (keep-list фасады)."""
    from app.services.caravans import CaravanService
    from app.services.catastrophes import CatastropheService
    from app.services.cover_stances import CoverStanceService
    from app.services.land_actions import LandActionService
    from app.services.night_raids import NightRaidResolver
    from app.services.onboarding import OnboardingService
    from app.services.pacts import PactService
    from app.services.patch_announce import PatchAnnounceService
    from app.services.player_context import PlayerContextService
    from app.services.raid_declare import RaidDeclareService
    from app.services.realm_admin import RealmLifecycleService
    from app.services.realm_tick import RealmTickRunner
    from app.services.rumors import RumorService
    from app.services.world_tick import WorldTickOrchestrator

    engine._realm_lifecycle = RealmLifecycleService(engine, db)
    engine._patch_announce = PatchAnnounceService(engine, db)
    engine._player_context = PlayerContextService(engine, db)
    engine._onboarding = OnboardingService(engine, db)
    engine._caravans = CaravanService(engine, db)
    engine._land_actions = LandActionService(engine, db)
    engine._catastrophes = CatastropheService(engine, db)
    engine._raid_declare = RaidDeclareService(engine, db)
    engine._night_raids = NightRaidResolver(engine, db)
    engine._cover_stances = CoverStanceService(engine, db)
    engine._pacts = PactService(engine, db)
    engine._world_tick = WorldTickOrchestrator(engine, db)
    engine._realm_tick = RealmTickRunner(engine, db)
    engine._rumors = RumorService(engine, db)


def build_app(*, db: Database | None = None) -> Engine:
    """Собирает приложение: Database + Engine + сервисы (composition root)."""
    database = db if db is not None else get_db()
    engine = Engine(database, compose=False)
    compose_services(engine, database)
    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_app()
    return _engine
