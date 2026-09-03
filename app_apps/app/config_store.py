"""One JSON file that makes every configuration field outlive the session.

The panels edit long-lived configuration objects -- the analysis window and the loop
gains, the soak's duration and output folder, the XCORR scan bounds, which services come
up. Every one of those was reset to a literal in a module registration on each launch, so
an operator who spent a morning tuning the loop retyped it after lunch. That is the whole
of what this fixes.

The objects are NOT replaced on load. Modules construct their configuration and hand the
same instance to a handle, a service and a view model; a store that returned a fresh copy
would leave those holding the old one. So :meth:`ConfigStore.bind` writes the saved
values *into* the object that was passed to it and gives it straight back, which means it
can be wrapped around an existing constructor call without moving anything.

Saving is by re-reading the bound objects, not by being told about edits. Nothing has to
report a change, no field needs a signal, and a knob added to a dataclass later is
persisted the day it is added with no further work -- which is the only version of "all
config fields" that stays true after this commit.

Failure here is never fatal. A corrupt file, a field whose type stopped being
serializable, a read-only home directory: each is logged and skipped, and the application
comes up on the defaults it would have used anyway. Configuration is a convenience; the
instrument has to start.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from base_core.framework.serialization.serde import PrimitiveSerde
from base_core.framework.serialization.serialization import _convert_field, to_primitive

log = logging.getLogger(__name__)

#: Environment variable naming the configuration file, so a test or a second instance can
#: be pointed somewhere harmless. Matches the LOG_DIR_ENV convention next door.
CONFIG_FILE_ENV = "MILNERLAB_CONFIG_FILE"

T = TypeVar("T")


def default_config_file() -> Path:
    """``$MILNERLAB_CONFIG_FILE`` or ``~/.milnerlab/app_apps.json``."""
    env = os.environ.get(CONFIG_FILE_ENV)
    return Path(env) if env else Path.home() / ".milnerlab" / "app_apps.json"


def _dump_value(value: Any) -> Any:
    # Path is not a primitive and base_core has no rule for it, but out_dir is exactly
    # the kind of field this store exists for -- a folder the operator picked once.
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return to_primitive(value)


def _load_value(hint: Any, value: Any) -> Any:
    if hint is Path:
        return Path(value)
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint(value)

    # Range[Length] and friends. base_core's generic reader calls Range.from_primitive,
    # which reads its own fields against a bare TypeVar and so hands back the two raw
    # floats -- an analysis window that comes home as 7.95e-07 instead of a Length. The
    # element type is right there in the annotation, so use it.
    origin, args = get_origin(hint), get_args(hint)
    if (origin is not None and isinstance(origin, type) and issubclass(origin, PrimitiveSerde)
            and len(args) == 1 and isinstance(args[0], type)
            and issubclass(args[0], PrimitiveSerde) and isinstance(value, dict)):
        try:
            return origin(**{f.name: args[0].from_primitive(value[f.name])
                             for f in fields(origin) if f.name in value})
        except Exception:
            pass  # fall through to the generic reader, which at least returns something

    return _convert_field(hint, value)


class ConfigStore:
    """Load-once, save-often JSON store for the application's configuration objects.

    Keys are chosen by the caller and written into the file as-is, so they are part of
    the file format: renaming one silently drops what the operator had set under the old
    name. Prefer to keep them.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_config_file()
        self._bound: dict[str, Any] = {}
        self._written: str | None = None
        self._data = self._read()

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    def of(cls, container: Any) -> "ConfigStore":
        """The container's store, registering one if nothing has yet.

        Modules bind their configuration through this rather than ``container.get``:
        module registration order is decided by ``requires``, and the headless tools
        bootstrap arbitrary subsets of the modules, so no single module can be relied on
        to have put the store in place first. Whoever asks first creates it.
        """
        store = container.try_get(cls)
        if store is None:
            store = cls()
            container.register_instance(cls, store)
        return store

    # -- loading ----------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except Exception:
            # Truncated by a power cut, hand-edited into invalid JSON: say so loudly and
            # start from the defaults rather than refusing to launch.
            log.exception("config: could not read %s; starting from defaults", self._path)
            return {}
        if not isinstance(data, dict):
            log.warning("config: %s is not an object; ignoring it", self._path)
            return {}
        return data

    def bind(self, key: str, obj: T) -> T:
        """Apply anything saved under ``key`` to ``obj``, in place, and keep watching it.

        Field by field: an unknown name, a field whose type no longer converts, or a
        value that has since become invalid costs that one field and nothing else. A
        config that gained a knob since the file was written keeps the new default for it
        -- absent is absent, never zero.
        """
        self._bound[key] = obj
        saved = self._data.get(key)
        if isinstance(saved, dict) and is_dataclass(obj):
            hints = self._hints(type(obj))
            for f in fields(obj):
                if f.name not in saved:
                    continue
                try:
                    setattr(obj, f.name, _load_value(hints.get(f.name, Any), saved[f.name]))
                except Exception:
                    log.warning("config: ignoring saved %s.%s", key, f.name, exc_info=True)
        return obj

    def build(self, key: str, default: T) -> T:
        """Same as :meth:`bind` for a *frozen* dataclass, which cannot be written into.

        The saved fields are merged over the defaults and a new instance is constructed,
        so the caller must use the return value. Frozen configs are immutable by design,
        which also means the store can only ever capture the value they were built with
        -- good enough for ``ServiceConfig``, which nothing edits at runtime.
        """
        cls = type(default)
        saved = self._data.get(key)
        if isinstance(saved, dict) and is_dataclass(cls):
            hints = self._hints(cls)
            kwargs: dict[str, Any] = {}
            for f in fields(cls):
                if f.name not in saved:
                    continue
                try:
                    kwargs[f.name] = _load_value(hints.get(f.name, Any), saved[f.name])
                except Exception:
                    log.warning("config: ignoring saved %s.%s", key, f.name, exc_info=True)
            if kwargs:
                try:
                    default = cls(**{**self._as_dict(default), **kwargs})
                except Exception:
                    log.exception("config: could not rebuild %s; using defaults", key)
        self._bound[key] = default
        return default

    @staticmethod
    def _as_dict(obj: Any) -> dict[str, Any]:
        return {f.name: getattr(obj, f.name) for f in fields(obj)}

    @staticmethod
    def _hints(cls: type) -> dict[str, Any]:
        try:
            return get_type_hints(cls)
        except Exception:
            # A forward reference that cannot be resolved from this module. The fields
            # still round-trip through the fallback branch of _convert_field.
            return {}

    # -- saving -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Current value of every bound object, as primitives.

        Keys in the file that nothing bound this session are carried through untouched,
        so running a tool that registers half the modules does not throw away the panels'
        settings.
        """
        out: dict[str, Any] = dict(self._data)
        for key, obj in self._bound.items():
            if not is_dataclass(obj):
                continue
            row: dict[str, Any] = {}
            for f in fields(obj):
                try:
                    row[f.name] = _dump_value(getattr(obj, f.name))
                except Exception:
                    # A field that cannot be written (a numpy template, a live handle) is
                    # dropped from the file, not from the object.
                    log.debug("config: %s.%s is not serializable", key, f.name)
            out[key] = row
        return out

    def save(self) -> bool:
        """Write the file if anything changed. Returns whether it was written.

        Cheap enough to call on a timer: the comparison is against the text last written,
        so an idle application does no disk I/O at all. Written to a sibling temp file and
        replaced, so an interrupted save cannot leave a half-file that the next launch
        would have to discard.
        """
        try:
            text = json.dumps(self.snapshot(), indent=2, sort_keys=True)
        except Exception:
            log.exception("config: could not serialize; not saving")
            return False
        if text == self._written:
            return False
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self._path)
        except Exception:
            log.exception("config: could not write %s", self._path)
            return False
        self._written = text
        return True
