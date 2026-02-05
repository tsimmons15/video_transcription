import logging
import logging.handlers
import os
import weakref
from threading import Lock
from typing import Optional


class ConsoleToggleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return bool(getattr(record, "console", False))


class Logging:

    _instances = weakref.WeakValueDictionary()
    _lock = Lock()

    # Global defaults
    _log_dir: str = "log"
    _when: str = "midnight"
    _backupCount: int = 5
    _level: int = logging.INFO

    _CONSOLE_HANDLER_NAME = "console_handler"
    _FILE_HANDLER_NAME = "file_handler"

    def __new__(cls, name: str = "app", *args, **kwargs):
        with cls._lock:
            inst = cls._instances.get(name)
            if inst is None:
                inst = super().__new__(cls)
                cls._instances[name] = inst  # weakly held
        return inst

    def __init__(
        self,
        name: str = "app",
        *,
        level: Optional[int] = None,
        when: Optional[str] = None,
        backupCount: Optional[int] = None,
    ):
        # Don't re-init an existing instance
        if getattr(self, "_initialized", False):
            return

        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.propagate = False

        # Apply defaults / overrides
        self._level_local = level if level is not None else self.__class__._level
        self._when_local = when if when is not None else self.__class__._when
        self._backupCount_local = backupCount if backupCount is not None else self.__class__._backupCount

        self.logger.setLevel(self._level_local)

        self.formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

        self._ensure_console_handler()
        self._ensure_file_handler()

        self._initialized = True

    # -------------------------
    # Global controls
    # -------------------------

    @classmethod
    def set_log_dir(cls, log_dir, *, create = True):
        if not log_dir:
            raise ValueError("log_dir must be a non-empty value")

        if create:
            os.makedirs(log_dir, exist_ok=True)

        with cls._lock:
            cls._log_dir = log_dir

            # Important: snapshot values because WeakValueDictionary can change during iteration
            live_instances = list(cls._instances.values())

        # Rewire outside the class lock to avoid holding it during I/O/handler operations
        for inst in live_instances:
            inst._replace_file_handler()

    @classmethod
    def set_defaults(
        cls,
        *,
        level = None,
        when = None,
        backupCount = None,
    ):
        with cls._lock:
            if level is not None:
                cls._level = level
            if when is not None:
                cls._when = when
            if backupCount is not None:
                cls._backupCount = backupCount

            live_instances = list(cls._instances.values())

        for inst in live_instances:
            if level is not None:
                inst.logger.setLevel(level)
                inst._level_local = level
            if when is not None:
                inst._when_local = when
            if backupCount is not None:
                inst._backupCount_local = backupCount

            if when is not None or backupCount is not None:
                inst._replace_file_handler()

    @classmethod
    def get(cls, name = "app", **kwargs):
        return cls(name=name, **kwargs)

    # -------------------------
    # Logging methods
    # -------------------------

    def write(self, message, log_level=logging.INFO, *args, console = False, **kwargs):
        extra = kwargs.pop("extra", {})
        extra["console"] = console

        self.logger.log(
            log_level,
            message,
            *args,
            extra=extra,
            **kwargs,
        )

    def error(self, message, *args, console = False, exc_info = False, **kwargs):
        extra = kwargs.pop("extra", {})
        extra["console"] = console

        self.logger.error(
            message,
            *args,
            exc_info=exc_info,
            extra=extra,
            **kwargs,
        )

    def info(self, message, *args, console = False, **kwargs):
        self.write(message, logging.INFO, *args, console=console, **kwargs)

    def warning(self, message, *args, console = False, **kwargs):
        self.write(message, logging.WARNING, *args, console=console, **kwargs)

    def debug(self, message, *args, console = False, **kwargs):
        self.write(message, logging.DEBUG, *args, console=console, **kwargs)

    # -------------------------
    # Internal: handlers
    # -------------------------

    def _ensure_console_handler(self):
        if self._find_handler_by_name(self._CONSOLE_HANDLER_NAME) is not None:
            return

        ch = logging.StreamHandler()
        ch.setFormatter(self.formatter)
        ch.addFilter(ConsoleToggleFilter())
        ch.setLevel(self.logger.level)
        ch.set_name(self._CONSOLE_HANDLER_NAME)
        self.logger.addHandler(ch)

    def _ensure_file_handler(self):
        if self._find_handler_by_name(self._FILE_HANDLER_NAME) is not None:
            return

        fh = self._build_file_handler()
        self.logger.addHandler(fh)

    def _build_file_handler(self):
        log_dir = self.__class__._log_dir
        os.makedirs(log_dir, exist_ok=True)

        filename = os.path.join(log_dir, f"{self.name}.log")

        fh = logging.handlers.TimedRotatingFileHandler(
            filename=filename,
            when=self._when_local,
            backupCount=self._backupCount_local,
            encoding="utf-8",
        )
        fh.setFormatter(self.formatter)
        fh.setLevel(self.logger.level)
        fh.set_name(self._FILE_HANDLER_NAME)
        return fh

    def _replace_file_handler(self):
        old = self._find_handler_by_name(self._FILE_HANDLER_NAME)
        if old is not None:
            try:
                self.logger.removeHandler(old)
            finally:
                try:
                    old.close()
                except Exception:
                    pass

        self.logger.addHandler(self._build_file_handler())

    def _find_handler_by_name(self, handler_name):
        for h in self.logger.handlers:
            if getattr(h, "get_name", lambda: None)() == handler_name:
                return h
        return None
