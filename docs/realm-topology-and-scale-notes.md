# Realm topology and scale notes

Notes from design discussions (2026-07). Infrastructure shipped first; heavier anti-blob and road rules deferred until there are more valleys and players.

## What we shipped (prep)

- Undirected `realm_links` between valleys, max degree **3** on create.
- New valley attaches to a random existing valley with free degree (`degree < 3`).
- **Play stays continent-wide** for now (`list_adjacent_realms` / `realms_are_adjacent` still mean same `world_id`).
- `chain_index` remains soft listing order, not the neighbor graph.
- When we want portal-limited play later: flip adjacency to read `realm_links`.

Map size / tile caps are a separate track. Valleys can grow with players; do not treat a hard tile ceiling as the long-term fairness lever.

---

## Problems we expect later

### 1. Mega-chat / pact domination

Large Telegram groups can fill pacts, feed a few apex fiefs, and deny smaller players.

**Rejected (easy to wing around):**

- Pile-on tax on ally count (feed 3 fat fiefs instead)
- Reinforce / help caps (same)
- Same-chat seat caps (spawn many pacts / Travian-style wings)

**Directions that survive off-book coordination** (key victim, border, or road - not friend count):

- Punch-down ash: loot collapses when farming much weaker targets; peer / feeder hunting stays valuable
- Edge pipe / muster compression: cross-valley force shares a border budget
- Multi-front / contested attention: opening many wars softens each front
- Gate fatigue / recovery vault: stop forever-camping without deleting feeder-hunt loops
- **Do not** use plain "same fief hit again → less loot" as the primary anti-blob tool if feeder-hunting (repeated hits on satellites) is the intended counter to apex empires

### 2. Abandoned neighbors / isolation

Sticky links + dead chats can leave a living valley with zero living doors.

**Directions:**

- Orphan rescue: if living neighbors drop to 0, rewire to another living valley (cooldown)
- Ghost / NPC border as last resort (low-stakes content, not a fake mega-rival)
- Do not rematch the whole continent every week (kills trust)

### 3. Tiny chat forever next to growing chat

Sizes drift; tile caps will not fix player-count projection.

**Directions:**

- Soft size-band when attaching a *new* valley (prefer similar active size)
- Cap force across doors / roads rather than constantly rewiring when sizes change
- Unequal borders can exist; unlimited force dump should not

### 4. Shared valley map vs personal r=2 maps

Consensus from fun / fairness / complexity pass: **keep shared valley maps**. Personal radius-2 as the main world kills contested land and group map culture. Fog on a shared map, or a private PvE pocket that does not replace claims, is optional later.

### 5. Caravan / tribute roads (out of scope for now)

Obvious future lever against feeder empires: make trade / caravans robable on the road (all trade, not only cross-valley). Today robbery is only "raid the sender and skim outbound escrow."

Parked until player/realm count justifies it. Open decisions if revisited:

- Separate "rob caravan" vs extend night raid targeting
- Visibility of private vs public caravans
- Same-valley gifts still robable?
- Escort / double-dip with yard raids
- Partial take and bounce behavior

---

## Suggested future packages (not committed)

**Anti-deny-play (when blobs appear):** punch-down ash + edge pipe + recovery seed stash.

**Topology health:** orphan rescue (+ optional ghost border).

**Roads:** caravan intercept once there is enough traffic to matter.

**Flip play to links:** only when we explicitly want portal-limited neighbors instead of open continent.
