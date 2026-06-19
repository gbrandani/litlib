#!/usr/bin/env python3
"""Sideload helper script for Local Literature Library Word Add-in on macOS."""

import os
import shutil
from pathlib import Path

def sideload():
    # Manifest path in development
    script_dir = Path(__file__).resolve().parent
    library_root = script_dir.parent
    manifest_src = library_root / "web-app" / "public" / "manifest.xml"
    
    if not manifest_src.exists():
        print(f"Error: Manifest source file not found at {manifest_src}")
        return
        
    # Sideload directory on macOS
    home = Path.home()
    wef_dir = home / "Library" / "Containers" / "com.microsoft.Word" / "Data" / "Documents" / "wef"
    
    # Create the directory if it doesn't exist
    wef_dir.mkdir(parents=True, exist_ok=True)
    
    manifest_dest = wef_dir / "manifest.xml"
    
    print(f"Copying manifest to macOS Word sideload folder:")
    print(f"  Source: {manifest_src}")
    print(f"  Destination: {manifest_dest}")
    
    try:
        shutil.copy2(manifest_src, manifest_dest)
        print("Success! Sideload manifest copied.")
        print("\nTo load the add-in in Microsoft Word:")
        print("  1. Open Microsoft Word.")
        print("  2. Create a new document or open an existing one.")
        print("  3. Go to the ribbon tab 'Insert'.")
        print("  4. Click 'Add-ins' (or the drop-down next to it) and select 'My Add-ins'.")
        print("  5. Under the 'Developer Add-ins' section, you should see 'Local Literature Library'.")
        print("  6. Select it and click 'Add'.")
    except Exception as e:
        print(f"Error copying manifest: {e}")

if __name__ == "__main__":
    sideload()
