#!/usr/bin/env python3
"""Live checker for a Savant host: inventory, watch, and (opt-in) exercise.

Run it on the same LAN as the host, without Home Assistant. It reuses the
integration's own client (``custom_components/savant_ha/savant_client.py``), so
it exercises exactly the login, discovery and subscription paths the integration
uses.

Stages:

1. **Inventory**: log in, download the config archive, and print each room's AV
   sources plus the entity names the room media players would show.
2. **Watch** (default 120 s): subscribe to every room's state and each source's
   endpoint keys and print every change while you use the Savant app.
3. **Exercise** (opt-in, ``--exercise ROOM SOURCE_A SOURCE_B``): the script sends
   PowerOn A, then PowerOn B, then PowerOff B in one room. This changes what is
   playing in your house, so it asks first unless ``--yes`` is given.

At the end it prints a verdict on the open questions behind the room media
players (README "Room media players"):

* Q1: when a room switches from source A to source B, does the host deactivate A?
* Q2: does the host report ``<room>.LastActiveService`` (used by room turn_on)?
* Q3: do the active-service IDs match the imported sources, and do names read well?

A JSONL recording is written alongside. It carries state keys and values only:
host IP, homeId, client/host UIDs and credentials are never written. Room and
source names do appear, so look it over before sharing it outside your household.

Usage:
    python tools/savant_live_check.py --host <HOST_IP> --user <LOCAL_USER>
    python tools/savant_live_check.py --host <HOST_IP> --user <LOCAL_USER> \\
        --exercise Study "Music" "Apple TV"

The password is read from ``$SAVANT_PASSWORD`` or prompted for.
Requires: ``pip install aiohttp msgpack``.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import logging
import os
import signal
import sys
import time
import types
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_DIR = os.path.join(_REPO, "custom_components", "savant_ha")
_PKG = "custom_components.savant_ha"


def _load_integration_modules() -> dict[str, types.ModuleType]:
    """Load the HA-free integration modules without importing ``__init__`` (HA)."""
    for parent in ("custom_components", _PKG):
        sys.modules.setdefault(parent, types.ModuleType(parent))
    loaded: dict[str, types.ModuleType] = {}
    for name in ("const", "media_routing", "room_media", "uiconfig", "savant_client"):
        full = f"{_PKG}.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, os.path.join(_PKG_DIR, f"{name}.py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
        loaded[name] = sys.modules[full]
    return loaded


# ---------------------------------------------------------------- inventory model


@dataclass
class Source:
    """One archive AV endpoint in a room (mirrors the per-source media player)."""

    room: str
    name: str
    component: str
    logical: str
    service_type: str
    service_id: str
    variant_id: str
    requests: list[str]


def sources_from_devices(devices: list[Any]) -> dict[str, list[Source]]:
    """Group archive media endpoints by room, in display order."""
    rooms: dict[str, list[Source]] = defaultdict(list)
    for device in devices:
        if getattr(device, "device_type", "") != "media_player" or not device.room:
            continue
        extra = device.extra or {}
        rooms[device.room].append(
            Source(
                room=device.room,
                name=device.name,
                component=device.component,
                logical=device.zone,
                service_type=str(extra.get("service_type") or ""),
                service_id=str(extra.get("service_id") or ""),
                variant_id=str(extra.get("variant_id") or "1"),
                requests=list(extra.get("requests") or []),
            )
        )
    for room in rooms:
        rooms[room].sort(key=lambda s: s.name.casefold())
    return dict(sorted(rooms.items()))


def unique_names(sources: list[Source]) -> dict[str, Source]:
    """Same suffixing rule as ``room_media.source_names``."""
    names: dict[str, Source] = {}
    for source in sources:
        name, n = source.name, 2
        while name in names:
            name = f"{source.name} ({n})"
            n += 1
        names[name] = source
    return names


# ------------------------------------------------------------------ analysis


def _service_set(value: Any) -> frozenset[str]:
    if not isinstance(value, str):
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


@dataclass
class Recorder:
    """Collect timestamped state pushes and answer the room-player questions."""

    rooms: dict[str, list[Source]]
    events: list[tuple[float, str, Any]] = field(default_factory=list)
    marks: list[tuple[float, str]] = field(default_factory=list)
    _last: dict[str, Any] = field(default_factory=dict)

    def record(self, t: float, key: str, value: Any) -> tuple[Any, bool]:
        """Store one push; return (previous value, whether it changed)."""
        previous = self._last.get(key, _MISSING)
        self._last[key] = value
        self.events.append((t, key, value))
        return (None if previous is _MISSING else previous), previous != value

    def mark(self, t: float, label: str) -> None:
        self.marks.append((t, label))

    # Q1 -----------------------------------------------------------------
    def room_activity(self, room: str) -> list[tuple[float, frozenset[str]]]:
        """Timeline of the room's active-service set (ActiveServices, else ActiveService)."""
        timeline: list[tuple[float, frozenset[str]]] = []
        plural = f"{room}.ActiveServices"
        singular = f"{room}.ActiveService"
        has_plural = any(key == plural for _, key, _ in self.events)
        for t, key, value in self.events:
            if key == (plural if has_plural else singular):
                current = _service_set(value)
                if not timeline or timeline[-1][1] != current:
                    timeline.append((t, current))
        return timeline

    def switch_findings(self, room: str, idle_gap: float = 3.0) -> list[str]:
        findings: list[str] = []
        timeline = self.room_activity(room)
        for (_t0, before), (t1, after) in zip(timeline, timeline[1:], strict=False):
            if len(after) > 1:
                findings.append(
                    f"+{t1:.1f}s MULTIPLE active at once: {sorted(after)}"
                )
            if before and after and before != after:
                dropped = before - after
                if dropped:
                    findings.append(f"+{t1:.1f}s switched {sorted(before)} -> {sorted(after)}: "
                                    "previous source deactivated")
                else:
                    findings.append(f"+{t1:.1f}s added {sorted(after - before)} while "
                                    f"{sorted(before)} stayed active")
        # A -> idle -> B within a short gap is also a clean switch, just not atomic.
        for (_t0, a), (t1, idle), (t2, b) in zip(
            timeline, timeline[1:], timeline[2:], strict=False
        ):
            if a and not idle and b and a != b and t2 - t1 <= idle_gap:
                findings.append(f"+{t2:.1f}s switched {sorted(a)} -> {sorted(b)} via "
                                f"{t2 - t1:.1f}s idle")
        return findings

    # Q2 -----------------------------------------------------------------
    def last_active(self, room: str) -> str:
        key = f"{room}.LastActiveService"
        values = [v for _, k, v in self.events if k == key]
        if not values:
            return "never reported"
        nonempty = [v for v in values if isinstance(v, str) and v.strip()]
        if not nonempty:
            return "reported, always empty"
        known = {s.service_id for s in self.rooms.get(room, [])}
        latest = _service_set(nonempty[-1])
        match = "matches an imported source" if latest & known else "does NOT match any imported source"
        return f"reported ({match})"

    # Q3 -----------------------------------------------------------------
    def unknown_services(self, room: str) -> set[str]:
        known = {s.service_id for s in self.rooms.get(room, []) if s.service_id}
        seen: set[str] = set()
        for _, active in self.room_activity(room):
            seen |= active
        return seen - known

    def report(self) -> str:
        lines = ["", "=" * 72, "REPORT", "=" * 72]
        for room, sources in self.rooms.items():
            names = list(unique_names(sources))
            lines.append(f"\n[{room}]  entity: '{room} Media'  sources: {names}")
            timeline = self.room_activity(room)
            if not timeline:
                lines.append("  Q1 activity: no ActiveService pushes seen (room untouched?)")
            else:
                for finding in self.switch_findings(room) or ["no source switches observed"]:
                    lines.append(f"  Q1 {finding}")
            lines.append(f"  Q2 LastActiveService: {self.last_active(room)}")
            unknown = self.unknown_services(room)
            lines.append(
                "  Q3 active-service IDs: "
                + ("all match imported sources" if not unknown
                   else f"UNMATCHED {sorted(unknown)} (room player cannot name these)")
            )
            for attr in ("CurrentVolume", "IsMuted"):
                seen = any(k == f"{room}.{attr}" for _, k, _ in self.events)
                lines.append(f"     {attr}: {'reported' if seen else 'not reported'}")
        lines.append(
            "\nVerdict guide: Q1 'previous source deactivated' or 'via idle' = room source"
            "\nswitching is safe; 'stayed active'/'MULTIPLE' = the room player needs to send"
            "\nPowerOff to the old source first. Q2 'never reported' = room turn_on will ask"
            "\nfor a source in multi-source rooms."
        )
        return "\n".join(lines)


