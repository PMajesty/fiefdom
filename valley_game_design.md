# «Долина» — Game Design Document (MVP)

Persistent, no-session medieval-fantasy fief game for a Telegram friend group.
One group chat = one realm (долина). Each player owns a personal fief (усадьба).
No death, no winner, no reset. 30 seconds to 2 minutes per day. Russian UI.

This document is the source of truth for **mechanics**, not code. All numbers live in
the Balance Appendix at the bottom and are meant to be tuned live (see Live-Ops section).

---

## 1. Design pillars

1. **Tiny loop, deep consequences.** The daily loop is trivial; depth comes from
   relationships (feuds, pacts, dependencies), not from menus.
2. **Mean but recoverable.** Raids and catastrophes sting ("крысы съели зерно,
   вы голодаете"), but nothing is ever permanently lost that can't be rebuilt.
3. **Absence is safe.** A player can vanish for a month and come back to a weedy
   but playable fief.
4. **The chat is the stage.** The bot posts facts (1 digest/day + rare events);
   humans create the drama. Personal management happens in DM.
5. **Patchable forever.** All content is data, all numbers are config, patches are
   delivered in-fiction as «Указы» (royal decrees).

---

## 2. Glossary (in-game Russian terms)

| Term | Meaning |
|---|---|
| Долина | The realm; one Telegram group |
| Усадьба | A player's fief (tiles + buildings + stash) |
| Клетка | Map tile |
| Зерно | Food resource |
| Товары | Goods resource (building material / trade good) |
| Сила | Might resource (military, non-stealable) |
| Голод | Hunger status (debuff, not death) |
| Дозор | Patrol (defensive action) |
| Пакт | Alliance (2–5 players) |
| Сводка | Morning digest |
| Указ | Patch notes, posted in-fiction |

---

## 3. Map

### 3.1 Size and shape

- Rectangular grid, coordinates `А1…` (Cyrillic columns, numeric rows).
- Total tiles ≈ `players × 3.5`, rounded to a clean rectangle.
  Minimum 12 (up to 3 players), maximum 64.
  - 5 players → 4×5 = 20 tiles
  - 10 players → 5×7 = 35 tiles
  - 16 players → 7×8 = 56 tiles
- **Live growth:** when claimed tiles exceed 70% of the map, append one row or
  column of freshly generated tiles ("Разведчики открыли новые земли" decree).
  This is the mechanism for new members joining mid-game.

### 3.2 Generation algorithm

1. Lay a **road** (Дорога): a horizontal line with one bend, spanning the map.
2. Lay a **river** (Река): a vertical line crossing the road at one tile — the
   **bridge tile** (мост), a special road tile.
3. Fill remaining tiles by weighted random with clustering (a tile has +15%
   chance to copy a random already-placed neighbor):
   - Поле 30%, Лес 20%, Холмы 15%, Руины 8%, Глушь — remainder (~20%+).
4. Place exactly one **Святилище** (shrine) on a tile far from the road.
   It is visible from day 1 but mechanically inert in MVP (reserved hook, see Roadmap).
5. Validate: every player spawn candidate (non-Глушь, non-road) must exist in
   sufficient quantity; regenerate if not.

### 3.3 Tile types

| Tile | Emoji | Effect when owned | Notes |
|---|---|---|---|
| Поле (field) | 🌾 | Ферма here yields ×1.5 | Most common |
| Лес (forest) | 🌲 | Мастерская here yields ×1.5 | |
| Холмы (hills) | ⛰️ | Сторожка here: defense & Сила ×1.5 | |
| Река (river) | 🌊 | +3 Зерно/day passive (fishing) | Vulnerable to Наводнение |
| Дорога (road) | 🛤️ | +3 Товары/day passive (toll) | Attracts events; bridge tile counts as road |
| Руины (ruins) | 🕳️ | One-time loot on claim: 30–80 Товары | Then acts as a plain ×1.0 tile |
| Глушь (wilds) | 🌫️ | Cannot build until cleared | Claim costs ×2; on clearing becomes random Поле/Лес/Холмы |
| Святилище (shrine) | ⛩️ | None in MVP | Unclaimable in MVP; future control point |

### 3.4 Claiming tiles

- Every fief starts with **1 tile** (see Onboarding) and can never drop below **2 claimed-or-core tiles** (the starter tile + first claim are "core").
- Claim cost escalates per fief (Товары): 2nd — 30, 3rd — 60, 4th — 120, 5th — 250, 6th — 400, 7th — 600, 8th — 850, 9th — 1150. **Hard cap: 9 tiles.**
- Must be adjacent to a tile you already own (orthogonal). Глушь costs double and consumes the claim action without building rights until cleared (clearing is automatic on claim, it just costs more).
- Claiming costs 1 action (see Actions).
- **Upkeep scales with size** (see Economy), so big fiefs are hungry, juicy targets — this is the anti-snowball.

### 3.5 Rendering

- `/map` (and the Sunday digest) posts an emoji grid with a legend:
  owner marked by a letter/digit index, e.g. `🌾К = Кирилл`.
- Personal DM view highlights *your* tiles and adjacent claimable ones.

---

## 4. Resources & economy

### 4.1 The three resources

| Resource | Sources | Sinks | Stealable |
|---|---|---|---|
| Зерно | Ферма, Река, events, trade | Upkeep, event mitigation, trade | Yes |
| Товары | Мастерская, Дорога, Руины, trade | Buildings, tile claims, mitigation, trade | Yes |
| Сила | Сторожка, events | Raids, Дозор, catastrophe contributions | **No** (men and morale can't be carted off) |

No money in MVP — all trade is barter. (A currency, «Золото», is a reserved
future patch; see Live-Ops §14.4 for how to introduce it safely.)

### 4.2 The daily tick

- One global tick per realm at **09:00** realm time (configurable), simultaneous with the digest.
- The tick: applies production to each fief's pending balance, charges upkeep,
  updates Голод status, refreshes actions, expires shields/patrols, rolls the daily event.
- Players **collect** pending production whenever they open the bot (free, no action cost).
  Uncollected production accumulates up to **3 days' worth**, then stops (rats eat the rest —
  flavored in DM). Амбар levels raise this cap (see Buildings).

### 4.3 Upkeep and Голод (hunger)

- Daily upkeep in Зерно: `4 + 2 × (tiles − 1)`.
  - 2 tiles = 6/day, 5 tiles = 12/day, 9 tiles = 20/day.
- If the fief can't pay (stash + pending = 0): status **Голод**:
  - All production −50%.
  - Cannot raid («голодные мужики не воюют»).
  - Cleared after one full tick with upkeep paid.
- Голод is loud in DM and may appear in the digest («Амбары Димы пусты»). It is
  the game's "mean but recoverable" centerpiece: rats ate your grain, you will
  starve — go buy, beg, or trade your way out.

### 4.4 Inflation control

- Steal caps recycle rather than create resources.
- Claim-cost curve and building costs are the main Товары sink; upkeep is the Зерно sink.
- Watch metric: total Зерно per player per day (see Live-Ops §14.6). If it drifts
  up over weeks, patch upkeep or event drains via decree.

---

## 5. Fief & buildings

### 5.1 Structure

- A building occupies one owned tile (one building per tile).
- Levels 1–3 in MVP. Upgrading requires the action + Товары.
- Buildings on their "native" tile get the ×1.5 bonus (see Tiles).
- Buildings are never destroyed below level 1; catastrophes/raids can drop them **−1 level** (repair = normal upgrade cost of that level, −50%).

### 5.2 Building list

| Building | Native tile | Lv1 / Lv2 / Lv3 cost (Товары) | Effect per level |
|---|---|---|---|
| Ферма | Поле | 20 / 50 / 120 | +8 / +14 / +22 Зерно per day |
| Мастерская | Лес | 25 / 60 / 140 | +5 / +9 / +14 Товары per day |
| Сторожка | Холмы | 20 / 50 / 110 | +6 / +12 / +20 defense; +2 / +4 / +6 Сила per day |
| Амбар | any | 30 / 70 / 150 | Storage cap 200 / 400 / 800 per resource; protects 25% / 40% / 60% of stash from raids; +1 day to uncollected-production cap |

Starting kit (onboarding): 1 tile, free Ферма lv1, 30 Зерно, 20 Товары, 5 Сила.

Base fief without buildings produces nothing except tile passives — buildings are the game.

---

## 6. Actions & the daily session

### 6.1 Action points

- **1 действие per day**, granted at the tick. Unused actions bank up to **3**
  (a returning player gets a satisfying burst, not a punishment).
- Free (no action cost): collecting, reading, posting/accepting **trades**, event
  buttons, alliance management.
- Costs 1 action: build, upgrade, repair, claim tile, raid, patrol.

### 6.2 The loop (30s–2min)

1. Open DM (or tap «Моё владение» in group) → auto-collect pending production.
2. See status card: stash, Голод/shield/patrol flags, today's event, 1–3 suggested actions.
3. Spend the action (or bank it). Optionally check the market or map.
4. Done.

### 6.3 Onboarding (first 3 days)

Guided micro-quests in DM, each rewarding small resources:

1. Day 1: choose a starter tile from 3 offered (non-Глушь, spaced ≥1 tile from
   existing players when possible) → build is pre-done (free Ферма) → collect.
2. Day 2: upgrade or build something (+15 Товары reward).
3. Day 3: post or accept one trade offer (+10 Зерно reward).

After that, no tutorial — the digest and events teach the rest.

---

## 7. Raids & defense

### 7.1 Raid resolution (deterministic, async-fair)

- Attacker commits Сила `S` (minimum 5) + 1 action.
- Defense `D` = Сторожка defense + 10 if Дозор active + pact intercept (see Alliances).
- Ratio `R = S / (S + D)`.
- **Outcome:**
  - `R < 0.33` — **отбит**: attacker loses all committed Сила, steals nothing.
    Public line: «Набег Саши на хутор Кирилла отбит у ворот».
  - `R ≥ 0.33` — success: attacker loses half the committed Сила and steals loot.
- **Loot** = `R × 20%` of the victim's *unprotected* stash (Зерно + Товары,
  proportionally), where Амбар protects its percentage first. Two caps apply:
  - Max **25%** of the victim's unprotected stash.
  - Max **2 days of the victim's production** (newbie protection — robbing a
    starter fief yields crumbs).

