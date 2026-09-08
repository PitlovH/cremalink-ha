"""Config flow for the Cremalink integration."""
import logging
import os
import json
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from cremalink.devices import get_device_maps, load_device_map
from cremalink import Client

from .const import *

_LOGGER = logging.getLogger(__name__)


def get_available_maps(hass: HomeAssistant) -> list[str]:
    """Retrieve available device maps, including custom ones.

    Args:
        hass: The Home Assistant instance.

    Returns:
        A list of available device map identifiers.
    """
    try:
        # Get built-in maps from the library
        maps = list(get_device_maps())
    except Exception:
        maps = []

    # Check for custom maps in the configuration directory
    custom_dir = hass.config.path(CUSTOM_MAP_DIR)
    if os.path.exists(custom_dir):
        for f in os.listdir(custom_dir):
            if f.endswith(".json"):
                maps.append(f"custom:{f}")
    maps.sort()
    return maps


def get_map_data(hass: HomeAssistant, map_name: str) -> dict:
    """Retrieve data for a specific map."""
    if map_name.startswith("custom:"):
        filename = map_name.replace("custom:", "", 1)
        custom_dir = hass.config.path(CUSTOM_MAP_DIR)
        filepath = os.path.join(custom_dir, filename)
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    else:
        try:
            return load_device_map(map_name)
        except Exception:
            return {}

def authenticate_with_email(email: str, password: str) -> str:
    """Authenticate with Gigya/Ayla using email and password to get a refresh token (OIDC flow)."""
    import requests
    import base64
    from datetime import datetime
    import urllib.parse
    import json
    from cremalink.resources.api_config import load_api_config

    api_conf = load_api_config()
    gigya_api = api_conf.get("GIGYA", {})
    ayla_api = api_conf.get("AYLA", {})

    API_KEY = gigya_api.get("API_KEY")
    CLIENT_ID = gigya_api.get("CLIENT_ID")
    CLIENT_SECRET = gigya_api.get("CLIENT_SECRET")
    SDK_BUILD = gigya_api.get("SDK_BUILD", 16650)
    APP_ID = ayla_api.get("APP_ID")
    APP_SECRET = ayla_api.get("APP_SECRET")
    
    AUTHORIZATION_HEADER = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    BROWSER_USER_AGENT = "DeLonghiComfort/5.1.1"

    def get_query_param(url, param):
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(param, [None])[0]

    auth_response = requests.get(
        f"https://fidm.eu1.gigya.com/oidc/op/v1.0/{API_KEY}/authorize",
        headers={"User-Agent": BROWSER_USER_AGENT},
        params={"client_id": CLIENT_ID, "response_type": "code", "redirect_uri": "https://google.it",
                "scope": "openid email profile UID comfort en alexa", "nonce": str(int(datetime.now().timestamp()))},
        allow_redirects=False,
    )
    context = get_query_param(auth_response.headers["Location"], "context")

    gigya_session_response = requests.get(
        f"https://socialize.eu1.gigya.com/socialize.getIDs",
        headers={"User-Agent": BROWSER_USER_AGENT},
        params={"APIKey": API_KEY, "includeTicket": True, "pageURL": "https://aylaopenid.delonghigroup.com/",
                "sdk": "js_latest", "sdkBuild": SDK_BUILD, "format": "json"},
    ).json()
    ucid, gmid, gmid_ticket = gigya_session_response["ucid"], gigya_session_response["gmid"], gigya_session_response["gmidTicket"]

    login_response = requests.post(
        "https://accounts.eu1.gigya.com/accounts.login",
        headers={"User-Agent": BROWSER_USER_AGENT},
        data={"loginID": email, "password": password, "sessionExpiration": 7884009, "targetEnv": "jssdk",
              "include": "profile,data,emails,subscriptions,preferences", "includeUserInfo": True,
              "loginMode": "standard", "APIKey": API_KEY, "source": "showScreenSet", "sdk": "js_latest",
              "authMode": "cookie", "pageURL": "https://aylaopenid.delonghigroup.com/", "gmid": gmid, "ucid": ucid,
              "sdkBuild": SDK_BUILD, "format": "json"},
    ).json()
    
    if login_response.get("errorCode", 0) != 0:
        raise ValueError(f"Login failed: {login_response.get('errorMessage')}")
        
    login_token = login_response["sessionInfo"]["login_token"]

    user_info_response = requests.post(
        "https://socialize.eu1.gigya.com/socialize.getUserInfo",
        headers={"User-Agent": BROWSER_USER_AGENT},
        data={"enabledProviders": "*", "APIKey": API_KEY, "sdk": "js_latest", "login_token": login_token,
              "authMode": "cookie", "pageURL": "https://aylaopenid.delonghigroup.com/", "gmid": gmid, "ucid": ucid,
              "sdkBuild": SDK_BUILD, "format": "json"},
    ).json()
    user_uid, user_uid_signature, user_signature_timestamp = user_info_response["UID"], user_info_response["UIDSignature"], user_info_response["signatureTimestamp"]

    consent_response = requests.get(
        f"https://aylaopenid.delonghigroup.com/OIDCConsentPage.php",
        headers={"User-Agent": BROWSER_USER_AGENT},
        params={"context": context, "clientID": CLIENT_ID, "scope": "openid+email+profile+UID+comfort+en+alexa",
                "UID": user_uid, "UIDSignature": user_uid_signature, "signatureTimestamp": user_signature_timestamp},
    ).text
    signature = consent_response.split("const consentObj2Sig = '")[1].split("';")[0]

    auth_continue_response = requests.get(
        f"https://fidm.eu1.gigya.com/oidc/op/v1.0/{API_KEY}/authorize/continue",
        headers={"User-Agent": BROWSER_USER_AGENT},
        params={"context": context, "login_token": login_token, "consent": json.dumps(
            {"scope": "openid email profile UID comfort en alexa", "clientID": CLIENT_ID, "context": context,
             "UID": user_uid, "consent": True}, separators=(",", ":")), "sig": signature, "gmidTicket": gmid_ticket},
        allow_redirects=False,
    )
    code = get_query_param(auth_continue_response.headers["Location"], "code")

    idp_token_response = requests.post(
        f"https://fidm.eu1.gigya.com/oidc/op/v1.0/{API_KEY}/token",
        headers={"User-Agent": BROWSER_USER_AGENT, "Authorization": AUTHORIZATION_HEADER,
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"code": code, "grant_type": "authorization_code", "redirect_uri": "https://google.it"},
    ).json()
    idp_token = idp_token_response["access_token"]

    ayla_token_response = requests.post(
        "https://user-field-eu.aylanetworks.com/api/v1/token_sign_in",
        headers={"User-Agent": BROWSER_USER_AGENT},
        data={"app_id": APP_ID, "app_secret": APP_SECRET, "token": idp_token},
    ).json()
    
    if "refresh_token" not in ayla_token_response:
        raise ValueError(f"Failed to get Ayla refresh token: {ayla_token_response}")

    return ayla_token_response["refresh_token"]


class CremalinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cremalink."""

    VERSION = 1
    _addon_url = DEFAULT_ADDON_URL
    _temp_token_file: str | None = None
    _discovered_devices: list[str] = []
    _selected_map: str | None = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step (Model Selection)."""
        errors = {}
        if user_input is not None:
            self._selected_map = user_input[CONF_DEVICE_MAP]

            # Check support
            map_data = await self.hass.async_add_executor_job(
                get_map_data, self.hass, self._selected_map
            )
            support = map_data.get("support", {})
            local_support = support.get("local", False)
            cloud_support = support.get("cloud", False)

            if local_support and cloud_support:
                return self.async_show_menu(
                    step_id="choose_connection",
                    menu_options={
                        "local": "Local Network (Add-on) [recommended]",
                        "cloud_auth": "Cloud (Ayla Networks)",
                    }
                )
            elif local_support:
                return await self.async_step_local()
            elif cloud_support:
                return await self.async_step_cloud_auth()
            else:
                errors["base"] = "no_support"

        maps = await self.hass.async_add_executor_job(get_available_maps, self.hass)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_DEVICE_MAP): vol.In(maps)
            }),
            errors=errors
        )

    async def async_step_choose_connection(self, user_input=None):
        """Handle the connection choice step."""
        if user_input == "local":
            return await self.async_step_local()
        elif user_input == "cloud_auth":
            return await self.async_step_cloud_auth()
        return self.async_abort(reason="unknown_choice")

    async def async_step_local(self, user_input=None):
        """Handle the local connection step.

        Args:
            user_input: Input data from the user.

        Returns:
            The next step in the flow.
        """
        errors = {}
        if user_input is not None:
            self._addon_url = user_input[CONF_ADDON_URL]
            try:
                import requests

                def _check():
                    # Check health endpoint of the addon
                    return requests.get(f"{self._addon_url.rstrip('/')}/health", timeout=5)

                resp = await self.hass.async_add_executor_job(_check)
                if resp.status_code == 200:
                    return await self.async_step_device()
            except Exception:
                pass

            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema({vol.Required(CONF_ADDON_URL, default=DEFAULT_ADDON_URL): str}),
            errors=errors
        )

    async def async_step_device(self, user_input=None):
        """Handle the local device configuration step.

        Args:
            user_input: Input data from the user
        Returns:
            The created config entry or the form to show.
        """
        errors = {}
        maps = await self.hass.async_add_executor_job(get_available_maps, self.hass)
        
        if user_input:
            user_input[CONF_ADDON_URL] = self._addon_url
            user_input[CONF_CONNECTION_TYPE] = CONNECTION_LOCAL

            if self._selected_map and CONF_DEVICE_MAP not in user_input:
                user_input[CONF_DEVICE_MAP] = self._selected_map

            await self.async_set_unique_id(user_input[CONF_DSN])
            self._abort_if_unique_id_configured()

            return self.async_create_entry(title=f"{user_input[DEVICE_NAME]}", data=user_input)

        schema = {
            vol.Required(DEVICE_NAME): str,
            vol.Required(CONF_DSN): str,
            vol.Required(CONF_LAN_KEY): str,
            vol.Required(CONF_DEVICE_IP): str,
        }
        if not self._selected_map:
            schema[vol.Required(CONF_DEVICE_MAP)] = vol.In(maps) if maps else str

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_cloud_auth(self, user_input=None):
        """Handle the cloud authentication step.

        Args:
            user_input: Input data from the user.

        Returns:
            The next step in the flow.
        """
        errors = {}
        if user_input is not None:
            refresh_token = user_input.get(CONF_REFRESH_TOKEN)
            email = user_input.get(CONF_EMAIL)
            password = user_input.get(CONF_PASSWORD)

            if email and password:
                try:
                    refresh_token = await self.hass.async_add_executor_job(
                        authenticate_with_email, email, password
                    )
                except Exception as e:
                    _LOGGER.error("Email authentication failed: %s", e)
                    errors["base"] = "auth_failed"

            if not refresh_token and not errors:
                errors["base"] = "auth_failed"

            if not errors and refresh_token:
                # Ensure token directory exists
                token_dir = self.hass.config.path(TOKEN_DIR)
                os.makedirs(token_dir, exist_ok=True)

                # Create a temporary token file
                temp_file = os.path.join(token_dir, "temp_token.json")

                try:
                    def _auth_and_fetch():
                        with open(temp_file, "w") as f:
                            json.dump({"refresh_token": refresh_token}, f)

                        client = Client(temp_file)
                        return client.get_devices()

                    self._discovered_devices = await self.hass.async_add_executor_job(_auth_and_fetch)
                    self._temp_token_file = temp_file

                    if not self._discovered_devices:
                        errors["base"] = "no_devices"
                    else:
                        return await self.async_step_cloud_device()

                except Exception as e:
                    _LOGGER.error("Authentication failed: %s", e)
                    errors["base"] = "auth_failed"
                    # Clean up if failed
                    if os.path.exists(temp_file):
                        os.remove(temp_file)

        return self.async_show_form(
            step_id="cloud_auth",
            data_schema=vol.Schema({
                vol.Optional(CONF_EMAIL): str,
                vol.Optional(CONF_PASSWORD): str,
                vol.Optional(CONF_REFRESH_TOKEN): str,
            }),
            errors=errors,
        )

    async def async_step_cloud_device(self, user_input=None):
        """Handle the cloud device selection step.

        Args:
            user_input: Input data from the user.

        Returns:
            The created config entry or the form to show.
        """
        errors = {}
        maps = await self.hass.async_add_executor_job(get_available_maps, self.hass)

        if user_input:
            dsn = user_input[CONF_DSN]

            # Check if already configured
            await self.async_set_unique_id(dsn)
            self._abort_if_unique_id_configured()

            # Move temp token file to permanent location
            token_dir = self.hass.config.path(TOKEN_DIR)
            final_token_path = os.path.join(token_dir, f"{dsn}.json")

            if self._temp_token_file and os.path.exists(self._temp_token_file):
                os.rename(self._temp_token_file, final_token_path)

            if self._selected_map and CONF_DEVICE_MAP not in user_input:
                user_input[CONF_DEVICE_MAP] = self._selected_map

            data = {
                CONF_CONNECTION_TYPE: CONNECTION_CLOUD,
                DEVICE_NAME: dsn,  # Default name, user can change later in HA entity settings
                CONF_DSN: dsn,
                CONF_DEVICE_MAP: user_input[CONF_DEVICE_MAP],
                CONF_TOKEN_FILE: final_token_path
            }

            return self.async_create_entry(title=dsn, data=data)

        schema = {
            vol.Required(DEVICE_NAME): str,
            vol.Required(CONF_DSN): vol.In(self._discovered_devices),
        }
        if not self._selected_map:
            schema[vol.Required(CONF_DEVICE_MAP)] = vol.In(maps) if maps else str

        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema(schema),
            errors=errors,
        )
