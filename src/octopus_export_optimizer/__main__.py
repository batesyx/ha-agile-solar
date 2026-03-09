"""Entry point: python -m octopus_export_optimizer."""

import argparse
import sys

from octopus_export_optimizer.config.settings import AppSettings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Octopus Export Optimizer — decision and reporting engine"
    )
    parser.add_argument(
        "-c", "--config",
        default=None,
        help="Path to config.yaml file (default: auto-detect)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo mode with synthetic data (no API keys needed)",
    )
    args = parser.parse_args()

    if args.demo:
        from octopus_export_optimizer.demo import run_demo
        run_demo()
        return

    from octopus_export_optimizer.app import Application
    settings = AppSettings.load(config_path=args.config)
    app = Application(settings)
    app.run()


if __name__ == "__main__":
    main()
