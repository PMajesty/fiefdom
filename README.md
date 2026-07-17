# Вотчина (Fiefdom)

Persistent medieval Telegram game for friend groups. Russian UI.

One group chat hosts one **долина** (valley). All valleys share one **континент** with a common tick clock and cross-valley play (raids, market, sends, pacts). Each player owns one **усадьба** on that continent (land, buildings, resources: зерно, товары, сила).

Play is hybrid: the group founds the valley and gets tick digests plus public drama (raids, pacts, catastrophes, decrees). Day-to-day actions run in DM with the bot.

## Stack

- aiogram 3 + PostgreSQL (pg8000)
- Four daily continent ticks (default 10:00, 13:00, 16:00, 19:00 `Europe/Moscow`; set via `TIMEZONE` / `TICK_HOUR*`)
- Event text is canned (`canned_narrative`). A Poe client exists in `app.narrative` but is not wired into the live path
- No player whitelist; admin DM toolkit gated by `ADMIN_USER_ID`

## Local

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# fill TELEGRAM_BOT_TOKEN, DB_*, ADMIN_USER_ID
cd src
python -m app.main
```

Tests (from repo root):

```powershell
pytest
```

## Group commands

| Command | Action |
|---------|--------|
| `/вотчина` | Create valley in this chat (joins the shared continent) |
| `/вч_карта` `/vch_map` | Valley map (tile legend under the image) |
| `/вч_рынок` `/vch_market` | Market board (view only; trade in DM) |
| `/вч_сводка` `/vch_digest` | Last tick digest, or schedule hint if none yet |
| `/вч_я` `/vch_me` | Deep-link button to open your усадьба in DM |
| `/вч_помощь` `/vch_help` | Short help |
| `/вч_гайд` `/вч_устав` `/vch_guide` `/vch_rules` | Full game rules |

Personal play is in DM (`/start` or `/вч_я`): claim, build, gather, demolish, patrol, raid, market trade, send/gift, pacts, rumors, holdings. Also `/меню` / `menu` for the estate hub.

## BotFather checklist

1. Group Privacy may stay on: group surface is slash commands (and callbacks). Disable only if you later need the bot to read ordinary group chat text.
2. Optionally set the BotFather command list to the group table above.
3. Start the bot, add it to a group, run `/вотчина`.

## Deploy (same VPS as other bots)

```powershell
python deploy/setup_vps.py    # once
python deploy/quick_deploy.py # code + restart
```

Service: `fiefdom` at `/opt/fiefdom`. Deploy secrets go in `deploy/secrets.env` (see `deploy/secrets.env.example`).

## Continent, wipe

- All valleys share one continent clock (ticks and events together). Digests include local rumors and a foreign-valley rumor block.
- `/вотчина` in a new group creates a valley on the current continent day. All valleys on the continent are one play space (raid, send, market, pacts).
- Each valley keeps its own land map; players can open other valley maps from the DM "Карта" menu.
- A player may own only one усадьба on the continent.
- Tick digests and public notices (raids, pacts, joins, catastrophes, decrees) post to the valley group; personal details and estate control stay in DM.
- On migrate/connect, valleys without a world are attached to the continent and given `chain_index` ordered by `id`.
- Wipe erases the whole continent (every valley on that world), not a single valley.

## Admin (DM)

Private chat with the bot only. Use `/вч_admin_help` for examples.

- `/вч_realms` - list valley ids (`#1` = realm_id)
- `/вч_tick` - run continent tick (all valleys at once; realm id args ignored)
- `/вч_grant realm_id fief_id grain goods might` - add resources
- `/вч_event realm_id key` - force continent minor event until next tick
- Wipe continent (two steps): `/вч_wipe_start 1` (any valley id as anchor), then paste the `/вч_wipe … УДАЛИТЬ` command the bot returns
- `/вч_freeze fief_id 0|1` - freeze/unfreeze estate
- `/вч_decree realm_id text` - post decree to the valley group chat
