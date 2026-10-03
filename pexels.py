import os
import requests

from dotenv import load_dotenv


load_dotenv()

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

if not PEXELS_API_KEY:
    raise ValueError(
        "PEXELS_API_KEY not found. "
        "Add it to your .env file."
    )


# =========================================
# SEARCH VIDEOS
# =========================================

def search_videos(
    query,
    per_page=10,
    orientation="portrait"
):

    url = "https://api.pexels.com/v1/videos/search"

    headers = {
        "Authorization": PEXELS_API_KEY
    }

    params = {
        "query": query,
        "orientation": orientation,
        "size": "medium",
        "per_page": per_page
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Pexels API error "
            f"{response.status_code}: "
            f"{response.text}"
        )

    return response.json()


# =========================================
# BEST VIDEO
# =========================================

def _extract_best_video(
    data,
    landscape=False
):

    videos = data.get(
        "videos",
        []
    )

    if not videos:
        return None

    candidates = []

    for video in videos:

        files = video.get(
            "video_files",
            []
        )

        for file in files:

            if file.get(
                "file_type"
            ) != "video/mp4":

                continue

            width = file.get(
                "width",
                0
            )

            height = file.get(
                "height",
                0
            )

            if landscape:

                if width <= height:
                    continue

            else:

                if height <= width:
                    continue

            candidates.append(
                {
                    "id": video.get("id"),

                    "duration": video.get(
                        "duration"
                    ),

                    "width": width,

                    "height": height,

                    "url": file.get(
                        "link"
                    ),

                    "pexels_url": video.get(
                        "url"
                    ),

                    "photographer": video.get(
                        "user",
                        {}
                    ).get(
                        "name",
                        "Unknown"
                    )
                }
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x:
            x["width"] * x["height"],
        reverse=True
    )

    return candidates[0]


# =========================================
# SHORTS VIDEO
# =========================================

def get_best_video(query):

    data = search_videos(
        query=query,
        per_page=10,
        orientation="portrait"
    )

    return _extract_best_video(
        data,
        landscape=False
    )


# =========================================
# LONG-FORM VIDEO
# =========================================

def get_best_landscape_video(query):

    data = search_videos(
        query=query,
        per_page=10,
        orientation="landscape"
    )

    video = _extract_best_video(
        data,
        landscape=True
    )

    # Fallback if Pexels doesn't return
    # a suitable landscape result.
    if video is None:

        data = search_videos(
            query=query,
            per_page=10,
            orientation="landscape"
        )

        video = _extract_best_video(
            data,
            landscape=True
        )

    return video