import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    required = [
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        sys.exit("Missing GitHub secrets: " + ", ".join(missing))

    # Use VIDEO_FILE if specified; otherwise choose the newest MP4 in output/.
    specified = os.getenv("VIDEO_FILE")
    if specified:
        video_path = Path(specified)
    else:
        videos = list(Path("output").rglob("*.mp4"))
        if not videos:
            sys.exit("No MP4 found in output/. Video generation may have failed.")
        video_path = max(videos, key=lambda p: p.stat().st_mtime)

    if not video_path.is_file():
        sys.exit(f"Video file not found: {video_path}")

    credentials = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    credentials.refresh(Request())

    youtube = build(
        "youtube", "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    title = os.getenv(
        "YOUTUBE_VIDEO_TITLE",
        video_path.stem.replace("_", " ").replace("-", " ")
    )[:100]

    description = os.getenv(
        "YOUTUBE_VIDEO_DESCRIPTION",
        "Discover fascinating facts and unexplained mysteries with Beyond the Known."
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        progress, response = request.next_chunk()
        if progress:
            print(f"Upload progress: {int(progress.progress() * 100)}%")

    print("Upload successful.")
    print("Video ID:", response["id"])
    print("Watch URL: https://www.youtube.com/watch?v=" + response["id"])


if __name__ == "__main__":
    main()
