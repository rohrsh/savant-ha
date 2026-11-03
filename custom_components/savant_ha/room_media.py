"""Room-level media model: one Savant room, many selectable AV sources.

Savant's own state is room-centric. Each room pushes ``<room>.ActiveService`` (the
service currently playing there), ``<room>.LastActiveService``, ``<room>.CurrentVolume``
and ``<room>.IsMuted`` (PROTOCOL.md §5.4 / §5.4b). The per-source media players are
projections of that state: "source X is active in room Y". This module groups those
projections back into one room view without any Home Assistant imports, so the
selection rules are unit-testable on their own.

Nothing here is new protocol knowledge. Switching a room's source is the existing
per-source ``PowerOn`` (which goes through the shared-server routing in
``media_routing``). The host deactivates the previously active service itself, as
observed for shared-source power changes (PROTOCOL.md §5.4b).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol


class RoomSource(Protocol):
    """The slice of a per-source media player that the room view relies on."""

    entity_id: str | None
    route_id: str
    savant_media_server_id: str
    _room: str
    _service_id: str
    _device_name: str

    @property
    def _activity_is_addressable(self) -> bool: ...

    def _is_active_service(self) -> bool: ...


def room_sources(endpoints: Iterable[Any], room: str) -> list[Any]:
    """Return the registered sources of one room in a stable display order."""
    return sorted(
        (e for e in endpoints if room and getattr(e, "_room", "") == room),
        key=lambda e: (str(getattr(e, "_device_name", "")).casefold(), e.route_id),
    )


def source_names(sources: Sequence[RoomSource]) -> dict[str, RoomSource]:
    """Map a unique, human-readable source name to each source.

    Names come from the archive alias (the device name the picker showed). Identical
    names in one room are suffixed ``(2)``, ``(3)``, … in display order, so
    ``select_source`` always resolves to exactly one endpoint.
    """
    names: dict[str, RoomSource] = {}
    for source in sources:
        base = str(source._device_name or source.route_id)
        name = base
        n = 2
        while name in names:
            name = f"{base} ({n})"
            n += 1
        names[name] = source
    return names


def active_source(sources: Sequence[RoomSource]) -> RoomSource | None:
    """Return the source the room reports as active, if exactly addressable.

    Only sources with a configured service ID can be matched against
    ``ActiveService``; legacy un-addressable endpoints never claim the room.
    """
    for source in sources:
        if source._activity_is_addressable and source._is_active_service():
            return source
    return None


def turn_on_source(
    sources: Sequence[RoomSource], last_active_service: object
) -> RoomSource | None:
    """Choose what ``turn_on`` should start when the room is off.

    Preference: the room's ``LastActiveService`` (a comma-separated service list, as
    pushed for ``ActiveService``), then the only source when there is just one.
    Otherwise ``None``: the caller must ask for an explicit source rather than guess.
    """
    if isinstance(last_active_service, str) and last_active_service:
        wanted = {part.strip() for part in last_active_service.split(",") if part.strip()}
        for source in sources:
            if source._service_id and source._service_id in wanted:
                return source
    if len(sources) == 1:
        return sources[0]
    return None


def group_member_rooms(
    selected_source_entity_ids: Iterable[str],
    endpoints_by_entity_id: dict[str, Any],
) -> list[str]:
    """Translate a shared server's selected source entities into their rooms."""
    rooms: list[str] = []
    for entity_id in selected_source_entity_ids:
        endpoint = endpoints_by_entity_id.get(entity_id)
        room = getattr(endpoint, "_room", "") if endpoint is not None else ""
        if room and room not in rooms:
            rooms.append(room)
    return rooms


def join_targets(
    anchor: RoomSource, member_rooms: Iterable[str], endpoints: Iterable[Any]
) -> list[str]:
    """Return, per target room, the source entity on the anchor's media server.

    Raises ``ValueError`` when a requested room has no source on that server: the
    room cannot hear this server, so grouping it would be a guess.
    """
    endpoints = list(endpoints)
    targets: list[str] = []
    for room in member_rooms:
        if room == anchor._room:
            continue
        match = next(
            (
                e
                for e in room_sources(endpoints, room)
                if e.savant_media_server_id == anchor.savant_media_server_id
                and e.entity_id
            ),
            None,
        )
        if match is None:
            raise ValueError(f"{room} has no source on this Savant media server")
        targets.append(match.entity_id)
    return targets
