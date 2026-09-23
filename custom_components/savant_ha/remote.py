"""Remote platform: expose every archive-declared request of an AV endpoint.

A media player can only represent the requests Home Assistant has features for;
the remote entity exposes the full declared surface (OSD cursor/select, Menu,
transport, power) via ``remote.send_command`` with the archive request names
(PROTOCOL.md §5.4/§5.4b). Commands are validated against the endpoint's own
declared request list, so a typo or an undeclared verb fails instead of being
sent blind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_TYPE_MEDIA_PLAYER,
    DOMAIN,
    VERB_POWER_OFF,
    VERB_POWER_ON,
)
from .entity import SavantEntity
from .hub import SavantHub

_DEFAULT_DELAY = 0.4  # seconds between repeated commands; HA remote default.


class SavantEndpointRemote(SavantEntity, RemoteEntity):
    """One remote per AV endpoint, sending its declared requests verbatim."""

    def __init__(
        self,
        hub: SavantHub,
        device: dict[str, object],
        component: str,
        logical_component: str,
    ) -> None:
        super().__init__(
            hub,
            device_key=f"media:{device['id']}",
            device_name=device["name"],
            area=device.get("area", ""),
        )
        self._component = component
        self._logical_component = logical_component
        self._room = str(device.get("room") or "")
        control = device.get("control")
        control = control if isinstance(control, dict) else {}
        self._variant_id = str(control.get("variant_id") or "1")
        self._service_id = str(control.get("service_id") or "")
        self._service_type = str(control.get("service_type") or "")
        self._requests = set(control.get("requests") or ())
        self._attr_unique_id = f"{hub.uid}_remote_{device['id']}"
        self._optimistic_on: bool | None = None

    @property
    def is_on(self) -> bool:
        services: set[str] = set()
        for attribute in ("ActiveService", "ActiveServices"):
            value = self._state(f"{self._room}.{attribute}")
            if isinstance(value, str):
                services.update(p.strip() for p in value.split(",") if p.strip())
        if self._service_id and self._service_id in services:
            return True
        if services:
            return False
        return bool(self._optimistic_on)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        # The valid inputs for remote.send_command on this endpoint.
        return {"commands": sorted(self._requests)}

    async def _request(self, request: str) -> None:
        await self._service_request(
            request,
            component=self._component,
            service_type=self._service_type,
            zone=self._room,
            logical_component=self._logical_component,
            variant_id=self._variant_id,
            include_request_id=True,
        )

    async def async_turn_on(self) -> None:
        if VERB_POWER_ON in self._requests:
            await self._request(VERB_POWER_ON)
            self._optimistic_on = True
            self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        if VERB_POWER_OFF in self._requests:
            await self._request(VERB_POWER_OFF)
            self._optimistic_on = False
            self.async_write_ha_state()

    async def async_send_command(self, command: Iterable[str], **kwargs: object) -> None:
        commands = [command] if isinstance(command, str) else list(command)
        unknown = [c for c in commands if c not in self._requests]
        if unknown:
            raise HomeAssistantError(
                f"Not declared by this Savant endpoint: {', '.join(unknown)}. "
                f"Valid commands: {', '.join(sorted(self._requests))}"
            )
        num_repeats = int(kwargs.get("num_repeats") or 1)
        delay = float(kwargs.get("delay_secs") or _DEFAULT_DELAY)
        first = True
        for _ in range(num_repeats):
            for cmd in commands:
                if not first:
                    await asyncio.sleep(delay)
                first = False
                await self._request(cmd)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: SavantHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SavantEndpointRemote] = []
    for device in hub.devices or []:
        if device.get("type") != DEVICE_TYPE_MEDIA_PLAYER:
            continue
        component = str(device.get("component") or "")
        logical = str(device.get("zone") or "")
        control = device.get("control")
        control = control if isinstance(control, dict) else {}
        # Only endpoints with a declared request list get a remote; legacy
        # state-bus-discovered zones have nothing to validate against.
        if component and logical and control.get("requests"):
            entities.append(SavantEndpointRemote(hub, device, component, logical))
    entities = [e for e in entities if not hub.is_created(e.unique_id)]
    if entities:
        hub.mark_created([e.unique_id for e in entities])
        async_add_entities(entities)
