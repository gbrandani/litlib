#!/usr/bin/env python3
"""Startup script for Literature Library local web application."""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent

try:
    import fastapi
    import uvicorn
except ImportError:
    print("Error: Missing backend dependencies.")
    print("Please run the following command to install required dependencies:")
    print(f"  pip install -r {LIBRARY_ROOT}/requirements.txt")
    sys.exit(1)

if __name__ == "__main__":
    print("Starting Literature Library Web Application...")
    ssl_key = LIBRARY_ROOT / "key.pem"
    ssl_cert = LIBRARY_ROOT / "cert.pem"
    ssl_args = {}
    if ssl_key.exists() and ssl_cert.exists():
        print("Enabling HTTPS (SSL)...")
        print("Open https://localhost:8000 in your browser.")
        ssl_args = {
            "ssl_keyfile": str(ssl_key),
            "ssl_certfile": str(ssl_cert)
        }
    else:
        print("Open http://localhost:8000 in your browser.")
        
    # Run the Uvicorn server
    uvicorn.run("webapp_api:app", host="127.0.0.1", port=8000, reload=True, app_dir=str(SCRIPT_DIR), **ssl_args)
