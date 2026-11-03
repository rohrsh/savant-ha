"""Binary sensor platform: integration health diagnostics."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control import coerce_number
from .entity import SavantEntity
from .hub import SavantHub
from .sensor import _has, media_components, room_capabilities


def _coerce_bool(value: object) -> bool | None:
    """Parse a state-bus boolean: bool, 0/1, numeric string, or "ON"/"OFF"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.upper() == "ON":
            return True
        if value.upper() == "OFF":
            return False
    if (number := coerce_number(value)) is not None:
        return bool(number)
    return None


class SavantConnectivitySensor(SavantEntity, BinarySensorEntity):
    """Whether the host connection is up and authenticated.

    Driven by the client's connect/auth status (the hub re-renders on every
    ``on_status`` change), not by any host state key.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Connected"

    def __init__(self, hub: SavantHub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.uid}_connectivity"

    @property
    def available(self) -> bool:
        # The base class marks entities unavailable while disconnected; this sensor
        # must stay available so it can report the disconnected state itself.
        return True

    @property
    def is_on(self) -> bool:
        return self.hub.client.authorized


class SavantRoomStateBinarySensor(SavantEntity, BinarySensorEntity):
    """A diagnostic on/off view of one captured `<room>.<attr>` push.

    Values arrive as bool, int, or numeric string (PROTOCOL.md §5.2); anything
    unparseable reads as unknown rather than off.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: SavantHub, room: str, attr: str, name: str, uid_key: str) -> None:
        super().__init__(hub, device_key=f"room:{room}", device_name=room, area=room)
        self._room = room
        self._room_attr = attr
        self._attr_name = name
        self._attr_unique_id = f"{hub.uid}_room_{uid_key}_{room}"

    @property
    def is_on(self) -> bool | None:
        return _coerce_bool(self._state(f"{self._room}.{self._room_attr}"))


class SavantEndpointBinarySensor(SavantEntity, BinarySensorEntity):
    """A diagnostic on/off view of one device-level endpoint push (PROTOCOL.md §5.4b)."""

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
    ) -> None:
        super().__init__(
            hub,
            device_key=f"endpoint:{component}.{logical}",
            device_name=device_name,
            area=area,
        )
        self._key = f"{component}.{logical}.{attr}"
        self._attr_name = name
        self._attr_unique_id = f"{hub.uid}_endpoint_{uid_key}_{component}.{logical}"

    @property
    def is_on(self) -> bool | None:
        return _coerce_bool(self._state(self._key))


# (attr, name, unique-id key, RoomCapabilities gate); attrs are the captured room
# surface (PROTOCOL.md §5.2 / §5.4b / const.ROOM_ATTRIBUTES).
_ROOM_BINARY_SENSORS = (
    ("IsMuted", "Muted", "muted", "hasAV"),
    ("ZoneIsActive", "Zone active", "zone_active", "hasAV"),
    ("ZoneHasAudio", "Zone has audio", "zone_has_audio", "hasAV"),
    ("ZoneHasVideo", "Zone has video", "zone_has_video", "hasAV"),
    ("SleepTimerActive", "Sleep timer", "sleep_timer", "hasAV"),
    ("RoomLightsAreOn", "Lights on", "lights_on", "hasLights"),
    ("RoomFansAreOn", "Fans on", "fans_on", "hasFans"),
    ("RoomShadesAreOpen", "Shades open", "shades_open", "hasShades"),
)

# Device-level binary specs: (attr, name, unique-id key).
_ENDPOINT_BINARY_SENSORS = (
    ("IsPowered", "Powered", "powered"),
    ("CurrentMuteStatus", "Muted", "muted"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: SavantHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [SavantConnectivitySensor(hub)]
    for room, flags in sorted(room_capabilities(hub).items()):
        entities.extend(
            SavantRoomStateBinarySensor(hub, room, attr, name, uid_key)
            for attr, name, uid_key, capability in _ROOM_BINARY_SENSORS
            if _has(flags, capability)
        )
    for (component, logical), (device_name, area) in sorted(media_components(hub).items()):
        entities.extend(
            SavantEndpointBinarySensor(
                hub, component, logical, device_name, area, attr, name, uid_key
            )
            for attr, name, uid_key in _ENDPOINT_BINARY_SENSORS
        )
    hub.mark_created([e.unique_id for e in entities])
    async_add_entities(entities)
