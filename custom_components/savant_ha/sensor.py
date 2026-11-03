"""Sensor platform: per-room diagnostics from the host's own room state.

The host pushes an asserted per-room state surface (`<room>.*`, PROTOCOL.md §5.2)
reflecting the commands it last issued. These sensors expose that assertion so
downstream automations can corroborate it against actual device state.

Which sensors a room gets is driven by the archive's ``RoomCapabilities`` flags
(carried on the imported ``room`` device); config entries predating room import
fall back to an AV-only surface for rooms hosting media players.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_TYPE_MEDIA_PLAYER, DEVICE_TYPE_ROOM, DOMAIN
from .control import coerce_number
from .entity import SavantEntity
from .hub import SavantHub

_IDLE = "idle"


class SavantRoomActiveServiceSensor(SavantEntity, SensorEntity):
    """The source a room is playing right now, from the host's point of view.

    State is `<room>.ActiveService` (PROTOCOL.md §5.2/§5.4b) — the authoritative
    room-activity push — mapped to the endpoint's configured name when the service
    ID matches an imported media player. The raw service ID stays available as an
    attribute so entity state can be corroborated against it.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Active service"

    def __init__(self, hub: SavantHub, room: str, names: dict[str, str]) -> None:
        super().__init__(hub, device_key=f"room:{room}", device_name=room, area=room)
        self._room = room
        # service ID -> configured endpoint name, for a friendly state value.
        self._names = names
        self._attr_unique_id = f"{hub.uid}_room_service_{room}"

    @property
    def native_value(self) -> str:
        service = self._state(f"{self._room}.ActiveService")
        if not isinstance(service, str) or not service:
            return _IDLE
        return self._names.get(service, service)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "service_id": self._state(f"{self._room}.ActiveService") or "",
            "active_services": self._state(f"{self._room}.ActiveServices") or "",
            "last_active_service": self._state(f"{self._room}.LastActiveService") or "",
        }


class SavantRoomAudioServiceSensor(SavantRoomActiveServiceSensor):
    """The room's separately tracked audio service (`<room>.ActiveAudioService`)."""

    _attr_name = "Active audio service"

    def __init__(self, hub: SavantHub, room: str, names: dict[str, str]) -> None:
        super().__init__(hub, room, names)
        self._attr_unique_id = f"{hub.uid}_room_audio_service_{room}"

    @property
    def native_value(self) -> str:
        service = self._state(f"{self._room}.ActiveAudioService")
        if not isinstance(service, str) or not service:
            return _IDLE
        return self._names.get(service, service)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"service_id": self._state(f"{self._room}.ActiveAudioService") or ""}


class SavantRoomVideoServiceSensor(SavantRoomActiveServiceSensor):
    """The room's video service (`<room>.ActiveVideoService`, PROTOCOL.md §5.4b).

    Pushed alongside ActiveService/ActiveAudioService; reads unknown until the
    room first pushes it.
    """

    _attr_name = "Active video service"

    def __init__(self, hub: SavantHub, room: str, names: dict[str, str]) -> None:
        super().__init__(hub, room, names)
        self._attr_unique_id = f"{hub.uid}_room_video_service_{room}"

    @property
    def native_value(self) -> str | None:
        service = self._state(f"{self._room}.ActiveVideoService")
        if service is None:
            return None
        if not isinstance(service, str) or not service:
            return _IDLE
        return self._names.get(service, service)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"service_id": self._state(f"{self._room}.ActiveVideoService") or ""}


class SavantRoomVolumeSensor(SavantEntity, SensorEntity):
    """The room amplifier's volume on Savant's raw 0–50 scale.

    `<room>.CurrentVolume` mirrors the room's amplifier and arrives as both
    str and int (PROTOCOL.md §5.4b); the raw value is reported unconverted so
    it can be corroborated against `service/request` SetVolume arguments.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Volume"

    def __init__(self, hub: SavantHub, room: str) -> None:
        super().__init__(hub, device_key=f"room:{room}", device_name=room, area=room)
        self._room = room
        self._attr_unique_id = f"{hub.uid}_room_volume_{room}"

    @property
    def native_value(self) -> float | None:
        return coerce_number(self._state(f"{self._room}.CurrentVolume"))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {"scale": "raw 0-50"}


@dataclass(frozen=True)
class RoomStateSensorSpec:
    """One captured `<room>.<attr>` key exposed as a diagnostic sensor."""

    attr: str
    name: str
    uid_key: str
    capability: str
    numeric: bool = False


# All attrs are the captured room surface (PROTOCOL.md §5.2 / const.ROOM_ATTRIBUTES);
# no key here may be introduced from anything but live observation.
ROOM_STATE_SENSORS: tuple[RoomStateSensorSpec, ...] = (
    RoomStateSensorSpec(
        "CurrentStreamingService", "Streaming service", "streaming", "hasAV"
    ),
    RoomStateSensorSpec("GroupVolume", "Group volume", "group_volume", "hasAV", numeric=True),
    RoomStateSensorSpec(
        "SleepTimerRemainingTime", "Sleep timer remaining", "sleep_remaining", "hasAV"
    ),
    RoomStateSensorSpec(
        "RoomNumberOfLightsOn", "Lights on count", "lights_on_count", "hasLights", numeric=True
    ),
    RoomStateSensorSpec(
        "BrightnessLevel", "Brightness level", "brightness", "hasLights", numeric=True
    ),
    RoomStateSensorSpec(
        "RoomNumberOfShadesOpen", "Shades open count", "shades_open_count", "hasShades",
        numeric=True,
    ),
    RoomStateSensorSpec("ShadeLevel", "Shade level", "shade_level", "hasShades", numeric=True),
)


class SavantRoomStateSensor(SavantEntity, SensorEntity):
    """A generic diagnostic sensor over one captured `<room>.<attr>` push."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: SavantHub, room: str, spec: RoomStateSensorSpec) -> None:
        super().__init__(hub, device_key=f"room:{room}", device_name=room, area=room)
        self._room = room
        self._spec = spec
        self._attr_name = spec.name
        self._attr_unique_id = f"{hub.uid}_room_{spec.uid_key}_{room}"
        if spec.numeric:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> object:
        value = self._state(f"{self._room}.{self._spec.attr}")
        if self._spec.numeric:
            return coerce_number(value)
        if isinstance(value, str):
            return value or None
        return value


