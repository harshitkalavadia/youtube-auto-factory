import os, json, re, subprocess, datetime, pathlib, asyncio, shutil
import xml.etree.ElementTree as ET
import requests
import edge_tts
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
ROOT = pathlib.Path("output")
ROOT.mkdir(exist_ok=True)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def get_trends():
    urls = [
        "https://trends.google.com/trending/rss?geo=US",
        "https://trends.google.com/trending/rss?geo=IN",
    ]
    topics = []
    for url in urls:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            root = ET.fromstring(r.text)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                if title and title not in topics:
                    topics.append(title)
        except Exception as e:
            print(f"Trend feed unavailable ({url}): {e}")
    return topics[:20]


def gemini(prompt):
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY GitHub Actions secret.")
    # Key goes in a header, not the URL, so it cannot leak into exception text or logs.
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent")
    headers = {"x-goog-api-key": GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.75,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "topic": {"type": "STRING"},
                    "reason": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "scenes": {"type": "ARRAY", "items": {
                        "type": "OBJECT",
                        "properties": {
                            "narration": {"type": "STRING"},
                            "visual_text": {"type": "STRING"},
                        },
                        "required": ["narration", "visual_text"],
                    }},
                },
                "required": ["topic", "reason", "title", "description", "tags", "scenes"],
            },
        },
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=90)
    except requests.RequestException as e:
        # Re-raise without the original exception chain: it may echo the request URL.
        raise RuntimeError(f"Gemini request failed: {type(e).__name__}") from None
    if not r.ok:
        raise RuntimeError(f"Gemini API error {r.status_code}: {r.text[:1000]}")
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.I)
    return json.loads(text)


def choose_topic(trends):
    if not trends:
        trends = ["why deep ocean discoveries matter",
                  "how everyday technology works",
                  "strange facts about the natural world"]
    prompt = f"""
Return valid JSON only with keys: topic, reason, title, description, tags, scenes.
Choose ONE topic suitable for an English-language faceless YouTube Short.
Use trend candidates as inspiration, not as verified facts:
{json.dumps(trends)}
Rules:
- Select a topic that can be explained responsibly and has evergreen value.
- Avoid medical, financial, political breaking-news or tragedy claims.
- Do not invent current facts. Use general, verifiable educational explanations.
- Title must be accurate, not deceptive, and at most 100 characters.
- description is 2-3 sentences and says this is educational content.
- tags is an array of 3-6 strings.
- scenes is an array of 6 objects, each with keys "narration" and "visual_text".
- narration must total roughly 80-110 words, with a strong opening and satisfying ending.
- visual_text should be concise (maximum 8 words).
- Original wording only; no quotations or copied scripts.
"""
    return gemini(prompt)


def get_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    # Pillow >= 10.1 (see requirements.txt) accepts a size for the built-in font.
    return ImageFont.load_default(size=size)


def wrap_text(draw, text, font, max_width):
    lines, line = [], ""
    for word in str(text).split():
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def make_card(text, index, total, outpath, vertical=True):
    w, h = (W, H) if vertical else (1920, 1080)
    im = Image.new("RGB", (w, h), (12, 22, 42))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((70, 90, w - 70, h - 90), radius=48,
                        outline=(54, 120, 170), width=5)
    d.text((w // 2, int(h * .17)), "QUICK EXPLAINER", font=get_font(36, True),
           fill=(100, 210, 255), anchor="mm")
    font = get_font(66 if vertical else 62, True)
    lines = wrap_text(d, text, font, w - 180)
    line_h = int(getattr(font, "size", 60) * 1.35)
    y = h // 2 - (len(lines) * line_h) // 2
    for line in lines:
        d.text((w // 2, y), line, font=font, fill=(248, 250, 255), anchor="mm")
        y += line_h
    d.text((w // 2, int(h * .84)), f"{index:02d} / {total:02d}",
           font=get_font(30), fill=(160, 180, 200), anchor="mm")
    im.save(outpath, quality=92)


async def synthesize(text, out_mp3):
    await edge_tts.Communicate(text, voice="en-US-AriaNeural").save(str(out_mp3))


def run(cmd):
    print("RUN:", " ".join(map(str, cmd)))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Command failed ({r.returncode}):\n{r.stderr[-2000:]}")


def audio_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def render_video(data, stem, vertical=True):
    scenes = data.get("scenes") or []
    if not scenes:
        raise RuntimeError("Model returned no scenes.")

    work = ROOT / f"work_{stem}"
    work.mkdir(exist_ok=True)
    parts = []

    for i, scene in enumerate(scenes):
        if isinstance(scene, dict):
            visual = str(scene.get("visual_text") or "A surprising idea").strip()
            narration = str(scene.get("narration") or "").strip() or visual
        else:
            visual = narration = str(scene).strip()

        img = work / f"{stem}_{i}.jpg"
        mp3 = work / f"{stem}_{i}.mp3"
        part = work / f"{stem}_{i}.mp4"

        make_card(visual, i + 1, len(scenes), img, vertical)
        asyncio.run(synthesize(narration, mp3))
        dur = audio_duration(mp3)

        run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-i", str(mp3),
             "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
             "-r", "30", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k",
             "-t", f"{dur:.3f}", str(part)])
        parts.append(part)

    concat_list = work / "concat.txt"
    # ffmpeg's concat demuxer single-quotes paths; escape any ' inside a path as '\''.
    concat_list.write_text(
        "".join("file '{}'\n".format(str(p.resolve()).replace("'", "'\\''"))
                for p in parts),
        encoding="utf-8")

    final = ROOT / f"{stem}.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(final)])

    metadata = {k: data.get(k) for k in ["topic", "title", "description", "tags", "reason"]}
    (ROOT / f"{stem}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return final


def main():
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    print("AUTOMATION: Generating topic with Gemini...")
    data = choose_topic(get_trends())  # fails loudly if Gemini is misconfigured

    video = render_video(data, f"short_{date}", vertical=True)
    shutil.copy(video, ROOT / "output.mp4")
    print(f"AUTOMATION: Compiled video -> {ROOT / 'output.mp4'}")

    meta = {
        "title": str(data.get("title") or "Beyond the Known")[:100],
        "description": str(data.get("description") or "Educational content."),
        "tags": [str(t) for t in (data.get("tags") or [])][:15],
    }
    (ROOT / "video_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
