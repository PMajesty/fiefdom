# Вотчина (Fiefdom)

Persistent medieval fief game for Telegram friend groups.
One group chat = one valley (долина). Each player owns a fief (усадьба).
Russian UI. Design: `valley_game_design.md`.

## Stack

- aiogram 3 + PostgreSQL (pg8000)
- Four daily ticks 10:00, 13:00, 16:00 and 19:00 Europe/Moscow
- Poe LLM for event narrative (canned fallback)
- No whitelist - anyone can play; admin toolkit for `ADMIN_USER_ID`

## Local

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# fill tokens + DB
cd src
python -m app.main
```

Tests:

```powershell
pytest
```

## Group commands

| Command | Action |
|---------|--------|
| `/вотчина` | Create realm in this chat |
| `/вч_карта` `/vch_map` | Map |
| `/вч_рынок` `/vch_market` | Removed (market closed; caravans in DM) |
| `/вч_сводка` `/vch_digest` | Digest hint |
| `/вч_я` `/vch_me` | Deep-link to DM |
| `/вч_помощь` `/vch_help` | Short help |
| `/вч_гайд` `/вч_устав` `/vch_guide` | Game rules |

Personal play (build, claim, raid, patrol, caravan, pacts) is in DM. Map includes a tile legend.

## BotFather checklist

1. Disable **Group Privacy** (or bot won't see non-command context as needed; commands still work with privacy on if they are registered).
2. Set commands list optionally.
3. Start bot, add to group, run `/вотчина`.

## Deploy (same VPS as other bots)

```powershell
python deploy/setup_vps.py    # once
python deploy/quick_deploy.py # code + restart
```

Service: `fiefdom` at `/opt/fiefdom`.

## Continent, wipe

- All valleys share one **continent clock** (ticks and events together).
- Rumors drip into the valley group chat between ticks (0 or 1 true line per play window, quiet hours 21:00-08:00); not in the tick digest. DM "слухи" re-reads the recent archive.
- `/вотчина` in a new group creates a valley on the current continent day. All valleys on the continent are one play space (raid, caravan, pacts).
- Each valley keeps its own land map; players can open other valley maps from the DM menu.
- A player may own only **one** estate on the continent.
- Tick digests post to the valley group chat; other game notices go to personal DMs.
- Existing valleys are attached automatically on deploy/migrate (ordered by id).
- Wipe erases the **whole continent** (every valley on that world).

## Admin (DM)

All in private chat with the bot. Use `/вч_admin_help` for examples.

- `/вч_realms` - list valley ids (`#1` = realm_id)
- `/вч_tick` - run continent tick (all valleys at once)
- `/вч_grant realm_id fief_id grain goods might` - add resources
- `/вч_event realm_id key` - force continent minor event until next tick
- Wipe continent (two steps): `/вч_wipe_start 1` (any valley id as anchor) then paste the command the bot returns
- `/вч_freeze fief_id 0|1` - freeze/unfreeze estate
- `/вч_decree realm_id text` - send decree to valley players' DMs
