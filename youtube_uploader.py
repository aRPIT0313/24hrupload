"""YouTube Data API uploader/scheduler with local + GitHub Actions OAuth support."""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRET_FILE = Path(os.getenv("YOUTUBE_CLIENT_SECRET_FILE", str(BASE_DIR / "client_secret.json")))
TOKEN_FILE = Path(os.getenv("YOUTUBE_TOKEN_FILE", str(BASE_DIR / "youtube_token.json")))
CHUNK_SIZE = 8 * 1024 * 1024


def _restore_token_from_env():
    """Restore a refresh-capable OAuth token supplied by GitHub Actions."""
    raw = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if not raw:
        return
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("YOUTUBE_TOKEN_JSON must contain a JSON object")
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(parsed), encoding="utf-8")
    except Exception as exc:
        raise RuntimeError("Invalid YOUTUBE_TOKEN_JSON secret.") from exc


def _load_credentials():
    _restore_token_from_env()
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if creds:
        granted = set(getattr(creds, "scopes", None) or [])
        if not set(SCOPES).issubset(granted):
            creds = None
        elif creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    if not creds or not creds.valid:
        if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
            raise RuntimeError(
                "YouTube OAuth token is missing/invalid in GitHub Actions. "
                "Create YOUTUBE_TOKEN_JSON from the working local youtube_token.json."
            )
        if not CLIENT_SECRET_FILE.exists():
            raise FileNotFoundError(
                f"YouTube OAuth file not found: {CLIENT_SECRET_FILE}."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def get_youtube_service():
    return build("youtube", "v3", credentials=_load_credentials())


def get_youtube_analytics_service():
    return build("youtubeAnalytics", "v2", credentials=_load_credentials())


def is_connected():
    if not TOKEN_FILE.exists() and not os.getenv("YOUTUBE_TOKEN_JSON"):
        return False
    try:
        creds = _load_credentials()
        return bool(creds and creds.valid)
    except Exception:
        return False


def connect_youtube():
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            granted = set(getattr(creds, "scopes", None) or [])
            if set(SCOPES).issubset(granted) and (creds.valid or creds.refresh_token):
                return get_youtube_service()
        except Exception:
            pass
        try:
            TOKEN_FILE.unlink()
        except OSError:
            pass
    return get_youtube_service()


def _clean_tags(tags):
    result = []
    for tag in tags or []:
        tag = str(tag).strip().lstrip("#")
        if tag and tag not in result:
            result.append(tag)
    return result[:30]


def upload_short(video_bytes, title, description="", tags=None, publish_at=None,
                  category_id="27", made_for_kids=False, contains_synthetic_media=False):
    if not video_bytes:
        raise ValueError("Video bytes are empty.")
    title = str(title).strip()[:100]
    description = str(description or "").strip()
    tags = _clean_tags(tags)

    if publish_at is not None:
        if publish_at.tzinfo is None:
            raise ValueError("publish_at must be timezone-aware.")
        publish_at = publish_at.astimezone(timezone.utc)
        if publish_at <= datetime.now(timezone.utc):
            raise ValueError("publish_at must be in the future.")
        privacy_status = "private"
        publish_at_text = publish_at.isoformat().replace("+00:00", "Z")
    else:
        privacy_status = "public"
        publish_at_text = None

    service = get_youtube_service()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            temp_path = tmp.name

        body = {
            "snippet": {"title": title, "description": description, "tags": tags, "categoryId": str(category_id)},
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": bool(made_for_kids),
                "containsSyntheticMedia": bool(contains_synthetic_media),
            },
        }
        if publish_at_text:
            body["status"]["publishAt"] = publish_at_text

        media = MediaFileUpload(temp_path, mimetype="video/mp4", chunksize=CHUNK_SIZE, resumable=True)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        retryable = {500, 502, 503, 504}
        for attempt in range(6):
            try:
                while response is None:
                    _, response = request.next_chunk()
                break
            except HttpError as exc:
                status_code = getattr(exc.resp, "status", None)
                if status_code not in retryable or attempt >= 5:
                    raise
                response = None
                time.sleep([2, 5, 10, 20, 40, 60][attempt])
        if not response or not response.get("id"):
            raise RuntimeError("YouTube upload completed without a video ID.")
        return response
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def schedule_short(video_bytes, title, publish_at, description="", tags=None, category_id="27",
                   made_for_kids=False, contains_synthetic_media=False):
    return upload_short(video_bytes, title, description, tags, publish_at, category_id,
                        made_for_kids, contains_synthetic_media)


def video_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def set_thumbnail(video_id, thumbnail_path):
    if not video_id:
        raise ValueError("video_id is required.")
    if not os.path.exists(thumbnail_path):
        raise FileNotFoundError(thumbnail_path)
    service = get_youtube_service()
    media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg", resumable=False)
    return service.thumbnails().set(videoId=video_id, media_body=media).execute()


def upload_long_video(video_bytes, title, description="", tags=None, publish_at=None,
                      category_id="27", made_for_kids=False, contains_synthetic_media=True):
    return upload_short(video_bytes, title, description, tags, publish_at, category_id,
                        made_for_kids, contains_synthetic_media)
