import importlib
import importlib.util
import inspect
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class Plugin:
    def __init__(
        self,
        name: str,
        version: str,
        description: str,
        module_path: str,
        enabled: bool = True,
        functions: Optional[Dict[str, Callable]] = None,
        functions_meta: Optional[Dict[str, dict]] = None,
    ):
        self.name = name
        self.version = version
        self.description = description
        self.module_path = module_path
        self.enabled = enabled
        self.functions = functions or {}
        self.functions_meta = functions_meta or {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "module_path": self.module_path,
            "enabled": self.enabled,
            "functions": list(self.functions.keys()),
        }


class PluginLoader:
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._plugins: dict[str, Plugin] = {}

    def load_all(self) -> List[Plugin]:
        self._plugins.clear()
        for f in self.plugins_dir.glob("*.py"):
            if f.name.startswith("_"):
                continue
            plugin = self._load_plugin_file(f)
            if plugin:
                self._plugins[plugin.name] = plugin
        return self.list()

    def _load_plugin_file(self, path: Path) -> Optional[Plugin]:
        try:
            module_name = path.stem
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            name = getattr(module, "PLUGIN_NAME", module_name)
            version = getattr(module, "PLUGIN_VERSION", "0.1.0")
            description = getattr(module, "PLUGIN_DESCRIPTION", "")

            functions: Dict[str, Callable] = {}
            functions_meta: Dict[str, dict] = {}
            for attr_name in dir(module):
                if attr_name.startswith("tool_"):
                    func = getattr(module, attr_name)
                    if callable(func):
                        sig = inspect.signature(func)
                        params = [
                            {
                                "name": p.name,
                                "annotation": str(p.annotation)
                                if p.annotation is not inspect.Parameter.empty
                                else "str",
                                "default": None
                                if p.default is inspect.Parameter.empty
                                else p.default,
                            }
                            for p in sig.parameters.values()
                            if p.name != "self"
                        ]
                        doc = inspect.getdoc(func) or ""
                        functions[attr_name] = func
                        functions_meta[attr_name] = {"params": params, "description": doc}

            enabled_file = self.plugins_dir / f"{module_name}.enabled"
            enabled = enabled_file.exists()

            return Plugin(
                name=name,
                version=version,
                description=description,
                module_path=str(path),
                enabled=enabled,
                functions=functions,
                functions_meta=functions_meta,
            )
        except Exception as e:
            logger.warning("Failed to load plugin %s: %s", path.name, e)
            return None

    def get(self, name: str) -> Optional[Plugin]:
        return self._plugins.get(name)

    def list(self) -> List[Plugin]:
        return list(self._plugins.values())

    def toggle(self, name: str, enabled: bool) -> bool:
        plugin = self._plugins.get(name)
        if not plugin:
            return False
        plugin.enabled = enabled
        enabled_file = self.plugins_dir / f"{Path(plugin.module_path).stem}.enabled"
        if enabled:
            enabled_file.touch()
        else:
            enabled_file.unlink(missing_ok=True)
        return True

    def get_enabled_plugins(self) -> List[Plugin]:
        return [p for p in self._plugins.values() if p.enabled]

    def call_function(self, plugin_name: str, func_name: str, **kwargs) -> Any:
        plugin = self._plugins.get(plugin_name)
        if not plugin or not plugin.enabled:
            raise ValueError(f"Plugin '{plugin_name}' not found or disabled")
        func = plugin.functions.get(func_name)
        if not func:
            raise ValueError(
                f"Function '{func_name}' not found in plugin '{plugin_name}'"
            )
        return func(**kwargs)
