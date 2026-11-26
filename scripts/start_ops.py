"""
One-command launcher for the CFD MT5 Operation Center (API + React UI).

Usage:
    python scripts/start_ops.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.infrastructure.web.api_server import build_operation_service, create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Start CFD MT5 Operation Center web UI & API")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default 8787)")
    args = parser.parse_args()

    ui_index = ROOT / "ui" / "dist" / "index.html"
    print("=" * 68)
    print(f"CFD MT5 Operation Center -> http://{args.host}:{args.port}")
    if not ui_index.exists():
        print("Notice: ui/dist not found. Run 'cd ui && npm install && npm run build' or 'npm run dev'.")
    print("=" * 68)

    app = create_app(build_operation_service(root_dir=ROOT))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
