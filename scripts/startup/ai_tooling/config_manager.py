"""Loads and provides access to addon configuration from config.json."""

import json
from pathlib import Path
from typing import Dict, Any, Optional

from .logging_utils import log

CONFIG_FILENAME = "config.json"
_config_data: Optional[Dict[str, Any]] = None
_active_config: Optional[Dict[str, Any]] = None


def _get_config_dir() -> Path:
    """Gets the directory where config.json should reside."""
    # Assuming it's in the same directory as this script
    # Alternatively, use bpy.utils.user_resource like cache/auth tokens
    script_dir = Path(__file__).parent
    return script_dir


def _load_config():
    """Loads the config.json file and determines the active environment."""
    global _config_data, _active_config
    if _config_data is not None:
        return  # Already loaded

    config_file = _get_config_dir() / CONFIG_FILENAME
    log(f"Attempting to load configuration from: {config_file}", "INFO")

    if not config_file.exists():
        log(f"Configuration file {config_file} not found. Using default fallback (if any).", "ERROR")
        _config_data = {}  # Indicate loading failed / no file
        _active_config = {}  # No active config possible
        # Consider raising an error or providing hardcoded minimal defaults
        return

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            _config_data = json.load(f)

        active_env_name = _config_data.get("active_environment", "prod")  # Default to prod
        log(f"Active environment set to: '{active_env_name}'", "INFO")

        environments = _config_data.get("environments", {})
        if active_env_name in environments:
            _active_config = environments[active_env_name]
            log(f"Loaded configuration for environment: '{active_env_name}'", "INFO")
        else:
            log(f"Active environment '{active_env_name}' not found in config environments. Using empty config.", "ERROR")
            _active_config = {}

    except json.JSONDecodeError as e:
        log(f"Error decoding JSON from {config_file}: {e}. Using empty config.", "ERROR")
        _config_data = {}  # Indicate loading failed
        _active_config = {}
    except IOError as e:
        log(f"Error reading config file {config_file}: {e}. Using empty config.", "ERROR")
        _config_data = {}
        _active_config = {}
    except Exception as e:
        log(f"Unexpected error loading configuration: {e}", "ERROR")
        _config_data = {}
        _active_config = {}

# --- Getter Functions --- #


def get_active_config() -> Dict[str, Any]:
    """Returns the configuration dictionary for the active environment."""
    if _active_config is None:
        _load_config()
    # Return a copy to prevent external modification?
    # For simplicity now, return direct reference.
    return _active_config or {}


def get_config_value(key: str, default: Any = None) -> Any:
    """Gets a specific value from the active configuration."""
    return get_active_config().get(key, default)


def get_api_base_url() -> str:
    """Gets the API base URL for the active environment."""
    # Provide a fallback default? Or rely on caller handling None/empty?
    return get_active_config().get("api_base_url", "")


def get_cognito_config() -> Dict[str, Any]:
    """Gets the Cognito configuration dictionary."""
    return get_active_config().get("cognito", {})


def get_auth_token_filename() -> str:
    """Gets the filename for storing authentication tokens."""
    return get_active_config().get("auth_token_filename", "auth_tokens.json")  # Default fallback


def get_cache_filename() -> str:
    """Gets the filename for the cache file."""
    return get_active_config().get("cache_filename", "ai_copilot_cache.json")  # Default fallback


def get_cache_max_history() -> int:
    """Gets the maximum number of history items per thread in the cache."""
    return get_active_config().get("cache_max_history", 200)  # Default fallback


# Ensure config is loaded when module is imported
_load_config()
