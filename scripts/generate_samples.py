# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
"""Make deterministic real-media fixtures, outside any user's editing project."""
import argparse
import subprocess
from pathlib import Path

from lxml import etree


def generate(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    media = directory / "校验素材.mov"
    if not media.exists():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=25", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=48000", "-t", "8", "-c:v", "prores_ks",
                        "-profile:v", "1", "-pix_fmt", "yuv422p10le", "-c:a", "pcm_s16le",
                        "-ac", "2", str(media)], check=True)
    root = etree.Element("fcpxml", version="1.8")
    resources = etree.SubElement(root, "resources")
    etree.SubElement(resources, "format", id="r1", name="FFVideoFormat640x360p25", frameDuration="1/25s", width="640", height="360", colorSpace="1-1-1 (Rec. 709)")
    etree.SubElement(resources, "asset", id="r2", name="校验素材", src=media.resolve().as_uri(), start="0s", duration="8s", hasVideo="1", format="r1", hasAudio="1", audioSources="1", audioChannels="2", audioRate="48000")
    library = etree.SubElement(root, "library")
    event = etree.SubElement(library, "event", name="XMLBridge 验证")
    for index, name in enumerate(["交接校验 A", "交接校验 B"]):
        project = etree.SubElement(event, "project", name=name)
        seq = etree.SubElement(project, "sequence", format="r1", duration="5s", tcStart="3600s", tcFormat="NDF", audioLayout="stereo", audioRate="48k")
        spine = etree.SubElement(seq, "spine")
        clip = etree.SubElement(spine, "asset-clip", ref="r2", name=f"镜头 A{index}", offset="3600s", start="0s", duration="2s")
        etree.SubElement(clip, "marker", start="1s", duration="1/25s", value="核对标记", note="第一段中点")
        etree.SubElement(spine, "gap", name="空隙", offset="3602s", start="0s", duration="1s")
        etree.SubElement(spine, "asset-clip", ref="r2", name=f"镜头 B{index}", offset="3603s", start="3s", duration="2s")
    path = directory / "双项目_1.8.fcpxml"
    etree.ElementTree(root).write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True, doctype="<!DOCTYPE fcpxml>")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    print(generate(parser.parse_args().directory))
