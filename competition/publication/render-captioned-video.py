#!/usr/bin/env python3
"""Add a factual caption band without cropping or overwriting desktop footage.

Requires Pillow, ffmpeg, ffprobe and an explicit font with Chinese glyphs.
The input is the verified, already edited excerpt; no new cuts are introduced.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def run(args):
    subprocess.run([str(a) for a in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    timeline = json.loads(args.timeline.read_text())
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != timeline["sourceSha256"]:
        raise ValueError("source excerpt differs from declared input")
    if not args.font.is_file():
        raise ValueError("provide an existing Chinese font")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required")
    args.output.mkdir(parents=True, exist_ok=False)
    frames = args.output / "caption-assets"
    frames.mkdir()
    width, height = timeline["sourceWidth"], timeline["captionBandHeight"]
    header_font = ImageFont.truetype(str(args.font), 30)
    captions = timeline["captions"]
    if captions[0]["start"] != 0 or captions[-1]["end"] != timeline["durationSeconds"]:
        raise ValueError("caption timeline must cover the video")
    last = 0
    layout = []
    for index, caption in enumerate(captions):
        if caption["start"] != last or caption["end"] <= caption["start"]:
            raise ValueError("captions must have contiguous, positive durations")
        last = caption["end"]
        lines = caption["text"].splitlines()
        if len(lines) != 2:
            raise ValueError("each caption must have exactly two readable lines")
        image = Image.new("RGB", (width, height), "#112B3B")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, 4), fill="#21BCC7")
        draw.text((64, 15), timeline["statusLabel"], font=header_font, fill="#A2DEE3")
        font_size = 46
        while font_size >= 38:
            body_font = ImageFont.truetype(str(args.font), font_size)
            if all(draw.textbbox((0, 0), text, font=body_font)[2] <= width - 128
                   for text in lines):
                break
            font_size -= 1
        if font_size < 38:
            raise ValueError("caption exceeds readable band width")
        for line_index, line in enumerate(lines):
            draw.text((64, 57 + line_index * 59), line, font=body_font, fill="#F4F8FA")
        image.save(frames / f"caption-{index:02d}.png")
        layout.append({"index": index, "font_size": font_size, "lines": len(lines)})
    concat = args.output / "caption-concat.txt"
    rows = []
    for index, caption in enumerate(captions):
        rows.extend([f"file 'caption-assets/caption-{index:02d}.png'",
                     f"duration {caption['end'] - caption['start']:.3f}"])
    rows.append(f"file 'caption-assets/caption-{len(captions) - 1:02d}.png'")
    concat.write_text("\n".join(rows) + "\n")
    fps = timeline["fps"]
    count = round(timeline["durationSeconds"] * fps)
    band = args.output / "caption-band.mp4"
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-n", "-f", "concat",
         "-safe", "0", "-i", concat, "-vf", f"fps={fps}", "-frames:v", count,
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt",
         "yuv420p", "-an", band])
    output = args.output / "openKylin-Memory-Bench-讲解字幕版.mp4"
    video_height = timeline["sourceHeight"]
    filter_graph = (f"[0:v]pad=iw:ih+{height}:0:0:color=0x112B3B[desktop];"
                    f"[desktop][1:v]overlay=0:{video_height}:shortest=1[final]")
    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-n", "-i", args.source,
         "-i", band, "-filter_complex", filter_graph, "-map", "[final]", "-an",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-r", fps,
         "-frames:v", count, "-pix_fmt", "yuv420p", "-movflags", "+faststart", output])
    report = {"source_sha256": timeline["sourceSha256"],
              "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
              "source_desktop_crop": None, "additional_cuts": False,
              "band_height": height, "caption_layout": layout,
              "audio_added": False, "render_completed": True,
              "visual_verification": "pending"}
    (args.output / "render-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "frames": count,
                      "caption_count": len(captions)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
