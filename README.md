# Savant HA

A [HACS](https://hacs.xyz)-compatible Home Assistant integration that controls a
**Savant host** on the local network.

> **Provenance.** This integration targets a protocol that was **reconstructed by
> black-box reverse engineering** — passive packet capture, mDNS enumeration, and a live
> MITM — with **no** vendor documentation, firmware, or source code. The reconstruction
> lives in the sibling project
> [`savant-app-re`](https://git.housedillon.com/wdillon/savant-app-re) (its
> `PROTOCOL.md` / `REVERSE_ENGINEERING.md`). This repo keeps a condensed copy in
> [`PROTOCOL.md`](PROTOCOL.md). Nothing here is drawn from a vendor SDK or spec.

## What it talks

The host exposes a WSS control channel (`wss://<host>:<dynamic-port>/`, WebSocket
subprotocol `rpm-protocol`, MessagePack payloads, gzip in the host→client direction,
UDP 9101/9103 discovery). The integration:

1. discovers the control port / `homeId` via a UDP broadcast (or uses one you provide),
2. connects over WSS (validating nothing, matching the app's own behavior),
3. logs in with a host-local account (or a cached `hostToken`) and subscribes to state,
4. uses the configuration archive as the device/room inventory, then
   receives `state/update` pushes and issues `service/request` commands.

## Installation

### HACS (recommended)

1. HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/rohrsh/savant-ha` (category **Integration**)
3. **Download**, then restart Home Assistant.

### Manual

Copy only the `custom_components/savant_ha` directory into your Home Assistant config
(cloning the repository straight into `custom_components/savant_ha` would nest it one
level too deep):

```bash
git clone https://github.com/rohrsh/savant-ha.git /tmp/savant-ha
```

```bash
cp -r /tmp/savant-ha/custom_components/savant_ha /path/to/ha/config/custom_components/
```

Then restart Home Assistant.

## Configuration

Settings → Devices & Services → **Add Integration** → **Savant**.

1. Enter the host address (leave the port blank to auto-discover it — recommended).
2. **Log in** with a host-local account (username/password). This is how the host
   authenticates a headless client; on success the integration authenticates, downloads
   the host's config archive (`uiconfig.tar.gz`), and enumerates the device inventory.
3. **Choose what to import** — tick the groups you want: *Media players and AV
   remotes*, *Lights*, *Climate*, *Shades and covers*, *Fans*, *Savant scenes*, and
   *Room and endpoint diagnostic sensors*. Only groups present on your host are offered,
   with device counts. Unticked groups are left out of the device picker and their
   Home Assistant platforms are not loaded at all, so a media-only install never
   subscribes to lighting or HVAC state. The CoolMasterNet and Lutron prompts below only
   appear when you pick Climate or Lights/Shades respectively. **Reconfigure** offers the
   same choice again (pre-filled with your current selection). Entries created before
   this step keep every group.
4. **Choose the devices** to import. Devices are individual
   controllable things (a light load, a thermostat, a shade, a fan, an AV zone, …) — not
   rooms — and each maps to one or more Home Assistant entities. On accept the entities
   are created (you can move them freely afterwards).

Optional advanced settings (a cached `hostToken`, `cloudToken`/`configurationID`, or
extra room names) live behind the integration's **Configure** button.

### Existing Lutron lights and shades

When the built-in `lutron_caseta` integration (Caseta/RA2 Select) already provides lights or covers,
Savant setup and device refresh suggest duplicates using exact, case-insensitive names
and matching Home Assistant area names within the same entity domain. A light never
matches a cover. Whitespace differences and an area prefix in a
light name are ignored; missing areas, ambiguous matches, disabled entities/devices, and
disabled Lutron entries are not used. No bridge connection or device control is performed.

When matches are found, setup and **Reconfigure** show an **Exclude matching Lutron
devices?** choice before the device picker. **Exclude** omits matched lights and covers
from both the picker and the saved Savant inventory, including previously imported
matches. **Include** keeps them in the picker, selected by default. Unmatched Savant
devices are unaffected. If no matches are found, the extra choice is skipped.
The choice is a form with explicit radio-option labels and a Submit button, not a
two-row navigation menu. Labels include the matched-device count and action. The
CoolMasterNet prompt uses the same form-based approach with its own explicit options.
Select an option and submit it; there is no default choice. The explanatory paragraphs
still depend on translations, but the option text does not.

If diagnosing a blank prompt, DEBUG lines `Savant setup choice form step=lutron` and
`Savant setup choice form step=coolmaster` identify the screen actually being shown.
After updating, restart HA, close any old setup dialogs, and start a fresh Reconfigure
flow. Two blank navigation rows are not the new form UI.

Nothing is saved until you submit the final device picker, so canceling leaves the
existing configuration unchanged. Reconfiguration asks again rather than permanently
remembering the exclusion choice. Direct Lutron entities are never modified; existing
HA entity registry records are not automatically deleted. An empty remaining inventory
can be accepted without importing any devices.

These are name-and-area suggestions, not verified hardware matches: Savant addresses
have no established mapping to Caseta serial numbers. Renamed or differently assigned
lights may need manual selection. Other Lutron integrations are not matched.

Both the original and user-assigned entity/device names are considered, so renaming a
Lutron entity does not discard its original name as matching evidence. The effective
Home Assistant area still takes precedence; names never override an assigned area.

When Lutron is loaded, its cached load names are also matched. These omit the generated
floor/room hierarchy that HA adds to display names. Each cached name is associated with
its registry entity by Lutron's own serial-based unique ID (or its bridge-scoped ID when
no serial exists), separately for lights and covers. This mirrors Home Assistant's
open-source [`lutron_caseta` entity implementation](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/lutron_caseta/entity.py).
No network query is made. If Lutron's runtime cache is unavailable, matching uses registry
names only; arbitrary display-name suffixes or floor labels are never guessed.

With debug logging enabled, `Savant Lutron registry scan` reports configured entry,
light/cover, and eligible-light/cover counts, plus exclusions for disabled records or missing
areas and names. `source_names` counts registry entities associated with cached load names.
`Savant Lutron matching summary` reports the matching results separately
for `domain=light` and `domain=cover`, including missing areas, unmatched areas/names,
ambiguous candidates, and suggestions. These summaries contain counts only.

The temporary per-device name/area debug printing has been removed. Only aggregate
Lutron matching diagnostics are logged, even at DEBUG level.

## Entities (created only for the devices you import)

| Platform | Device source | Entities |
|---|---|---|
| Light | capture-backed `LightEntities` dimmer/color loads | brightness, on/off, and RGBW where state supports it |
| Climate | `HVACEntities` | configured captured modes/setpoints and read-only CoolMaster state |
| Cover | `ShadeEntities` | capture-backed shade open/close and position; stop is not captured |
| Fan | `FanEntities` | exact per-device state, read-only |
| Media Player (room) | one per room with imported sources | the room as Savant models it: state from `<room>.ActiveService`, room volume/mute, `source_list`/`select_source` over the room's sources, transport and now-playing from the active source, Music browse/search/play, and grouping of rooms on a shared music server via standard `media_player.join`/`unjoin` |
| Media Player (per source) | `ServiceImplementationZonedService` (one selectable source or Apple TV endpoint per room) | music: browsing, global catalog search, playback, now-playing, power, volume, transport, and album art; Apple TV: archive-declared power, volume, and play/pause |
| Scene | Savant dashboard `scenesAndFoldersReduced` updates | standalone native `scene.turn_on` activation |
| Remote | each imported AV endpoint with a declared request list | `remote.send_command` for any archive-declared request; on/off via PowerOn/PowerOff |
| Sensor / Binary sensor | `RoomCapabilities` rooms and imported AV endpoints | diagnostics: host connectivity, per-room active service/volume/zone flags, per-endpoint power/input/volume |

## Room media players

Savant's state is room-centric: each room reports the one service it is playing. The
integration therefore creates a **room media player** (`<Room> Media`) on the same
device as that room's diagnostic sensors:

- **State** is the active source's state, or `off` when the room is idle.
- **Source** lists the room's imported sources. Selecting one sends that source's
  `PowerOn`, and Savant switches the room (the previous source deactivates itself).
- **Turn on** restarts the room's `LastActiveService`, or the only source; with several
  sources and no history it asks you to pick one instead of guessing.
- **Volume / mute** are the room's own values, shown once rather than per source. They
  are controllable while a source is active.
- **Play/pause/skip, now-playing and artwork** come from the active source.
  **Browse/search/play media** go to the room's Savant Music source, which powers itself
  on before playback.
- **Grouping**: when the active source is a shared music server, `group_members` lists
  the rooms listening to it, and `media_player.join` / `unjoin` select or deselect that
  server in other rooms (using the routing described below).

The per-source media players remain: they do the actual work and keep the routing below
exact. New installs register them **hidden** (not disabled), so they stay usable from
automations and the entity list without cluttering auto-generated dashboards. Entities
that already exist keep their current visibility.

## Shared media-server routing

Several room media-player entities can be endpoint projections of one physical Savant
media server. For example, room-specific `Music` entities share the server's content and
transport while each entity controls whether its own Savant zone receives that server.
The integration derives this relationship from the configuration archive's physical
component and room identifiers, never from entity IDs, room names, or display names.

Every projected media-player entity exposes these stable state attributes:

- `savant_media_server_id`: opaque physical media-server identifier.
- `savant_zone_id`: opaque identifier for this endpoint's Savant zone.
- `savant_selected_zone_entity_ids`: sorted exact HA entity IDs currently selected for
  the server. Every alias of one server reports the same list.
- `savant_shared_media_server`: `true` when multiple imported endpoints project the server.

Use `savant_ha.set_media_server_zones` before `media_player.play_media`. The
`zone_entity_ids` list is the complete desired replacement state, not an incremental
addition. The service removes extra selected endpoints first, adds missing endpoints,
waits for authoritative Savant state pushes, and returns the verified final topology when
the caller requests response data. Repeating an already-correct request sends no controls.

Music browse IDs are opaque, deterministic hashes of the captured node. They remain
stable when the same catalog node is returned again, while the Savant routing metadata
remains private to the integration.
Playlist normal-play and shuffle actions are returned as playable browse entries. Shuffle
is intentionally not exposed as a standalone media-player toggle because Savant has no
captured standalone shuffle setter.

Isolate playback to one room:

```yaml
action: savant_ha.set_media_server_zones
data:
  entity_id: media_player.master_bath_music
  zone_entity_ids:
    - media_player.master_bath_music
response_variable: savant_route
```

Select multiple rooms:

```yaml
action: savant_ha.set_media_server_zones
data:
  entity_id: media_player.master_bath_music
  zone_entity_ids:
    - media_player.master_bath_music
    - media_player.dining_room_music
```

Deselect every imported endpoint for the server:

```yaml
action: savant_ha.set_media_server_zones
data:
  entity_id: media_player.master_bath_music
  zone_entity_ids: []
```

An empty route works when every currently selected endpoint supports Savant `PowerOff`;
otherwise the service fails explicitly and reports the actual selected set. Only imported
endpoint projections have HA entity IDs and can be requested or reported, but the complete
archive topology is retained so exact replacement also deselects active, unimported
endpoints. Existing entries must be reconfigured once to acquire that complete topology;
until then exact routing and grouping fail explicitly while existing media controls continue
to work.

Standard Home Assistant grouping is also supported for shared servers. `group_members`
reports the same exact selected endpoint IDs, `media_player.join` adds same-server
endpoints, and `media_player.unjoin` removes the targeted endpoint. Cross-server joins are
rejected. Grouping and exact replacement use the same per-server lock and verification
path. Playback itself remains backward compatible and does not implicitly isolate a room.

## Limitations / open questions

These are inherited from the sibling protocol document — see `PROTOCOL.md` §7:

- **`secretKey`** is issued by the host on login but its purpose is unknown; the
  integration ignores it.
- **Fans / door-locks / garages** have incomplete or state-only evidence and no captured
  general control shapes. Fans are read-only; locks and garages are not imported.
- Shade positioning is available only for capture-backed `ShadeSet` and `RFShadeSet` backends.
- Music mute/repeat remain one-way (`MuteOn`/`RepeatOn`) and are therefore not advertised
  as Home Assistant controls. Search filters and pagination beyond the captured defaults
  are not exposed.

New climate imports honor archive `isCelsius`, range, and mode-capability fields. Legacy
fallback devices retain the original Fahrenheit defaults.

## Debug logging

Logging is controlled through Home Assistant's native integration UI; no YAML edit or
Savant-specific toggle is needed. Open **Settings → Devices & Services → Savant** and
use the integration menu's **Enable debug logging** or log-level control (wording varies
by Home Assistant version). Select **Debug** if a level picker is shown.

For missing scenes, enable logging **before reloading the Savant integration** so the
capture includes authentication and the initial dashboard subscription. After reproducing,
disable debug logging or restore the previous level. Download the captured log when Home
Assistant offers it. Logging covers all Savant entries, not just one host.

Scene diagnostics distinguish these stages without logging scene names, IDs, or payloads:

- `Savant scene subscription sent`: the scene-list registration was sent.
- `Savant dashboard update has no scene inventory`: a dashboard message arrived, but it
  did not contain the expected scene-list state.
- `Savant dashboard scene candidate`: structural details of an unrecognized message,
  including field types, whether `state` exists, and the `value` container size. Only known
  field names and the subscribed dashboard state names are shown; other keys and values
  are masked. Output is limited to five messages and twelve fields per message, without
  limiting scene parsing. This distinguishes unrelated `RecentServices` replies from
  unexpected scene-list wrappers without exposing scene contents.
- `Savant scene inventory ignored`: the scene-list value had an unexpected type.
- `Savant scene inventory received`: total entries, accepted unique scenes, and rejected
  or duplicate entries; zero entries means the host supplied an empty inventory.
- `Savant scene inventory delivered`: inventory reached the hub, with callback count.
- `Savant scene platform waiting`: the scene platform loaded before an inventory arrived.
- `Savant scene reconciliation`: inventory size, additions requested, live removals,
  and tracked scenes. Additions do not prove entities are enabled in Home Assistant.

Review debug logs before sharing: other diagnostics can include search text, state keys,
and network details. Do not share credentials or unredacted client-site information.

### Search diagnostics

The same control enables music-search diagnostics. For installations managed through
YAML, the equivalent logger configuration is:

```yaml
logger:
  logs:
    custom_components.savant_ha: debug
```

Search traces include queue wait, search `correlation_id`, RPC `request_id`, response
time, retry source (`initial`, `refresh`, or `watchdog`), and final outcome/elapsed time.
The same correlation ID spans all attempts; RPC request IDs change on each attempt.
Refreshes with `reason=no_local_waiter` have no matching active search in this client;
their arrival alone does not measure HA search latency. Logs include search terms, so
review them for private information before sharing.

## Live check (no Home Assistant needed)

`tools/savant_live_check.py` logs in to your host with the integration's own client,
lists each room's sources (with the `<Room> Media` names), then records room and source
state while you use the Savant app, and reports:

- **Q1** whether switching a room from source A to B deactivates A,
- **Q2** whether the host reports `LastActiveService` (used by room turn-on),
- **Q3** whether active-service IDs match the imported sources.

```bash
pip install aiohttp msgpack
```

```bash
python tools/savant_live_check.py --host <HOST_IP> --user <LOCAL_USER>
```

Add `--exercise <Room> "<Source A>" "<Source B>"` to have the script itself play A,
switch to B and turn the room off (it asks first). The password comes from
`$SAVANT_PASSWORD` or a prompt. The JSONL recording omits host address, homeId, UIDs
and credentials but does contain room/source names; it is git-ignored.

## Development

Provenance rules — this repo's `AGENTS.md` — are mandatory: every change must be paired
with the prompt that produced it (recorded in `savant_ha_prompts.csv`), and no
proprietary knowledge may be introduced.