_MISSING = object()


# ------------------------------------------------------------------ runtime


def _subscribe_keys(mods: dict[str, types.ModuleType], rooms: dict[str, list[Source]]) -> list[str]:
    const = mods["const"]
    keys = list(const.build_default_subscribe_keys(list(rooms)))
    keys += const.room_state_keys(set(rooms))
    for sources in rooms.values():
        for s in sources:
            keys += const.endpoint_state_keys(s.component, s.logical)
            if s.service_type == "SVC_AV_SAVANTMUSIC":
                keys += const.audio_zone_state_keys(s.component, s.logical, s.variant_id)
    return list(dict.fromkeys(keys))


async def _run(args: argparse.Namespace) -> int:
    mods = _load_integration_modules()
    client_mod = mods["savant_client"]
    password = os.environ.get("SAVANT_PASSWORD") or getpass.getpass("Savant password: ")

    port, home_id = args.port, ""
    if not port:
        info = await client_mod.discover_host(args.host, timeout=3.0)
        if info is None or info.port <= 0:
            print("Discovery failed; pass --port with the host's current control port.")
            return 2
        port, home_id = info.port, info.home_id
        print(f"Discovered host: model={info.host_model or '?'} build={info.build_version or '?'}")

    print("Stage 1: logging in and downloading the config archive …")
    probe = await client_mod.probe_host(
        args.host, port, home_id=home_id, username=args.user, password=password, timeout=15.0
    )
    if not probe.authorized:
        print("Login failed (authorized=False). Check the host-local account.")
        return 3
    rooms = sources_from_devices(probe.devices)
    if args.room:
        rooms = {r: s for r, s in rooms.items() if r in args.room}
    print(f"Found {sum(map(len, rooms.values()))} AV sources in {len(rooms)} rooms:")
    for room, sources in rooms.items():
        print(f"  {room} Media:")
        for name, s in unique_names(sources).items():
            flag = "" if s.service_id else "  (no service ID: cannot be matched)"
            print(f"    - {name}  [{s.service_type}]{flag}")

    recorder = Recorder(rooms)
    start = time.monotonic()
    out_path = args.out or f"savant_live_check_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    out = open(out_path, "w", encoding="utf-8")  # noqa: SIM115 - closed in finally
    out.write(json.dumps({"type": "inventory", "rooms": {
        r: [{"name": s.name, "service_type": s.service_type, "service_id": s.service_id,
             "requests": s.requests} for s in ss] for r, ss in rooms.items()}}) + "\n")

    relevant = tuple(f"{room}." for room in rooms) + tuple(
        f"{s.component}.{s.logical}." for ss in rooms.values() for s in ss
    )

    def on_state(key: str, value: Any) -> None:
        if not key.startswith(relevant):
            return
        t = time.monotonic() - start
        previous, changed = recorder.record(t, key, value)
        out.write(json.dumps({"t": round(t, 3), "key": key, "value": value}, default=str) + "\n")
        if changed and (args.verbose or not key.endswith(("ElapsedTime", "RemainingTime"))):
            print(f"  +{t:6.1f}s {key}: {previous!r} -> {value!r}")

    client = client_mod.SavantClient(
        args.host, port, home_id=home_id, username=args.user, password=password,
        subscribe_keys=_subscribe_keys(mods, rooms),
    )
    client.on_state_update = on_state
    receive: asyncio.Task[None] | None = None
    try:
        await client._connect()
        receive = asyncio.ensure_future(client._receive_loop())
        for _ in range(100):
            if client.authorized:
                break
            await asyncio.sleep(0.1)
        if not client.authorized:
            print("Watch session did not authorize.")
            return 3
        await asyncio.sleep(3)  # initial state burst

        if args.exercise:
            await _exercise(args, client, rooms, recorder, start)
        else:
            print(f"\nStage 2: watching for {args.duration}s. Use the Savant app now: switch a "
                  "room between two sources, turn it off, change volume. Ctrl-C ends early.")
            stop = asyncio.Event()
            try:
                asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
            except (NotImplementedError, RuntimeError):  # Windows: Ctrl-C ends without report
                pass
            try:
                await asyncio.wait_for(stop.wait(), args.duration)
            except TimeoutError:
                pass
    finally:
        if receive is not None:
            receive.cancel()
            try:
                await receive
            except BaseException:  # noqa: BLE001 - shutting down
                pass
        await client._disconnect()
        out.close()
    print(recorder.report())
    print(f"\nRecording: {os.path.abspath(out_path)}")
    return 0


