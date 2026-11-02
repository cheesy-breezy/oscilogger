import argparse
import sys

from PySide6.QtWidgets import QApplication

from gui import MainWindow
from scope_simulator import ScopeSimulator
from real_scope import Tektronix
from scope_config import DEFAULT_RESOURCE, RESOURCE_PLACEHOLDER


def make_scope(args):
    channels = args.channels or ("CH1", "CH2")
    if args.mode == "sim":
        return ScopeSimulator(channels), f"Oscilloscope simulator {' + '.join(channels)}"

    if not args.resource or args.resource == RESOURCE_PLACEHOLDER:
        raise SystemExit("For tek mode use --resource or set OSCILLOSCOPE_RESOURCE")

    scope = Tektronix(args.resource, channels)
    return scope, f"Oscilloscope Tektronix {' + '.join(channels)}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["sim", "tek"], default="tek")
    parser.add_argument("--resource", default=DEFAULT_RESOURCE)
    channel_args = parser.add_mutually_exclusive_group()
    channel_args.add_argument(
        "--channels", nargs="+", type=str.upper, choices=["CH1", "CH2"],
        help="Channels to display (default: CH1 CH2)",
    )
    channel_args.add_argument(
        "--channel", dest="channels", action="append", type=str.upper,
        choices=["CH1", "CH2"], help="Display a single channel (legacy option)",
    )

    args = parser.parse_args()
    if args.channels and len(set(args.channels)) != len(args.channels):
        parser.error("Select each channel only once")

    scope, title = make_scope(args)

    app = QApplication(sys.argv)
    win = MainWindow(scope, title)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
