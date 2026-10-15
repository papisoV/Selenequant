"""TGWatcher Web Application — Flask server with REST API and static file serving."""
import argparse
import logging
import os
import sys
from pathlib import Path

from flask import Flask, send_from_directory, Response

from tgwatcher.config_schema import validate_config
from tgwatcher.logging_config import setup_logging
from tgwatcher.web.api import api, init_services
from tgwatcher.web.async_loop import AsyncLoopManager
import yaml

# Configure structured logging before any app code runs so init-time log
# calls (storage migration, signal engine init) go through the new formatter.
# Level read from config later in create_app; for now use INFO as a safe floor.
setup_logging(level=os.environ.get("TGWATCHER_LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _resolve_config_path(config_path: str | None = None) -> str:
    if config_path:
        return config_path
    env_path = os.environ.get("TGWATCHER_CONFIG")
    if env_path:
        return env_path
    return str(Path.cwd() / "config.yaml")


def _load_config(config_path: str) -> dict:
    """Load YAML config and run schema validation.

    Hard-fails on validation errors (typos in required fields, missing keys,
    invalid provider config). Logs warnings for suspected typos but continues.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    errors = validate_config(config)
    if errors:
        for err in errors:
            logger.error("config: %s", err)
        print(
            f"[FATAL] Config validation failed with {len(errors)} error(s); "
            "see log above. Aborting.",
            file=sys.stderr,
        )
        sys.exit(1)
    return config


def create_app(config_path: str | None = None) -> Flask:
    config_path = _resolve_config_path(config_path)
    config = _load_config(config_path)

    # Re-apply setup_logging with config-derived level so debug etc. honors
    # config.yaml if the env var wasn't set explicitly.
    cfg_level = config.get("logging", {}).get("level") or os.environ.get("TGWATCHER_LOG_LEVEL", "INFO")
    setup_logging(level=cfg_level)

    from tgwatcher.tz_utils import set_tz_offset
    set_tz_offset(config.get("timezone", {}).get("utc_offset_hours", 8))

    static_dir = Path(__file__).resolve().parent / "static"
    app = Flask(__name__, static_folder=str(static_dir))
    app.register_blueprint(api)

    async_loop = AsyncLoopManager()
    async_loop.start()
    logger.info(
        "init_services starting",
        extra={"component": "init", "signal_enabled": bool(config.get("signal", {}).get("enabled", False))},
    )
    init_services(config, async_loop=async_loop)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.after_request
    def _no_cache_static(response):
        if response.content_type and ("javascript" in response.content_type or "html" in response.content_type):
            # no-store: never use cached copy without revalidating
            # must-revalidate: stale caches must revalidate
            # max-age=0: immediately stale
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            # Disable back-forward cache (BFCache) — forces reload on back/fwd navigation
            response.headers["X-Accel-Expires"] = "0"
            # Strip conditional-response headers so Flask won't return 304 on
            # If-None-Match / If-Modified-Since (which would bypass no-store)
            response.headers.pop("ETag", None)
            response.headers.pop("Last-Modified", None)
        return response

    return app


def main():
    parser = argparse.ArgumentParser(description="TGWatcher Web GUI")
    parser.add_argument("--port", type=int, default=5800, help="Port to run on")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    app = create_app(args.config)
    url = f"http://localhost:{args.port}"
    print(f"TGWatcher Web GUI starting on {url}")

    if not args.no_browser:
        import webbrowser
        import threading
        def _open():
            import time
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    app.run(host="0.0.0.0", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
