import os
import yaml
from typing import Any, Dict
from loguru import logger
from contracts.errors import ConfigurationError


def load_config(config_path: str = "") -> Dict[str, Any]:
    """Loads configuration yaml file and returns it as a dict.

    Falls back to CONFIG_PATH environment variable if config_path is not specified.
    """
    if not config_path:
        config_path = os.getenv("CONFIG_PATH", "config/live.yaml")

    if not os.path.exists(config_path):
        # Check parent directory fallback for app runners running in nested folders
        fallback_path = os.path.join("..", "..", config_path)
        if os.path.exists(fallback_path):
            config_path = fallback_path
        else:
            raise ConfigurationError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
            logger.info(f"Loaded config from {config_path}")
            return config
    except Exception as e:
        raise ConfigurationError(f"Failed to parse config file '{config_path}': {e}")
