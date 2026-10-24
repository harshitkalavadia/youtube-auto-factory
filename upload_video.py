import json
import os
import sys
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
RETRIABLE = {500, 502, 503, 504}
MAX_RETRIES = 5
DEFAULT_TITLE = "Beyond the Known: Unexplained Mysteries"


def find_video():
    specified = os.getenv("VIDEO_FILE")
    if specified:
        path = Path(specified)
    else:
        path = Path("output/output.mp4")
        if not path.is_file():
            videos = sorted(Path("output").rglob("*.mp4"),
                            key=lambda p: p.stat().st_mtime)
            if not videos:
                sys.exit("No MP4 found in output/. Video generation may have failed.")
            path = videos[-1]
    if not path.is_file():
        sys.exit(f"Video file not found: {path}")
    return path


def load_meta(video_path):
    meta = {}
    meta_file = Path("output/video_meta.json")
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))

    title = (meta.get("title") or video_path.stem).strip()[:100] or DEFAULT_TITLE
    description = meta.get("description") or os.getenv(
        "VIDEO_DESCRIPTION", "Discover the mysteries of our universe with Beyond the Known.")
    tags = [str(t) for t in meta.get("tags", [])][:15]
    return title, description, tags


def upload(youtube, video_path, title, description, tags, privacy):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4",
        chunksize=8 * 1024 * 1024, resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media)

    response = None
    retries = 0
    while response is None:
        try:
            print("Uploading...")
            status, response = request.next_chunk()
            if status:
                print(f"Uploaded {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in RETRIABLE and retries < MAX_RETRIES:
                retries += 1
                wait = 2 ** retries
                print(f"Retriable error {e.resp.status}; retry {retries} in {wait}s")
                time.sleep(wait)
            else:
                raise
    return response["id"]


def main():
    required = ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"]
    missing = [n for n in required if not os.getenv(n)]
    if missing:
        sys.exit("Missing GitHub secrets: " + ", ".join(missing))

    video_path = find_video()
    title, description, tags = load_meta(video_path)
    privacy = os.getenv("VIDEO_PRIVACY", "private")  # set to "public" when ready

    credentials = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    credentials.refresh(Request())
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    print(f"Uploading {video_path} as '{title}' ({privacy})")
    video_id = upload(youtube, video_path, title, description, tags, privacy)
    print(f"Done: https://youtu.be/{video_id}")


if __name__ == "__main__":
    main()
