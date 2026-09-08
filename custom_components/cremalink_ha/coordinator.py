"""Data update coordinator for the Cremalink integration."""
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from cremalink.domain.device import Device

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL_FAST = timedelta(seconds=1)
SCAN_INTERVAL_SLOW = timedelta(seconds=30)

class CremalinkCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Cremalink device."""

    def __init__(self, hass: HomeAssistant, device: Device, connection_type: str, dsn: str, map_path: str, token_file: str = None):
        """Initialize the coordinator.

        Args:
            hass: The Home Assistant instance.
            device: The Cremalink device instance.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # Poll the device every second for updates
            update_interval=SCAN_INTERVAL_FAST,
        )
        self.device = device
        self.connection_type = connection_type
        self.dsn = dsn
        self.map_path = map_path
        self.token_file = token_file

    async def _async_update_data(self):
        """Fetch data from the device.

        Returns:
            The monitoring data from the device.

        Raises:
            UpdateFailed: If there is an error communicating with the device.
        """
        import requests
        from cremalink import Client
        from homeassistant.exceptions import ConfigEntryAuthFailed
        
        try:
            data = await self.hass.async_add_executor_job(self.device.get_monitor)

            if data and hasattr(data, 'parsed') and isinstance(data.parsed, dict):
                status = data.parsed.get("status")
                if status == 0:  # if in standby, poll slowly
                    self.update_interval = SCAN_INTERVAL_SLOW
                elif status is not None:
                    self.update_interval = SCAN_INTERVAL_FAST

            return data
        except requests.exceptions.HTTPError as err:
            if getattr(err, "response", None) and err.response.status_code == 401 and self.connection_type == "cloud":
                _LOGGER.info("Access token expired, attempting to refresh...")
                try:
                    def _refresh_device():
                        client = Client(self.token_file)
                        return client.get_device(self.dsn, self.map_path)
                    
                    self.device = await self.hass.async_add_executor_job(_refresh_device)
                    # Retry once
                    data = await self.hass.async_add_executor_job(self.device.get_monitor)
                    return data
                except Exception as refresh_err:
                    raise ConfigEntryAuthFailed(f"Token refresh failed: {refresh_err}") from refresh_err
            raise UpdateFailed(f"Error communicating with device: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with device: {err}") from err
