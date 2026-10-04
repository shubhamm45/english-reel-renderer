"""Render one narrated English lesson and publish a request-specific manifest."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def validate(lesson, request_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
        raise ValueError("request_id must be 8-80 letters, digits, underscores or hyphens")
    if not isinstance(lesson, dict):
        raise ValueError("lesson must be a JSON object")
    for key in ("title", "phrase", "meaning", "example"):
        value = lesson.get(key)
        limit = 100 if key == "title" else 240
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{key} must be nonempty text, at most {limit} characters")
    if any(ord(c) < 32 and c not in '\n\t' for v in lesson.values() if isinstance(v, str) for c in v):
        raise ValueError("Control characters are not allowed")


def font(size, bold=False):
    candidates = [os.environ.get("REEL_FONT", ""),
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf"]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("Install DejaVu fonts or set REEL_FONT to a .ttf font")


def wrap(draw, text, face, width):
    lines = []
    for paragraph in text.splitlines() or [text]:
        line = ""
        for word in paragraph.split():
            proposed = f"{line} {word}".strip()
            if draw.textlength(proposed, font=face) <= width:
                line = proposed
            else:
                if line:
                    lines.append(line)
                line = ""
                for char in word:
                    if draw.textlength(line + char, font=face) > width:
                        lines.append(line)
                        line = char
                    else:
                        line += char
        if line:
            lines.append(line)
    return lines


def card(label, text, title, index, dest):
    im = Image.new("RGB", (1080, 1920), "#101827")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((72, 250, 1008, 1520), radius=40, fill="#18263b")
    d.text((108, 310), "DAILY ENGLISH", font=font(32, True), fill="#73e7c6")
    d.text((108, 400), label.upper(), font=font(40, True), fill="#a9b8ce")
    size = 76
    while True:
        face = font(size, True)
        lines = wrap(d, text, face, 840)
        if len(lines) * (size + 20) <= 750:
            break
        size -= 2
        if size < 30:
            raise ValueError("Text cannot fit on a card")
    y = 560
    for line in lines:
        d.text((108, y), line, font=face, fill="#ffffff")
        y += size + 20
    for line_no, line in enumerate(wrap(d, title, font(30), 840)):
        d.text((108, 1370 + line_no * 38), line, font=font(30), fill="#a9b8ce")
    d.text((108, 1600), f"{index}/3  •  Practice out loud", font=font(30), fill="#73e7c6")
    im.save(dest)


async def speak(text, voice, path):
    import edge_tts
    for attempt in range(3):
        try:
            await edge_tts.Communicate(text, voice, rate="-8%").save(str(path))
            return
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** (attempt + 1))


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))


def render(lesson, request_id, base_url, public, test_audio=False):
    validate(lesson, request_id)
    if not base_url.startswith("https://"):
        raise ValueError("base_url must start with https://")
    out = public / "reels" / request_id
    if out.exists():
        raise ValueError("request_id already exists; use a fresh unique ID")
    out.mkdir(parents=True)
    sections = [("Say this", lesson["phrase"], f"{lesson['title']}. Today's phrase: {lesson['phrase']}"),
        ("Meaning", lesson["meaning"], f"Meaning: {lesson['meaning']}"),
        ("Example", lesson["example"], f"For example: {lesson['example']}. Now repeat it out loud.")]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        segments = []
        for index, (label, text, narration) in enumerate(sections, 1):
            png, audio, video = [tmp / f"{index}.{ext}" for ext in ("png", "mp3", "mp4")]
            card(label, text, lesson["title"], index, png)
            if test_audio:
                run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio)])
            else:
                asyncio.run(speak(narration, os.environ.get("REEL_VOICE", "en-US-AriaNeural"), audio))
            duration = float(probe(audio)["format"]["duration"]) + 0.5
            run(["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(png), "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-vf", "format=yuv420p", "-af", "apad",
                "-t", str(duration), "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-maxrate", "5M", "-bufsize", "10M", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(video)])
            segments.append(video)
        listing = tmp / "concat.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments))
        target = out / "reel.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(target)])
    data = probe(target)
    vs = next(s for s in data["streams"] if s["codec_type"] == "video")
    aud = next(s for s in data["streams"] if s["codec_type"] == "audio")
    if (vs["width"], vs["height"], vs["codec_name"], aud["codec_name"]) != (1080, 1920, "h264", "aac"):
        raise ValueError("Output media validation failed")
    if target.stat().st_size > 90 * 1024 * 1024:
        raise ValueError("Video exceeds renderer's 90 MiB limit")
    duration = float(data["format"]["duration"])
    if duration > 90:
        raise ValueError("Lesson exceeds renderer's 90 second limit")
    manifest = {"request_id": request_id, "status": "ready", "video_url": f"{base_url.rstrip('/')}/reels/{request_id}/reel.mp4",
        "title": lesson["title"], "duration_seconds": round(duration, 2), "width": 1080, "height": 1920,
        "created_at": datetime.now(timezone.utc).isoformat(), "test_audio": test_audio}
    requests = public / "requests"
    requests.mkdir(exist_ok=True)
    (requests / f"{request_id}.json").write_text(json.dumps(manifest, indent=2))
    (public / "latest.json").write_text(json.dumps(manifest, indent=2))
    (public / ".nojekyll").touch()
    (public / "index.html").write_text("<!doctype html><title>English Reel Renderer</title><p>English reel renderer is running.</p>")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lesson", type=Path)
    parser.add_argument("--request-id", default=os.environ.get("REQUEST_ID"))
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL"))
    parser.add_argument("--public", type=Path, default=Path("public"))
    parser.add_argument("--test-audio", action="store_true", help="Offline verification only; generates tones, not narration")
    args = parser.parse_args()
    lesson = json.loads(args.lesson.read_text() if args.lesson else os.environ["LESSON_JSON"])
    render(lesson, args.request_id, args.base_url, args.public, args.test_audio)