async def _exercise(args, client, rooms, recorder, start) -> None:  # noqa: ANN001
    room, name_a, name_b = args.exercise
    names = unique_names(rooms.get(room, []))
    missing = [n for n in (name_a, name_b) if n not in names]
    if missing:
        raise SystemExit(f"Unknown source(s) in {room}: {missing}. Choices: {list(names)}")
    a, b = names[name_a], names[name_b]
    if not args.yes:
        reply = input(f"\nStage 3 will play {name_a!r}, switch to {name_b!r}, then turn "
                      f"{room} off. Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Skipped exercise.")
            return

    async def step(label: str, source: Source, verb: str) -> None:
        t = time.monotonic() - start
        recorder.mark(t, label)
        print(f"\n  +{t:6.1f}s >>> {label}")
        await client.service_request(
            verb, component=source.component, service_type=source.service_type, zone=room,
            logical_component=source.logical, variant_id=source.variant_id,
            include_request_id=True,
        )
        await asyncio.sleep(args.step_wait)

    await step(f"PowerOn {name_a}", a, "PowerOn")
    await step(f"PowerOn {name_b} (switch)", b, "PowerOn")
    await step(f"PowerOff {name_b}", b, "PowerOff")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", required=True, help="Savant host IP or hostname")
    parser.add_argument("--port", type=int, default=0, help="control port (default: discover)")
    parser.add_argument("--user", required=True, help="host-local username")
    parser.add_argument("--duration", type=int, default=120, help="watch seconds (default 120)")
    parser.add_argument("--room", action="append", help="limit to this room (repeatable)")
    parser.add_argument("--exercise", nargs=3, metavar=("ROOM", "SOURCE_A", "SOURCE_B"),
                        help="send PowerOn A, PowerOn B, PowerOff B in ROOM and observe")
    parser.add_argument("--step-wait", type=float, default=10.0, help="seconds between steps")
    parser.add_argument("--yes", action="store_true", help="do not ask before --exercise")
    parser.add_argument("--out", help="JSONL recording path")
    parser.add_argument("--verbose", action="store_true", help="also print elapsed-time ticks")
    parser.add_argument("--debug", action="store_true", help="integration debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
