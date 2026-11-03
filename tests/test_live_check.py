"""Tests for the live checker's offline analysis (tools/savant_live_check.py)."""

from __future__ import annotations

import importlib.util
import os
import sys
from types import SimpleNamespace

_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "savant_live_check.py")
_spec = importlib.util.spec_from_file_location("savant_live_check", _PATH)
live = importlib.util.module_from_spec(_spec)
sys.modules["savant_live_check"] = live  # dataclasses resolve their module
_spec.loader.exec_module(live)


def _device(room, name, sid, svc="SVC_AV_GENERALAUDIO"):
    return SimpleNamespace(
        device_type="media_player", room=room, name=name, component=name, zone="zone",
        extra={"service_type": svc, "service_id": sid, "variant_id": "1", "requests": []},
    )


def _rooms():
    return live.sources_from_devices([
        _device("Study", "Music", "s-music", "SVC_AV_SAVANTMUSIC"),
        _device("Study", "Apple TV", "s-atv"),
        _device("Den", "Amp", "d-amp1"),
        _device("Den", "Amp", "d-amp2"),
        SimpleNamespace(device_type="light", room="Study", name="Lamp", component="",
                        zone="", extra={}),
    ])


def test_inventory_groups_media_by_room_with_unique_names():
    rooms = _rooms()
    assert list(rooms) == ["Den", "Study"]
    assert [s.name for s in rooms["Study"]] == ["Apple TV", "Music"]
    assert list(live.unique_names(rooms["Den"])) == ["Amp", "Amp (2)"]


def test_clean_switch_is_reported_as_deactivating_previous_source():
    rec = live.Recorder(_rooms())
    rec.record(0.0, "Study.ActiveServices", "")
    rec.record(1.0, "Study.ActiveServices", "s-music")
    rec.record(5.0, "Study.ActiveServices", "s-atv")
    rec.record(9.0, "Study.LastActiveService", "s-atv")
    findings = rec.switch_findings("Study")
    assert findings == ["+5.0s switched ['s-music'] -> ['s-atv']: previous source deactivated"]
    assert rec.last_active("Study") == "reported (matches an imported source)"
    assert rec.unknown_services("Study") == set()


def test_overlap_and_idle_switch_and_unknown_ids_are_flagged():
    rec = live.Recorder(_rooms())
    rec.record(1.0, "Study.ActiveServices", "s-music")
    rec.record(2.0, "Study.ActiveServices", "s-music,s-atv")
    rec.record(3.0, "Study.ActiveServices", "")
    rec.record(4.0, "Study.ActiveServices", "s-other")
    findings = rec.switch_findings("Study")
    assert any("MULTIPLE active" in f for f in findings)
    assert any("stayed active" in f for f in findings)
    assert any("via 1.0s idle" in f for f in findings)
    assert rec.unknown_services("Study") == {"s-other"}
    assert rec.last_active("Study") == "never reported"


def test_singular_key_is_used_when_plural_is_absent_and_report_renders():
    rec = live.Recorder(_rooms())
    rec.record(1.0, "Den.ActiveService", "d-amp1")
    rec.record(2.0, "Den.LastActiveService", "")
    assert rec.room_activity("Den") == [(1.0, frozenset({"d-amp1"}))]
    assert rec.last_active("Den") == "reported, always empty"
    text = rec.report()
    assert "[Den]  entity: 'Den Media'  sources: ['Amp', 'Amp (2)']" in text
    assert "no ActiveService pushes seen" in text  # Study untouched


def test_record_reports_previous_value_and_change():
    rec = live.Recorder({})
    assert rec.record(0.0, "Den.IsMuted", 0) == (None, True)
    assert rec.record(1.0, "Den.IsMuted", 0) == (0, False)
    assert rec.record(2.0, "Den.IsMuted", 1) == (0, True)


def test_end_to_end_exercise_against_a_fake_host(monkeypatch, tmp_path, capsys):
    """Drive the real orchestration with the client swapped for a fake host."""
    import asyncio
    import json

    mods = live._load_integration_modules()
    client_mod = mods["savant_client"]
    devices = [
        _device("Study", "Music", "s-music", "SVC_AV_SAVANTMUSIC"),
        _device("Study", "Apple TV", "s-atv"),
    ]

    async def fake_probe(host, port, **kwargs):
        return SimpleNamespace(authorized=True, devices=devices)

    instances = []

    class FakeClient:
        def __init__(self, host, port, **kwargs):
            self.subscribe_keys = kwargs["subscribe_keys"]
            self.authorized = False
            self.on_state_update = None
            self.sent = []
            instances.append(self)

        async def _connect(self):
            self.authorized = True

        async def _receive_loop(self):
            self.on_state_update("Study.ActiveServices", "")
            self.on_state_update("Study.CurrentVolume", 20)
            self.on_state_update("Elsewhere.ActiveServices", "ignored")
            await asyncio.Event().wait()

        async def service_request(self, request, *, component, zone, **kwargs):
            self.sent.append((zone, component, request))
            sid = {"Music": "s-music", "Apple TV": "s-atv"}[component]
            # Fake host: PowerOn makes the source the room's only active one.
            self.on_state_update(f"{zone}.ActiveServices", sid if request == "PowerOn" else "")
            if request == "PowerOn":
                self.on_state_update(f"{zone}.LastActiveService", sid)

        async def _disconnect(self):
            pass

    monkeypatch.setattr(client_mod, "probe_host", fake_probe)
    monkeypatch.setattr(client_mod, "SavantClient", FakeClient)
    monkeypatch.setattr(live.asyncio, "sleep", _fast_sleep(asyncio.sleep))
    monkeypatch.setenv("SAVANT_PASSWORD", "secret-pass")
    out = tmp_path / "rec.jsonl"
    rc = live.main([
        "--host", "192.0.2.10", "--port", "51152", "--user", "tester",
        "--exercise", "Study", "Music", "Apple TV", "--yes",
        "--step-wait", "0", "--out", str(out),
    ])
    assert rc == 0
    client = instances[0]
    assert client.sent == [
        ("Study", "Music", "PowerOn"), ("Study", "Apple TV", "PowerOn"),
        ("Study", "Apple TV", "PowerOff"),
    ]
    assert "Study.ActiveServices" in client.subscribe_keys
    assert "Study.LastActiveService" in client.subscribe_keys
    text = capsys.readouterr().out
    assert "switched ['s-music'] -> ['s-atv']: previous source deactivated" in text
    assert "LastActiveService: reported (matches an imported source)" in text
    recording = out.read_text()
    lines = [json.loads(line) for line in recording.splitlines()]
    assert lines[0]["type"] == "inventory"
    assert all(line.get("key", "Study.").startswith("Study.") for line in lines[1:])
    for secret in ("192.0.2.10", "51152", "tester", "secret-pass"):
        assert secret not in recording


def _fast_sleep(real_sleep):
    async def sleep(_delay, *args, **kwargs):
        await real_sleep(0)
    return sleep