### 7.2 Cooldowns and shields

| Rule | Value |
|---|---|
| Victim shield after being raided | 36h (unraidable) |
| Same attacker → same victim | 72h cooldown |
| Attacker global raid cooldown | 20h (plus the 1-action gate) |
| Голод | Cannot raid at all |

### 7.3 Дозор (patrol)

- 1 action + 5 Сила → for 24h: +10 defense to your own fief and, if you are in a
  pact, you count as "on watch" for intercepts.

### 7.4 Pact intercept

- Pact members may toggle «Прикрывать союзников». If a covered ally is raided
  while you have ≥5 Сила, 5 of your Сила is auto-spent and added to their `D`.
  You get a DM report either way.

### 7.5 Publicity & feuds

- **Every raid produces one public group line** (success or failure). Drama is the point.
- Raiding the same fief 3+ times in 7 days flags a **Вражда** in the digest
  («Вражда: Саша против Кирилла, неделя вторая»). Purely narrative in MVP.

### 7.6 Reputation counters

Tracked per player, surfaced as digest titles (Sunday):

- Raids attempted / succeeded / repelled (as defender)
- Grain sold / goods sold (trade volume)
- Catastrophe contributions
- Titles are generated from leaders: «Гроза дорог», «Хлебный барон», «Щит долины», «Тихий юг».

