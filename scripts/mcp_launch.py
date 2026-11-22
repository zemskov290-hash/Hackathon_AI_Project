"""Entry point of the MCP server command in .cursor/mcp.json, .mcp.json and .vscode/mcp.json.

The config runs a short readable ``python -c`` line (``BOOTSTRAP`` in
``scripts/mcp_server.py``) that climbs to the repository root and runs this
file. The server then works from the root, whatever the client's cwd was.

By hand: ``python scripts/mcp_launch.py`` speaks MCP on stdin and stdout.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.chdir(ROOT)
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    sys.argv = [str(ROOT / "scripts" / "mcp_server.py"), *sys.argv[1:]]
    runpy.run_path(str(ROOT / "scripts" / "mcp_server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
