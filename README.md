# Вотчина (Fiefdom)

Persistent medieval fief game for Telegram friend groups.
One group chat = one valley (долина). Each player owns a fief (усадьба).
Russian UI. Design: `valley_game_design.md`.

## Stack

- aiogram 3 + PostgreSQL (pg8000)
- Daily tick 13:00 Europe/Moscow
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
| `/вч_рынок` `/vch_market` | Market |
| `/вч_сводка` `/vch_digest` | Digest hint |
| `/вч_я` `/vch_me` | Deep-link to DM |
| `/вч_помощь` `/vch_help` | Short help |
| `/вч_гайд` `/вч_устав` `/vch_guide` | Game rules |

Personal play (build, claim, raid, patrol, trade, pacts) is in DM. Map includes a tile legend.

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

## Admin (DM)

All in private chat with the bot. Use `/вч_admin_help` for examples.

- `/вч_realms` - list valley ids (`#1` = realm_id)
- `/вч_tick [realm_id]` - run daily tick (all realms if omitted)
- `/вч_grant realm_id fief_id grain goods might` - add resources
- `/вч_event realm_id key` - force minor event 24h
- Wipe (two steps): `/вч_wipe_start 1` then paste the command the bot returns
- `/вч_freeze fief_id 0|1` - freeze/unfreeze estate
- `/вч_decree realm_id text` - post decree to the group