---

## 8. Alliances (Пакты)

- 2–5 players. Created by invite/accept in DM. Named by the founder (visible on map legend and digest).
- One pact per player.
- Features (all MVP):
  - Shared tag next to names in digest and on the map.
  - **Intercept** toggle (see §7.4).
  - Leave anytime; kicked by founder. Dissolves below 2 members.
- **Not** in MVP: common chest, taxes, officers, pact-level war declarations.
  The chat is the diplomacy engine; the bot only enforces the intercept.

---

## 9. Trade

- Any player posts an offer in DM: give `X` of resource A, want `Y` of resource B
  (Зерно ↔ Товары only; Сила is untradeable).
- Offers live on the realm market board (`/market` or DM tab), expire after 48h.
- Accepting is one tap; the bot escrows and swaps instantly. No action cost for either side.
- Targeted offers allowed (visible only to one named player) — this is how "people
  sometimes trade with the loner" works without him reading a board.
- The digest mentions notable market activity («Ира продала 120 зерна за неделю»).
- No price control: let the friends invent the exchange rate. Watch it as a metric.

---

## 10. Events

Two layers, both realm-wide, both **mechanically scripted** — the LLM only writes
the narrative text and button labels (constrained output; canned fallback text
exists for every event so an LLM outage never blocks the game).

