"""Render one narrated English lesson and publish a request-specific manifest."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
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
        f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf"]
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
    # Keep key content inside the central safe area for Reel controls/captions.
    cream, ink, coral, mint = "#f5f1e8", "#172b29", "#ff704f", "#c9e9d7"
    im = Image.new("RGB", (1080, 1920), cream)
    d = ImageDraw.Draw(im)
    d.ellipse((730, -160, 1240, 350), fill=mint)
    d.ellipse((-180, 1480, 300, 1960), fill="#eadfcf")
    # Small editorial masthead and lesson number.
    d.rounded_rectangle((88, 170, 408, 226), radius=28, fill=ink)
    d.text((111, 182), "DAILY / ENGLISH", font=font(27, True), fill=cream)
    d.text((815, 180), f"0{index} / 03", font=font(28, True), fill=ink)
    headlines = ["Sound more", "Make it", "Try it in"]
    endings = ["natural.", "click.", "real life."]
    d.text((88, 290), headlines[index-1], font=font(92, True), fill=ink)
    d.text((88, 395), endings[index-1], font=font(92, True), fill=coral)
    panel = (88, 570, 944, 1180)
    fill = [ink, mint, coral][index-1]
    fg = cream if index == 1 else ink
    # Offset shadow adds depth without a large empty enclosing card.
    d.rounded_rectangle((100, 584, 956, 1194), radius=38, fill="#ddd4c5")
    d.rounded_rectangle(panel, radius=38, fill=fill)
    small_label = ["THE PHRASE", "IN PLAIN ENGLISH", "YOUR EXAMPLE"][index-1]
    d.text((132, 612), small_label, font=font(26, True), fill=fg)
    d.line((132, 668, 890, 668), fill=fg, width=2)
    size = 90 if index == 1 else 76
    while True:
        face = font(size, True)
        lines = wrap(d, text, face, 746)
        height = len(lines) * (size + 18)
        if height <= 410:
            break
        size -= 2
        if size < 32:
            raise ValueError("Text cannot fit on a card")
    y = 710 + (410-height)/2
    for line in lines:
        d.text((132, y), line, font=face, fill=fg)
        y += size + 18
    # Deliberate supporting visual, with a different learning cue per scene.
    d.rounded_rectangle((88, 1250, 944, 1428), radius=32, fill="#ffffff")
    d.ellipse((116, 1288, 222, 1394), fill=mint if index != 2 else coral)
    if index == 1:
        # Speech waveform.
        for n, h in enumerate([16, 35, 58, 78, 44, 26]):
            x = 137 + n*12
            d.rounded_rectangle((x, 1341-h/2, x+6, 1341+h/2), radius=3, fill=ink)
        heading, detail = "Say it with confidence", "Listen. Then repeat out loud."
    elif index == 2:
        d.line((144, 1340, 163, 1360, 197, 1320), fill=ink, width=8)
        heading, detail = "One phrase. One idea.", "Keep it simple. Make it stick."
    else:
        d.rounded_rectangle((141, 1315, 197, 1358), radius=10, outline=ink, width=4)
        d.polygon([(151, 1356), (151, 1371), (171, 1356)], fill=ink)
        heading, detail = "Your turn to speak", "Make a sentence of your own."
    d.text((252, 1292), heading, font=font(34, True), fill=ink)
    d.text((252, 1350), detail, font=font(27), fill="#5d706a")
    for j, line in enumerate(wrap(d, title, font(28), 800)[:2]):
        d.text((88, 1490+j*36), line, font=font(28), fill="#5d706a")
    # Three-part navigation gives the lesson a clear visual rhythm.
    for j, stage in enumerate(["SAY IT", "GET IT", "USE IT"]):
        x = 88+j*290
        d.rounded_rectangle((x, 1608, x+270, 1616), radius=4, fill=coral if j+1 == index else "#ddd6cb")
        d.text((x, 1636), stage, font=font(24, True), fill=ink if j+1 == index else "#86928b")
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
                "-map", "0:v:0", "-map", "1:a:0", "-vf",
                f"zoompan=z='min(1.025,1+on*0.00006)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=1080x1920:fps=30,fade=t=in:st=0:d=0.18,fade=t=out:st={duration-0.18}:d=0.18,format=yuv420p", "-af", "apad",
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
        "design_version": 2, "title": lesson["title"], "duration_seconds": round(duration, 2), "width": 1080, "height": 1920,
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