class SavantEndpointStateSensor(SavantEntity, SensorEntity):
    """A diagnostic sensor over one device-level endpoint push.

    Keys are ``<component>.<logicalComponent>.<attr>`` (PROTOCOL.md §5.4b);
    attributes a device class does not report simply stay unknown.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        hub: SavantHub,
        component: str,
        logical: str,
        device_name: str,
        area: str,
        attr: str,
        name: str,
        uid_key: str,
        numeric: bool = False,
    ) -> None:
        super().__init__(
            hub,
            device_key=f"endpoint:{component}.{logical}",
            device_name=device_name,
            area=area,
        )
        self._key = f"{component}.{logical}.{attr}"
        self._numeric = numeric
        self._attr_name = name
        self._attr_unique_id = f"{hub.uid}_endpoint_{uid_key}_{component}.{logical}"

    @property
    def native_value(self) -> object:
        value = self._state(self._key)
        if self._numeric:
            return coerce_number(value)
        if isinstance(value, str):
            return value or None
        return value


# Device-level sensor specs: (attr, name, unique-id key, numeric).
ENDPOINT_STATE_SENSORS = (
    ("CurrentPowerStatus", "Power status", "power_status", False),
    ("CurrentInput", "Input", "input", False),
    ("CurrentVolume", "Volume", "volume", True),
)


def media_components(hub: SavantHub) -> dict[tuple[str, str], tuple[str, str]]:
    """Unique (component, logicalComponent) pairs -> (device name, area).

    A shared source appears in several rooms; only a single-room component gets
    a suggested area. A component with several logical components (e.g. a
    multi-zone audio device) carries the logical component in its name so the
    resulting devices stay distinguishable.
    """
    rooms: dict[tuple[str, str], set[str]] = {}
    logicals: dict[str, set[str]] = {}
    for device in hub.devices or []:
        if device.get("type") != DEVICE_TYPE_MEDIA_PLAYER:
            continue
        component = str(device.get("component") or "")
        logical = str(device.get("zone") or "")
        if not (component and logical):
            continue
        rooms.setdefault((component, logical), set()).add(str(device.get("room") or ""))
        logicals.setdefault(component, set()).add(logical)
    return {
        (component, logical): (
            component if len(logicals[component]) == 1 else f"{component} {logical}",
            next(iter(members)) if len(members) == 1 else "",
        )
        for (component, logical), members in rooms.items()
    }


def room_capabilities(hub: SavantHub) -> dict[str, dict[str, bool]]:
    """Per-room capability flags from imported ``room`` devices.

    Entries created before room import lack them; media-player rooms then get an
    AV-only fallback so the original AV diagnostics keep working.
    """
    capabilities: dict[str, dict[str, bool]] = {}
    for device in hub.devices or []:
        if device.get("type") != DEVICE_TYPE_ROOM:
            continue
        control = device.get("control")
        control = control if isinstance(control, dict) else {}
        flags = control.get("capabilities")
        if (room := str(device.get("room") or "")) and isinstance(flags, dict):
            capabilities[room] = {k: bool(v) for k, v in flags.items()}
    if capabilities:
        return capabilities
    rooms, _ = _media_rooms(hub)
    return {room: {"hasAV": True} for room in rooms}


def _has(flags: dict[str, bool], capability: str) -> bool:
    if capability == "hasShades":
        return flags.get("hasShades", False) or flags.get("hasVariableShades", False)
    return flags.get(capability, False)


def _media_rooms(hub: SavantHub) -> tuple[set[str], dict[str, str]]:
    """Rooms hosting at least one approved AV endpoint, and the ID->name map."""
    rooms: set[str] = set()
    names: dict[str, str] = {}
    for device in hub.devices or []:
        if device.get("type") != DEVICE_TYPE_MEDIA_PLAYER:
            continue
        control = device.get("control")
        control = control if isinstance(control, dict) else {}
        if room := str(device.get("room") or ""):
            rooms.add(room)
        if service_id := str(control.get("service_id") or ""):
            names[service_id] = str(device.get("name") or service_id)
    return rooms, names


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: SavantHub = hass.data[DOMAIN][entry.entry_id]
    _, names = _media_rooms(hub)
    entities: list[SensorEntity] = []
    for room, flags in sorted(room_capabilities(hub).items()):
        if _has(flags, "hasAV"):
            entities.append(SavantRoomActiveServiceSensor(hub, room, names))
            entities.append(SavantRoomAudioServiceSensor(hub, room, names))
            entities.append(SavantRoomVideoServiceSensor(hub, room, names))
            entities.append(SavantRoomVolumeSensor(hub, room))
        entities.extend(
            SavantRoomStateSensor(hub, room, spec)
            for spec in ROOM_STATE_SENSORS
            if _has(flags, spec.capability)
        )
    for (component, logical), (device_name, area) in sorted(media_components(hub).items()):
        entities.extend(
            SavantEndpointStateSensor(
                hub, component, logical, device_name, area, attr, name, uid_key, numeric
            )
            for attr, name, uid_key, numeric in ENDPOINT_STATE_SENSORS
        )
    if entities:
        hub.mark_created([e.unique_id for e in entities])
        async_add_entities(entities)