### 10.1 Daily minor events (rolled at tick, 60% chance; otherwise a quiet day)

| # | Event | Mechanics |
|---|---|---|
| 1 | Урожайный день | All farms +25% today |
| 2 | Туман | Raids today ignore Дозор (mean; favors rats) |
| 3 | Бродячий торговец | Every player gets 2 random personal DM deals, 24h (e.g. 30 Зерно → 20 Товаров) |
| 4 | Крысы в амбарах | Everyone holding >150 unprotected Зерно loses 10% («богатых крысы любят») |
| 5 | Ярмарка | Trades completed today: both parties +5% bonus |
| 6 | Дезертир | Group message, one button, first-come: +10 Сила |
| 7 | Хороший камень | Upgrades today −25% cost |
| 8 | Засуха | Farms −30% for 24h; personal mitigation: pay 10 Товаров (полив) |
| 9 | Свадьба в деревне | Everyone who completes a trade today +8 Зерно gift |
| 10 | Знамение | No mechanics; LLM foreshadows the *category* of the next catastrophe |

Minor events appear in the digest; only #6 gets its own (single) group message.

### 10.2 Catastrophes (every 5–8 days, jittered; posted 19:00–21:00 prime time; 12–24h response window)

| # | Catastrophe | Mechanics |
|---|---|---|
| 1 | **Ночь бандитов** | Shared group message with a live counter. Realm must contribute `players × 2.5` Сила. Success: contributors split a Товары loot pool (`players × 8`) + digest glory. Failure: every non-contributor and the 2 lowest-defense fiefs lose 15% unprotected Зерно; the worst-hit fief's random building −1 lvl. |
| 2 | **Наводнение** | All Река tiles and their orthogonal neighbors: one building −1 lvl unless the owner pays 15 Товаров (мешки с песком) within the window. Anyone may pay for someone else (a donate button) — solidarity mechanic. |
| 3 | **Мор скота** | All farms −50% for 48h; personal mitigation: 20 Зерно (забить больной скот) ends it early for that fief. |
| 4 | **Крысиный король** | For 48h, the player with the most raid *attempts* this week gets +30% loot. Everyone is warned by name. Bounty: the first player to successfully raid the Rat King during the event takes a bonus pool (`players × 5` Товаров). Hunt-the-rat drama, by design. |
| 5 | **Драконьи слухи** | Opt-in expedition: pledge 10 Сила each, needs ≥3 players. If total pledged ≥ 40 Сила: treasure split (Товары pool + one Реликвия when relics ship in v0.2). Otherwise all participants return with Голод for 24h («вернулись ни с чем»). |
| 6 | **Чёрная ярмарка** | 24h: every player gets one shady personal DM offer — e.g. дымовая шашка (next raid: defender's Дозор ignored, one use), or донос (reveal one named player's exact stash). Costs Товары. |

Cadence guard: never two catastrophes within 4 days; never the same one twice in a row.

### 10.3 LLM pipeline

1. The engine picks event type + parameters (targets, amounts) from the tables above.
2. The LLM receives a structured brief and returns JSON: `narrative` (2–4 sentences,
   Russian, mean-medieval tone), `button_labels`.
3. Validation: length limits, no numbers other than the ones provided, profanity filter.
4. On any failure → canned text. The LLM **never** decides outcomes, amounts, or targets.

