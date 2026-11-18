# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1] / "xmlbridge" / "assets"
root.mkdir(parents=True, exist_ok=True)
# The approved PNG is the source artwork; packaging must preserve its design.
with Image.open(root / "XMLBridge.png") as source:
    im = source.convert("RGBA")
    if im.size != (1024, 1024):
        raise ValueError("XMLBridge.png must be 1024 x 1024")
    im.save(root / "XMLBridge.icns", sizes=[(16,16),(32,32),(64,64),(128,128),(256,256),(512,512),(1024,1024)])
