#!/usr/bin/env python3
"""Render a scripted Telegram-style chat (a YAML scenario) into a looping GIF.

    python docs/chatgif/render.py docs/chatgif/scenarios/chair.yaml
    python docs/chatgif/render.py scenarios/chair.yaml --out ../images/chair.gif --width 800 --mp4
    python docs/chatgif/render.py scenarios/chair.yaml --preview      # open the live player in a browser instead

player.html draws the chat as a pure function of time (`window.seek(t)`), so this script just steps
through the timeline frame by frame in headless Chrome, screenshots each one, and hands the frames
to ffmpeg. Runs of identical frames (the pauses) collapse into one longer frame.
"""
import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PLAYER = HERE / "player.html"


def load_scenario(path: Path) -> dict:
    scenario = yaml.safe_load(path.read_text())
    base = path.parent

    def url(p):   # asset paths are relative to the scenario file; the player needs file:// URLs
        return (base / p).resolve().as_uri()

    for who in scenario["participants"].values():
        who["avatar"] = url(who["avatar"])
    for m in scenario["messages"]:
        if m.get("image"):
            m["image"] = url(m["image"])
    return scenario


def write_player(scenario: dict, out_html: Path) -> Path:
    html = PLAYER.read_text().replace("/*SCENARIO*/ null", json.dumps(scenario, ensure_ascii=False))
    out_html.write_text(html)
    return out_html


def capture_frames(html: Path, scenario: dict, fps: int, frames_dir: Path, scale: float) -> list[tuple[Path, float]]:
    from playwright.sync_api import sync_playwright

    width, height = scenario.get("width", 900), scenario.get("height", 1000)
    frames: list[tuple[Path, float]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=scale)
        page.add_init_script("window.__driven = true")
        cdp = page.context.new_cdp_session(page)   # raw CDP screenshots skip Playwright's per-shot setup
        page.goto(html.as_uri())
        page.evaluate("window.ready")
        duration = page.evaluate("window.duration()")
        total = int(duration * fps) + 1
        print(f"timeline {duration:.1f}s, {total} frames at {fps} fps, {width}x{height}@{scale}x")
        clip = {"x": 0, "y": 0, "width": width, "height": height, "scale": scale}   # CDP ignores the page's DPR
        started = time.time()
        last_png, count = None, 0
        for i in range(total):
            page.evaluate("t => window.seek(t)", i / fps)
            shot = cdp.send("Page.captureScreenshot", {"format": "png", "clip": clip})
            png = base64.b64decode(shot["data"])
            if png == last_png:
                frames[-1] = (frames[-1][0], frames[-1][1] + 1 / fps)
                continue
            path = frames_dir / f"f{count:05d}.png"
            path.write_bytes(png)
            frames.append((path, 1 / fps))
            last_png, count = png, count + 1
            if i % (fps * 5) == 0:
                print(f"  {i / fps:5.1f}s  ({count} unique frames, {time.time() - started:.0f}s elapsed)")
        browser.close()
    print(f"captured {count} unique frames of {total} in {time.time() - started:.0f}s")
    return frames


def concat_file(frames: list[tuple[Path, float]], path: Path) -> Path:
    lines = ["ffconcat version 1.0"]
    for frame, dur in frames:
        lines += [f"file '{frame}'", f"duration {dur:.4f}"]
    lines.append(f"file '{frames[-1][0]}'")   # the concat demuxer needs the last frame repeated to honour its duration
    path.write_text("\n".join(lines) + "\n")
    return path


def encode_gif(concat: Path, out: Path, width: int, fps: int):
    scale = f"scale={width}:-1:flags=lanczos"
    filters = (f"[0:v]{scale},split[a][b];[a]palettegen=stats_mode=diff[p];"
               f"[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-r", str(fps), "-lavfi", filters, "-loop", "0", str(out)])


def encode_mp4(concat: Path, out: Path, width: int, fps: int):
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-vf", f"scale={width}:-2:flags=lanczos,format=yuv420p", "-r", str(fps),
         "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart", str(out)])


def run(cmd: list[str]):
    print("  $", " ".join(cmd[:2]), "…", cmd[-1])
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--out", type=Path, help="output GIF (default: out/<scenario>.gif)")
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--width", type=int, help="output width in px (default: the scenario's width)")
    ap.add_argument("--scale", type=float, default=2.0, help="render at this device scale before downscaling")
    ap.add_argument("--mp4", action="store_true", help="also write an .mp4 next to the GIF")
    ap.add_argument("--keep-frames", action="store_true", help="leave the PNG frames in out/<scenario>_frames/")
    ap.add_argument("--preview", action="store_true", help="open the live player in the default browser and exit")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found (brew install ffmpeg)")
    scenario = load_scenario(args.scenario)
    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)
    stem = args.scenario.stem
    html = write_player(scenario, out_dir / f"{stem}.html")
    if args.preview:
        webbrowser.open(html.as_uri())
        print(f"opened {html}")
        return

    out = args.out or out_dir / f"{stem}.gif"
    width = args.width or scenario.get("width", 900)
    frames_dir = out_dir / f"{stem}_frames" if args.keep_frames else Path(tempfile.mkdtemp(prefix="chatgif-"))
    frames_dir.mkdir(exist_ok=True)
    try:
        frames = capture_frames(html, scenario, args.fps, frames_dir, args.scale)
        concat = concat_file(frames, frames_dir / "frames.ffconcat")
        encode_gif(concat, out, width, args.fps)
        print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
        if args.mp4:
            mp4 = out.with_suffix(".mp4")
            encode_mp4(concat, mp4, width, args.fps)
            print(f"wrote {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")
    finally:
        if not args.keep_frames:
            shutil.rmtree(frames_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