---

## 11. Absence & neglect (no death, ever)

| Inactivity | Effect |
|---|---|
| 0–6 days | Nothing special; production accrues to the collect cap, then stops |
| 7+ days | Fief «дремлет»: shown as weedy on the map; excluded from being a catastrophe's "worst-hit" target (don't kick the sleeping) |
| 21+ days | Tiles beyond the 2 core ones become «заросшие»: claimable by others at normal cost; the absentee is auto-compensated 50% of the claim price when one is taken |
| Return, any time | Open the bot → collect → banked actions (up to 3) → full player again |

Nothing else is ever taken. Buildings on core tiles persist forever.

---

## 12. Chat presence & noise budget

| Message | Frequency |
|---|---|
| Morning digest (Сводка) | 1/day, at tick |
| Catastrophe post | ~1 per 5–8 days |
| Raid result lines | Appended into the digest, **except** raids during an active catastrophe window (posted live for drama) |
| Дезертир-type instant events | ≤1/day, rare |
| Указ (patch notes) | When you patch |

Everything else — personal cards, markets, trades, alliance management — lives in DM.
Group «Моё владение» button answers with a silent deep-link to DM (and a private
toast «Напиши боту /start» if DM is closed).

### Digest template

```
🏰 Долина друзей — день 43
🌙 Ночью: Саша ограбил Кирилла (−34 товара). Набег Оли на Иру отбит.
📜 Сегодня: Засуха — фермы −30%. Полив: 10 товаров (в личке).
🛒 Рынок: 3 лота. Лучший: 40 зерна за 25 товаров (Ваня).
⚔️ Вражда: Саша против Кирилла — неделя вторая.
```

Sunday digest adds: titles, tile-count leaders, pact standings, the map render.

---

## 13. Identity & multi-group model

- Data model from day 1: `User (telegram_id) → Fief (user_id, realm_id)`;
  `Realm (chat_id)`. One fief per user per realm.
- DM context: deep-links from a group carry the realm; a cold `/start` in DM shows
  a realm picker (remembering the last active realm).
- Fief naming: players may name their усадьба («Хутор Ворона»); defaults to
  «Усадьба {first_name}». Names appear in digests — free flavor, zero mechanics.

---

## 14. Live-ops: patching a running world

You will be changing this game weekly while people live in it. Rules:

### 14.1 Everything is data

- **Balance config**: every number in this document (costs, yields, caps, cooldowns,
  percentages, cadences) lives in one config, overridable per realm. Tuning ≠ deploy.
- **Content tables**: tiles, buildings, events, (later) relics are rows, not code paths.
  Adding minor event #11 is inserting a row.

### 14.2 Additive patching rules

1. Never delete a resource or building type mid-game. Deprecate: remove its
   *sources*, keep its *sinks*, convert leftovers via decree if needed.
2. Never retroactively invalidate what players own. Grandfather old tiles/buildings;
   change only future acquisitions.
3. Nerfs to income are safer than confiscations. If you must confiscate, wrap it
   in a catastrophe («Королевская подать») so it's an event, not a betrayal.
4. New systems ship behind per-realm **feature flags** (relics: off, shrine: off),
   so the friend group can beta-test on demand.

### 14.3 Указы (in-fiction patch notes)

Every patch posts a short decree to the group:

```
📜 УКАЗ №7
Отныне амбары укрывают больше зерна (40% → 45% на II уровне).
Крысы недовольны.
```

This turns maintenance into content and trains players to expect change.

### 14.4 Worked example: adding a currency later

Introducing «Золото» mid-game: decree announces royal mint; every fief receives
a starter grant scaled to trade-volume reputation; market gains gold pairs; barter
stays legal. No migration pain because trade was already escrow-based.

### 14.5 Admin toolkit (mechanics-level requirements)

- Spawn/force any event; cancel an active event.
- Grant/remove resources; rollback the last raid (dispute resolution among friends).
- Freeze/unfreeze a player; rename tiles; extend the map manually.
- Regenerate today's digest.

### 14.6 Health metrics to watch weekly

