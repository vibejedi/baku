"""Entry point for the packaged Windows app: double-clicking Baku.exe opens the app."""
import sys

from baku.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["app"]))
