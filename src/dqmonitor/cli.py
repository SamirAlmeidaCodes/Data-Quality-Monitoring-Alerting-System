"""Command line entry points."""

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from dqmonitor.config import load_application
from dqmonitor.engine import CheckEngine, RunReport
from dqmonitor.errors import ConfigError
from dqmonitor.logging_config import configure_logging
from dqmonitor.storage import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dqmonitor",
        description="Run data quality checks on a schedule and serve the status API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m dqmonitor\n"
            "  python -m dqmonitor serve --port 8000\n"
            "  python -m dqmonitor run-once --config config/rules.yaml"
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "run-once"],
        help="serve the API and scheduler, or run-once and exit. Default: serve.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("DQ_CONFIG", "config/rules.yaml"),
        help="Path to the YAML config. Defaults to DQ_CONFIG or config/rules.yaml.",
    )
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)
    try:
        if args.command == "run-once":
            return _run_once(Path(args.config))
        return _serve(Path(args.config), args.host, args.port)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


def _serve(config_path: Path, host: str, port: int) -> int:
    # Validate before the server binds the port.
    settings = load_application(config_path).settings
    configure_logging(settings.logging.level)
    from dqmonitor.api.app import create_app

    app = create_app(config_path)
    uvicorn.run(app, host=host, port=port, log_level=settings.logging.level.lower())
    return 0


def _run_once(config_path: Path) -> int:
    compiled = load_application(config_path)
    configure_logging(compiled.settings.logging.level)
    store = Store(compiled.settings.storage.url)
    store.create_tables()
    try:
        report = CheckEngine(config_path, store).run()
    finally:
        store.dispose()
    print(format_report(report))
    counts = report.counts()
    if counts["error"]:
        return 2
    if counts["fail"]:
        return 1
    return 0


def format_report(report: RunReport) -> str:
    if not report.results:
        return "No checks ran."
    width = max(len(item.rule_name) for item in report.results)
    lines = [
        f"{item.rule_name:<{width}}  {item.status.upper():<5}  {item.summary}"
        for item in report.results
    ]
    counts = report.counts()
    lines.append("")
    lines.append(
        f"{counts['total']} checks: "
        f"{counts['pass']} pass, {counts['fail']} fail, {counts['error']} error"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