| Metric | Healthy | Symptom if off |
|---|---|---|
| Daily actors / players | > 50% | Loop too thin or too spammy |
| Raids per day | 0.5–2 | Zero: fights don't pay. >3: shields/caps too weak |
| Trades per week | ≥ players/2 | Barter rates broken or market invisible |
| Зерно per player per day (net) | ~flat | Inflation → raise sinks via decree |
| Catastrophe participation | > 60% | Rewards too small or windows too short |

---

## 15. MVP cut-line

**In MVP (build all of this, nothing less):**

1. Realm creation, map gen, live map growth
2. Fiefs, 3 resources, 4 buildings × 3 levels, tile claiming with cost curve
3. Daily tick, collect, action points with banking
4. Upkeep + Голод
5. Raids with caps/shields/cooldowns, Дозор, public raid lines
6. Pacts with intercept
7. Barter market with escrow + targeted offers
8. 10 minor events + 6 catastrophes, LLM narrative layer with canned fallback
9. Digest + noise budget, Sunday extended digest with titles
10. Absence rules (дремлет / заросшие)
11. Onboarding quests, fief naming
12. Balance config + content tables + feature flags + admin toolkit + decrees

**Explicitly NOT in MVP:**

- Relics/items (v0.2), Shrine control (v0.3), gold currency
- Cross-realm anything: messengers, war/trade declarations (v0.4), portals,
  caravans and caravan robbery, wilderness zones between realms (v0.5+)
- Mini App UI (DM menus first; Mini App is a later cosmetic upgrade)
- Monetization (never for the friend-group version)

### Roadmap sketch

| Version | Theme | Contents |
|---|---|---|
| v0.2 | Things worth stealing | 3 relics (passive bonuses, raid-stealable), Чёрная ярмарка expands |
| v0.3 | The shrine wakes | Святилище becomes a contested control point with a realm-wide buff |
| v0.4 | Messengers | Cross-realm declarations of trade/war (text + small mechanical stakes) |
| v0.5 | The portal | One fief hosts a portal to another realm; caravans, robbery, RvR seeds |

---

## 16. Balance Appendix (initial values — expect to patch)

| Parameter | Value |
|---|---|
| Tiles per player | 3.5 |
| Tile hard cap per fief | 9 |
| Claim costs (2nd→9th) | 30 / 60 / 120 / 250 / 400 / 600 / 850 / 1150 Товары |
| Глушь claim multiplier | ×2 |
| Starting kit | 1 tile, Ферма lv1, 30 Зерно, 20 Товары, 5 Сила |
| Upkeep | 4 + 2×(tiles−1) Зерно/day |
| Голод penalty | −50% production, no raiding |
| Actions | 1/day, bank max 3 |
| Collect cap | 3 days of production (+1/Амбар level) |
| Ферма yield | 8 / 14 / 22 (+50% on Поле) |
| Мастерская yield | 5 / 9 / 14 (+50% on Лес) |
| Сторожка | def 6 / 12 / 20, Сила 2 / 4 / 6 (+50% on Холмы) |
| Амбар | cap 200/400/800, protect 25/40/60% |
| Река passive | +3 Зерно/day |
| Дорога passive | +3 Товары/day |
| Руины loot | 30–80 Товары once |
| Raid min Сила | 5 |
| Raid success threshold | R ≥ 0.33 |
| Loot formula | R × 20% unprotected stash |
| Loot caps | ≤25% unprotected stash AND ≤2 days victim production |
| Victim shield | 36h |
| Same-victim cooldown | 72h |
| Attacker cooldown | 20h |
| Дозор | 1 action + 5 Сила → +10 def, 24h |
| Intercept | 5 Сила auto-spent, +5 def to ally |
| Pact size | 2–5 |
| Minor event chance | 60%/day |
| Catastrophe cadence | every 5–8 days, jittered |
| Ночь бандитов threshold | players × 2.5 Сила; pool players × 8 Товаров |
| Tick / digest time | 09:00 realm time |
| Catastrophe post window | 19:00–21:00 |
| Дремлет threshold | 7 days |
| Заросшие threshold | 21 days (keeps 2 core tiles) |
