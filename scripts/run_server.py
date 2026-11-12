"""Portable launcher for an owner-installed checkout; never installs at startup."""
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent
py=root/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
if not py.exists():raise SystemExit('Run python3 scripts/install_local.py first to install dependencies.')
os.execv(str(py),[str(py),str(root/'scripts/server.py'),*sys.argv[1:]])
