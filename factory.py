import os, json, re, subprocess, textwrap, datetime, pathlib, xml.etree.ElementTree as ET
import requests
from PIL import Image, ImageDraw, ImageFont
import edge_tts
IMAGE_DIR = "output/images"
os.makedirs(IMAGE_DIR, exist_ok=True)


def search_image(query, index):
    """Find a potentially relevant image on Wikimedia Commons."""
    api = "https://commons.wikimedia.org/w/api.php"

    try:
        response = requests.get(
            api,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}",
                "gsrnamespace": 6,
                "gsrlimit": 5,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata",
                "iiurlwidth": 1080,
                "format": "json",
            },
            headers={"User-Agent": "BeyondTheKnownVideoBot/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})

        for page in pages.values():
            info = page.get("imageinfo", [{}])[0]
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue

            image_response = requests.get(
                url, timeout=25,
                headers={"User-Agent": "BeyondTheKnownVideoBot/1.0"}
            )
            image_response.raise_for_status()

            path = os.path.join(IMAGE_DIR, f"scene_{index}.jpg")
            with Image.open(__import__("io").BytesIO(
                image_response.content
            )) as img:
                img = img.convert("RGB")
                img = ImageOps.fit(
                    img, (1080, 1920),
                    method=Image.Resampling.LANCZOS
                )
                img.save(path, quality=90)

            metadata = info.get("extmetadata", {})
            license_name = metadata.get("LicenseShortName", {}).get(
                "value", "Check license"
            )
            artist = metadata.get("Artist", {}).get(
                "value", "Author not listed"
            )
            page_url = page.get("canonicalurl", "")

            with open(
                os.path.join(IMAGE_DIR, "image_credits.txt"),
                "a", encoding="utf-8"
            ) as credits:
                credits.write(
                    f"{query} | {license_name} | {artist} | "
                    f"{page_url}\n"
                )

            return path

    except Exception as exc:
        print(f"Image search failed for '{query}': {exc}")

    return None

ROOT = pathlib.Path("output")
ROOT.mkdir(exist_ok=True)
W, H = 1080, 1920
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

def get_trends():
    # Google Trends RSS is a discovery signal, not proof a topic will perform.
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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_API_KEY}"
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
                    "scenes": {"type": "ARRAY", "items": {"type": "STRING"}}
                },
                "required": ["topic", "reason", "title", "description", "tags", "scenes"]
            }
        }
    }

    r = requests.post(url, json=payload, timeout=90)
    if not r.ok:
        raise RuntimeError(f"Gemini API error {r.status_code}: {r.text[:1000]}")
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    # Strip accidental fences if a model returns them.
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.I)
    return json.loads(text)

def choose_topic(trends):
    if not trends:
        trends = ["why deep ocean discoveries matter", "how everyday technology works",
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
- Title must be accurate, not deceptive.
- description is 2-3 sentences and says this is educational content.
- tags is an array of 3-6 strings.
- scenes is an array of 6 objects, each with keys "narration" and "visual_text".
- narration must total roughly 80-110 words, with a strong opening and satisfying ending.
- visual_text should be concise (maximum 8 words), not a full paragraph.
- Original wording only; no quotations or copied scripts.
"""
    return gemini(prompt)

def choose_long_topic(trends, short_topic):
    prompt = f"""
Return valid JSON only with keys: topic, title, description, tags, scenes.
Create an original English faceless YouTube long-form educational video, about 4-6 minutes,
for a general audience. Use 8-10 scenes with narration around 45-75 words each.
Possible trend candidates (not verified facts): {json.dumps(trends)}
Today's Short topic for context only: {short_topic}
Choose an evergreen subject with clear educational value. Avoid medical, financial,
political breaking-news or tragedy claims. Do not invent statistics or citations.
Each scene object must have "narration" and "visual_text" (max 8 words).
description should be 2-3 sentences. tags is an array of 3-6 strings.
"""
    return gemini(prompt)

def get_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    words = str(text).split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0,0), test, font=font)[2] <= max_width:
            line = test
        else:
            if line: lines.append(line)
            line = word
    if line: lines.append(line)
    return lines

def make_card(text, index, total, outpath, vertical=True):
    w, h = (W, H) if vertical else (1920, 1080)
    # Simple, original graphic card; no external image copyright issues.
    im = Image.new("RGB", (w, h), (12, 22, 42))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((70, 90, w-70, h-90), radius=48, outline=(54, 120, 170), width=5)
    d.text((w//2, int(h*.17)), "QUICK EXPLAINER", font=get_font(36, True),
           fill=(100, 210, 255), anchor="mm")
    font = get_font(66 if vertical else 62, True)
    lines = wrap_text(d, text, font, w-180)
    line_h = int(font.size * 1.35)
    y = h//2 - (len(lines)*line_h)//2
    for line in lines:
        d.text((w//2, y), line, font=font, fill=(248, 250, 255), anchor="mm")
        y += line_h
    d.text((w//2, int(h*.84)), f"{index:02d} / {total:02d}", font=get_font(30),
           fill=(160, 180, 200), anchor="mm")
    im.save(outpath, quality=92)

async def synthesize(text, out_mp3):
    communicate = edge_tts.Communicate(text, voice="en-US-AriaNeural", rate="+0%")
    await communicate.save(out_mp3)

def run(cmd):
    print("RUN:", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)

def render_video(data, stem, vertical=True):
    scenes = data.get("scenes", [])
    if not scenes:
        raise RuntimeError("Model returned no scenes.")
    work = ROOT / f"work_{stem}"
    work.mkdir(exist_ok=True)
    scene_files, audio_files = [], []
    import asyncio
    for i, scene in enumerate(scenes):
        # 1. Safe JSON structural fallback
        if isinstance(scene, dict):
            visual_text = scene.get("visual_text", "A surprising idea")
            narration = str(scene.get("narration", "")).strip()
        else:
            visual_text = str(scene)
            narration = str(scene)
                
        # 2. Dynamically build paths on-the-fly to prevent IndexErrors
        dynamic_scene_file = work / f"{stem}_{i}.jpg"
        
        # 3. Pass the clean, dynamic path to make_card
        make_card(visual_text, i, len(scenes), dynamic_scene_file, vertical)

    # 4. Re-declare the list trackers to match the exact number of generated scenes
    scene_files = [work / f"{stem}_{idx}.jpg" for idx in range(len(scenes))]
    audio_files = [work / f"{stem}_{idx}.mp3" for idx in range(len(scenes))]

    clips = []
    metadata = {k: data.get(k) for k in ["topic", "title", "description", "tags", "reason"]}
    (ROOT / f"{stem}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return out

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    date = now.strftime("%Y-%m-%d")
    trends = get_trends()
    print("Trend candidates:", trends)
    short = choose_topic(trends)
    short["reason"] = short.get("reason", "Selected from trend candidates and evergreen potential.")
    render_video(short, f"short_{date}", vertical=True)
    # Weekly long-form draft: Sunday UTC. Change this if you prefer another day.
    if now.weekday() == 6:
        long = choose_long_topic(trends, short.get("topic", ""))
        render_video(long, f"long_{date}", vertical=False)
    print("Draft generation complete. Review videos before publishing.")

if __name__ == "__main__":
    main()
