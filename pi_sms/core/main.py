"""Main entry point for the pi-sms daemon."""

import argparse
import asyncio
import os
import signal
import sys
import warnings

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from pydantic import ValidationError

# Suppress RuntimeWarning when running as python -m pi_sms.core.main
# This warning occurs because Python imports the package before executing the module
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*found in sys.modules.*")

try:
    from ..filter.filter import SmsFilter
    from ..modem.hilink import HilinkClient
    from .config import Config, load_config
    from .debug import debug_print
    from .dependencies import check_external_dependencies
    from .scheduler import start_scheduler
    from .workflow import poll_and_process
except ImportError:
    # Allow running as script: python pi_sms/core/main.py
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    from pi_sms.core.config import Config, load_config
    from pi_sms.core.debug import debug_print
    from pi_sms.core.dependencies import check_external_dependencies
    from pi_sms.core.scheduler import start_scheduler
    from pi_sms.core.workflow import poll_and_process
    from pi_sms.filter.filter import SmsFilter
    from pi_sms.modem.hilink import HilinkClient

# Global state
scheduler: AsyncIOScheduler | None = None
config: Config | None = None
_shutdown_event: asyncio.Event | None = None
_is_running = {"value": False}


def shutdown(signum: int, frame: object) -> None:  # noqa: ARG001
    """Graceful shutdown handler."""
    global _shutdown_event
    print()  # Newline to clear the countdown line
    print("Stopping service...")
    if _shutdown_event:
        _shutdown_event.set()
    else:
        sys.exit(0)


async def run_service(config_path: str | None = None) -> None:
    """Run the daemon in an async context.

    Args:
        config_path: Path to configuration file. If None, uses CONFIG_PATH env var or default.
    """
    global config, _shutdown_event, scheduler
    _shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    if hasattr(loop, "add_signal_handler"):
        try:
            loop.add_signal_handler(signal.SIGINT, shutdown, signal.SIGINT, None)
            loop.add_signal_handler(signal.SIGTERM, shutdown, signal.SIGTERM, None)
        except (ValueError, OSError):
            pass

    print("pi-sms daemon starting...")

    check_external_dependencies()

    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")

    print(f"Loading config from: {config_path}")

    try:
        config = load_config(config_path)
    except ValidationError:
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"Failed to load config: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Failed to load config: {e}")
        sys.exit(1)

    debug_enabled = os.getenv("DEBUG_MODE", "false").lower() == "true"
    debug_print("\nConfiguration:")
    debug_print(f"  Debug mode: {'enabled' if debug_enabled else 'disabled'}")
    debug_print(f"  Modem: {config.modem.base_url}")
    debug_print(f"  Trello list: {config.trello.list_id}")
    debug_print(f"  Exclude patterns: {len(config.filter.exclude_patterns)}")
    debug_print()

    modem = HilinkClient(config.modem.base_url, config.modem.request_timeout_seconds)
    sms_filter = SmsFilter(config.filter.exclude_patterns)

    async def _poll_and_process_wrapper() -> None:
        assert config is not None
        await poll_and_process(config, modem, sms_filter, _is_running)

    # Run an initial poll immediately, then start the recurring schedule
    await _poll_and_process_wrapper()
    scheduler = start_scheduler(config, _poll_and_process_wrapper)

    try:
        while not _shutdown_event.is_set():
            try:
                await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                break
            except TimeoutError:
                continue
    except asyncio.CancelledError:
        pass
    finally:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if tasks:
            for task in tasks:
                task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1.0)
            except (TimeoutError, Exception):
                pass

        if scheduler:
            try:
                scheduler.remove_all_jobs()
                scheduler.shutdown(wait=False)
            except Exception:
                pass


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="SMS-to-Trello daemon for Raspberry Pi with a Huawei E3372 HiLink modem"
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="Path to configuration file (overrides CONFIG_PATH environment variable)",
    )
    args = parser.parse_args()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, shutdown)

    try:
        asyncio.run(run_service(config_path=args.config))
    except KeyboardInterrupt:
        print("\nStopping service...")
        sys.exit(0)


if __name__ == "__main__":
    main()
