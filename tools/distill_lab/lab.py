#!/usr/bin/env python3
"""Entry point; all generated data stays under work/, never in source control."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "upstream"))
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from drive_lab.cli import main
    main()
