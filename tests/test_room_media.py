"""Tests for the room-level media model (room selection rules)."""

from __future__ import annotations

import pytest

from custom_components.savant_ha.room_media import (
    active_source,
    group_member_rooms,
    join_targets,
    room_sources,
    source_names,
    turn_on_source,
)


class FakeSource:
    def __init__(
        self,
        entity_id: str,
        room: str,
        name: str,
        *,
        service_id: str = "",
        server_id: str = "server:other",
        active: bool = False,
    ) -> None:
        self.entity_id = entity_id
        self.route_id = entity_id
        self.savant_media_server_id = server_id
        self._room = room
        self._service_id = service_id
        self._device_name = name
        self.active = active

    @property
    def _activity_is_addressable(self) -> bool:
        return bool(self._room and self._service_id)

    def _is_active_service(self) -> bool:
        return self.active


def _study() -> list[FakeSource]:
    return [
        FakeSource("media_player.study_music", "Study", "Music", service_id="s-music",
                   server_id="server:music"),
        FakeSource("media_player.study_apple_tv", "Study", "Apple TV", service_id="s-atv"),
        FakeSource("media_player.study_foxtel", "Study", "Foxtel", service_id="s-fox"),
    ]


def test_room_sources_filters_by_room_and_orders_by_name():
    sources = _study() + [FakeSource("media_player.den_music", "Den", "Music")]
    assert [s.entity_id for s in room_sources(sources, "Study")] == [
        "media_player.study_apple_tv",
        "media_player.study_foxtel",
        "media_player.study_music",
    ]
    # Sources without a room never form a room view.
    assert room_sources([FakeSource("media_player.zone_1", "", "Audio Zone 1")], "") == []


def test_source_names_are_unique_per_room():
    a = FakeSource("media_player.a", "Den", "Amp")
    b = FakeSource("media_player.b", "Den", "Amp")
    c = FakeSource("media_player.c", "Den", "Amp (2)")
    names = source_names([a, b, c])
    assert list(names) == ["Amp", "Amp (2)", "Amp (2) (2)"]
    assert names["Amp"] is a and names["Amp (2)"] is b and names["Amp (2) (2)"] is c


def test_active_source_requires_an_addressable_service():
    sources = _study()
    assert active_source(sources) is None
    sources[1].active = True
    assert active_source(sources) is sources[1]
    # A legacy endpoint without a service ID cannot claim the room.
    legacy = FakeSource("media_player.legacy", "Study", "Legacy", active=True)
    assert active_source([legacy]) is None


def test_turn_on_prefers_last_active_service_then_a_sole_source():
    sources = _study()
    assert turn_on_source(sources, "s-fox") is sources[2]
    assert turn_on_source(sources, "other, s-atv") is sources[1]
    # Ambiguous: several sources and no usable history -> caller must ask.
    assert turn_on_source(sources, "") is None
    assert turn_on_source(sources, None) is None
    assert turn_on_source(sources[:1], "unknown") is sources[0]


def test_group_member_rooms_maps_selected_sources_to_rooms():
    study = _study()
    den = FakeSource("media_player.den_music", "Den", "Music", service_id="d-music",
                     server_id="server:music")
    endpoints = {s.entity_id: s for s in [*study, den]}
    assert group_member_rooms(
        ["media_player.den_music", "media_player.study_music", "media_player.gone"],
        endpoints,
    ) == ["Den", "Study"]


def test_join_targets_pick_each_rooms_source_on_the_anchor_server():
    study = _study()
    den = FakeSource("media_player.den_music", "Den", "Music", service_id="d-music",
                     server_id="server:music")
    den_tv = FakeSource("media_player.den_tv", "Den", "TV", service_id="d-tv")
    kitchen = FakeSource("media_player.kitchen_radio", "Kitchen", "Radio", service_id="k-r")
    endpoints = [*study, den, den_tv, kitchen]
    anchor = study[0]
    assert join_targets(anchor, ["Study", "Den"], endpoints) == ["media_player.den_music"]
    with pytest.raises(ValueError, match="Kitchen has no source"):
        join_targets(anchor, ["Kitchen"], endpoints)
