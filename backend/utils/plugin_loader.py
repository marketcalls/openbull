import json
import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_broker_plugins: dict = {}


def load_all_plugins() -> dict:
    global _broker_plugins
    broker_dir = Path(__file__).parent.parent / "broker"
    for plugin_path in broker_dir.glob("*/plugin.json"):
        broker_name = plugin_path.parent.name
        try:
            with open(plugin_path, encoding="utf-8") as f:
                data = json.load(f)
            _broker_plugins[broker_name] = data
            logger.info("Loaded broker plugin: %s", broker_name)
        except Exception as e:
            logger.error("Failed to load plugin %s: %s", broker_name, e)
    return _broker_plugins


def get_plugin_info(broker_name: str) -> dict | None:
    return _broker_plugins.get(broker_name)


def get_all_plugins() -> dict:
    return _broker_plugins


def get_broker_module(broker_name: str, module_type: str):
    """Dynamically import a broker sub-module.
    module_type: 'auth_api', 'order_api', 'funds', 'data'
    """
    module_path = f"backend.broker.{broker_name}.api.{module_type}"
    return importlib.import_module(module_path)
