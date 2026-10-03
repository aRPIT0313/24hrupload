"""YouTube Shorts performance analytics and lightweight daily optimization."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from youtube_uploader import get_youtube_service, get_youtube_analytics_service
from content_learning import load_content_metadata


METRICS = (
    "views,engagedViews,likes,comments,shares,estimatedMinutesWatched,"
    "averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost"
)


def _parse_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _performance_views(row):
    """Return the best comparable performance-view metric for optimization.

    Since YouTube changed public view counting in August 2026, engagedViews is
    the safer apples-to-apples signal for Shorts optimization. Fall back to
    views when engagedViews is unavailable (for example during a reporting lag).
    """
    engaged = float(row.get("engagedViews", 0) or 0)
    if engaged > 0:
        return engaged
    return float(row.get("views", 0) or 0)


def _get_recent_video_ids(days=21):
    """Find recently published uploads using the uploads playlist.

    The uploads playlist is the authoritative channel-owned list.  We do not
    require Analytics here because a video can be visible in Studio before
    Analytics has exposed it.  A Search API fallback is used if the playlist
    temporarily returns no usable recent IDs.
    """
    service = get_youtube_service()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    channel = service.channels().list(
        part="contentDetails",
        mine=True,
        maxResults=1,
    ).execute()
    items = channel.get("items", [])
    if not items:
        return []

    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids = []
    page_token = None

    while True:
        data = service.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in data.get("items", []):
            published = (
                item.get("contentDetails", {}).get("videoPublishedAt")
                or item.get("snippet", {}).get("publishedAt")
            )
            vid = item.get("contentDetails", {}).get("videoId")
            if not published or not vid:
                continue
            try:
                dt = _parse_date(published)
            except Exception:
                continue
            if dt >= cutoff:
                ids.append(vid)
            else:
                # Uploads playlist is newest-first, so once we cross the
                # cutoff there is no need to request older pages.
                return ids

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    # Fallback: Search API can see public recent uploads even when the uploads
    # playlist response is temporarily stale/incomplete.
    if not ids:
        published_after = cutoff.isoformat().replace("+00:00", "Z")
        page_token = None
        while True:
            data = service.search().list(
                part="id,snippet",
                forMine=True,
                type="video",
                order="date",
                maxResults=50,
                publishedAfter=published_after,
                pageToken=page_token,
            ).execute()
            for item in data.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid:
                    ids.append(vid)
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(ids))

def _video_metadata(video_ids):
    if not video_ids:
        return {}
    service = get_youtube_service()
    result = {}
    for i in range(0, len(video_ids), 50):
        data = service.videos().list(
            part="snippet,contentDetails,statistics,fileDetails",
            id=",".join(video_ids[i:i + 50]),
            maxResults=50,
        ).execute()
        for item in data.get("items", []):
            result[item["id"]] = item
    return result


def _iso_duration_seconds(duration):
    """Convert an ISO-8601 YouTube duration (PT20S, PT1M30S, ...) to seconds."""
    if not duration:
        return 0
    m = re.fullmatch(r"P(?:0D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return 0
    hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return hours * 3600 + minutes * 60 + seconds


def _looks_like_short(item):
    """Best-effort Shorts classification from Data API metadata.

    YouTube classifies square/vertical videos up to 3 minutes as Shorts. The
    Analytics API's creatorContentType dimension is useful, but it should not
    be used as a required dimension in the per-video performance query because
    that combination can return an empty report for otherwise valid videos.
    """
    duration = _iso_duration_seconds(
        item.get("contentDetails", {}).get("duration", "")
    )
    if duration <= 0 or duration > 180:
        return False

    # fileDetails is owner-only and may not be returned for every video. If it
    # is available, use aspect ratio as a stronger signal.
    streams = item.get("fileDetails", {}).get("videoStreams", []) or []
    ratios = []
    for stream in streams:
        ratio = stream.get("aspectRatio")
        if ratio:
            ratios.append(str(ratio))
    if ratios:
        parsed = []
        for ratio in ratios:
            try:
                w, h = (float(x) for x in ratio.split(":", 1))
                parsed.append((w, h))
            except (ValueError, TypeError):
                continue
        if parsed:
            # YouTube's current rule is square or vertical + <=3 minutes.
            # Only reject when every known stream is clearly landscape.
            return any(h >= w for w, h in parsed)

    # Fallback for projects whose generated Shorts are known to be short-form.
    # This intentionally accepts <=3-minute videos when aspect-ratio metadata
    # is unavailable; Analytics data is still the source of performance truth.
    return True


def _analytics_date_range(days=21, timezone_name="America/Los_Angeles"):
    """Return a reporting window aligned with YouTube Analytics dates.

    Analytics reporting dates are calendar dates and current-day data can lag.
    We therefore query from N days ago through yesterday in Pacific Time rather
    than using the machine's UTC calendar date.
    """
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    start = (now - timedelta(days=days)).date()
    end = (now - timedelta(days=1)).date()
    if end < start:
        end = start
    return start.isoformat(), end.isoformat()


def _query_short_ids_from_analytics(video_ids, days=21):
    """Ask Analytics which candidate videos are Shorts, when available.

    This is deliberately a separate query from the performance query. It keeps
    creatorContentType out of the metric report that previously produced the
    false zero-row result.
    """
    if not video_ids:
        return set()
    analytics = get_youtube_analytics_service()
    start_date, end_date = _analytics_date_range(days)
    short_ids = set()
    for i in range(0, len(video_ids), 100):
        chunk = video_ids[i:i + 100]
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            dimensions="video,creatorContentType",
            metrics="views",
            filters="video==" + ",".join(chunk),
            maxResults=200,
            sort="-views",
        ).execute()
        headers = [h["name"] for h in response.get("columnHeaders", [])]
        for raw in response.get("rows", []):
            row = dict(zip(headers, raw))
            if row.get("creatorContentType") == "SHORTS" and row.get("video"):
                short_ids.add(row["video"])
    return short_ids


def _query_video_analytics(video_ids, days=21):
    """Query metrics by video only; avoid creatorContentType in this query."""
    if not video_ids:
        return {}

    analytics = get_youtube_analytics_service()
    start_date, end_date = _analytics_date_range(days)
    result = {}

    # The API supports up to 500 video IDs in a video filter. Keep chunks small
    # so one bad/empty report cannot affect the rest of the batch.
    for i in range(0, len(video_ids), 100):
        chunk = video_ids[i:i + 100]
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            dimensions="video",
            metrics=METRICS,
            filters="video==" + ",".join(chunk),
            maxResults=200,
            sort="-views",
        ).execute()
        headers = [h["name"] for h in response.get("columnHeaders", [])]
        for raw in response.get("rows", []):
            row = dict(zip(headers, raw))
            vid = row.get("video")
            if vid:
                result[vid] = row
    return result


def _query_video_analytics_minimal(video_ids, days=21):
    """Small fallback if a newly changed metric makes the full query fail."""
    if not video_ids:
        return {}

    analytics = get_youtube_analytics_service()
    start_date, end_date = _analytics_date_range(days)
    result = {}
    minimal_metrics = "views,engagedViews,likes,comments,shares,subscribersGained"
    for i in range(0, len(video_ids), 100):
        chunk = video_ids[i:i + 100]
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            dimensions="video",
            metrics=minimal_metrics,
            filters="video==" + ",".join(chunk),
            maxResults=200,
            sort="-views",
        ).execute()
        headers = [h["name"] for h in response.get("columnHeaders", [])]
        for raw in response.get("rows", []):
            row = dict(zip(headers, raw))
            vid = row.get("video")
            if vid:
                result[vid] = row
    return result



def fetch_long_video_performance(days=21):
    """Return recent long-form videos only for long-form topic learning.

    This intentionally does not reuse Shorts classification or Shorts hook data.
    """
    video_ids = _get_recent_video_ids(days=days)
    if not video_ids:
        return []
    metadata = _video_metadata(video_ids)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    long_ids = []
    for vid in video_ids:
        item = metadata.get(vid, {})
        published = item.get("snippet", {}).get("publishedAt")
        if not published or _parse_date(published) < cutoff:
            continue
        if not _looks_like_short(item):
            long_ids.append(vid)
    if not long_ids:
        return []
    try:
        analytics_by_video = _query_video_analytics(long_ids, days=days)
    except Exception:
        analytics_by_video = _query_video_analytics_minimal(long_ids, days=days)
    rows = []
    for vid in long_ids:
        item = metadata.get(vid, {})
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        row = dict(analytics_by_video.get(vid, {}))
        row.update({"video": vid, "title": snippet.get("title", ""),
                    "publishedAt": snippet.get("publishedAt", ""),
                    "duration": item.get("contentDetails", {}).get("duration", ""),
                    "publicViewCount": int(stats.get("viewCount", 0) or 0),
                    "publicLikeCount": int(stats.get("likeCount", 0) or 0),
                    "publicCommentCount": int(stats.get("commentCount", 0) or 0),
                    "analyticsAvailable": bool(analytics_by_video.get(vid))})
        row.setdefault("views", row["publicViewCount"]); row.setdefault("likes", row["publicLikeCount"]); row.setdefault("comments", row["publicCommentCount"])
        rows.append(row)
    return rows

def fetch_shorts_performance(days=21):
    """Return recent Shorts with robust Analytics + Data API metrics.

    The previous implementation required ``creatorContentType=SHORTS`` to be
    present in the same per-video Analytics report. In practice that could
    produce zero rows even though the channel had valid Shorts. We now:

    1. discover recent uploads with the Data API,
    2. identify likely Shorts from duration/aspect metadata,
    3. query Analytics using the stable ``video`` dimension only, and
    4. merge Analytics metrics with current public counters.

    Analytics values are preferred. Public counters are retained as a fallback
    so the dashboard can still learn from existing videos if Analytics has a
    short reporting delay.
    """
    video_ids = _get_recent_video_ids(days=days)
    if not video_ids:
        return []

    metadata = _video_metadata(video_ids)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent_ids = []
    for vid in video_ids:
        item = metadata.get(vid)
        if not item:
            continue
        published = item.get("snippet", {}).get("publishedAt")
        if not published:
            continue
        if _parse_date(published) >= cutoff:
            recent_ids.append(vid)

    if not recent_ids:
        return []

    # Identify Shorts from Data API metadata first.  This is deliberately
    # independent of Analytics: Studio can show a Short immediately while
    # Analytics still has no row for it.  Requiring creatorContentType here was
    # the main reason the old implementation could report a false zero.
    shorts_ids = [vid for vid in recent_ids if _looks_like_short(metadata[vid])]

    # If metadata is unusually incomplete, make one optional Analytics
    # classification attempt and use it only as a rescue path.
    if not shorts_ids:
        try:
            classified_short_ids = _query_short_ids_from_analytics(recent_ids, days=days)
            shorts_ids = [vid for vid in recent_ids if vid in classified_short_ids]
        except Exception:
            shorts_ids = []

    if not shorts_ids:
        return []

    try:
        analytics_by_video = _query_video_analytics(shorts_ids, days=days)
    except Exception:
        # If a newly changed Analytics metric is rejected, retry with the
        # smallest stable metric set instead of returning a false "no data".
        analytics_by_video = _query_video_analytics_minimal(shorts_ids, days=days)

    rows = []
    for vid in shorts_ids:
        item = metadata.get(vid, {})
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        row = dict(analytics_by_video.get(vid, {}))

        # A video can have no Analytics row yet (usually reporting delay).
        # Keep it in the dashboard using current public counters, while clearly
        # marking that Analytics-specific metrics are unavailable for now.
        row["video"] = vid
        row["title"] = snippet.get("title", "")
        row["publishedAt"] = snippet.get("publishedAt", "")
        row["duration"] = item.get("contentDetails", {}).get("duration", "")
        row["publicViewCount"] = int(stats.get("viewCount", 0) or 0)
        row["publicLikeCount"] = int(stats.get("likeCount", 0) or 0)
        row["publicCommentCount"] = int(stats.get("commentCount", 0) or 0)
        row["analyticsAvailable"] = bool(analytics_by_video.get(vid))

        # Public views are a fallback only. For optimization, engagedViews is
        # preferred because YouTube's public view-counting methodology changed.
        if "views" not in row:
            row["views"] = row["publicViewCount"]
        if "likes" not in row:
            row["likes"] = row["publicLikeCount"]
        if "comments" not in row:
            row["comments"] = row["publicCommentCount"]
        for key in (
            "engagedViews", "shares", "estimatedMinutesWatched",
            "averageViewDuration", "averageViewPercentage",
            "subscribersGained", "subscribersLost",
        ):
            row.setdefault(key, 0)
        rows.append(row)

    # Enrich each video with the topic/hook metadata saved at scheduling time.
    content_meta = load_content_metadata()
    for row in rows:
        meta = content_meta.get(str(row.get("video", "")), {})
        for key in ("category", "hook_style", "quality_score", "selected_hook", "cta"):
            if meta.get(key) not in (None, ""):
                row[key] = meta[key]
        # Older Shorts predate the metadata file. Infer only coarse labels from
        # their titles so existing channel history can still participate in learning.
        row.setdefault("category", _infer_category(row.get("title", "")))
        row.setdefault("hook_style", _infer_hook_style(row.get("title", "")))

    # Prefer videos with real Analytics data, then newest publication time.
    rows.sort(
        key=lambda r: (
            bool(r.get("analyticsAvailable")),
            r.get("publishedAt", ""),
        ),
        reverse=True,
    )
    return rows


def _title_keywords(rows):
    stop = {
        "the", "why", "how", "what", "when", "where", "this", "that", "are", "was",
        "were", "is", "world", "first", "most", "ever", "really", "you", "your", "and",
        "for", "with", "from", "into", "about", "behind", "can", "could", "does", "did",
        "just", "they", "their", "these", "those", "short", "problem", "secret", "mystery",
    }
    counts = Counter()
    for row in rows:
        views = _performance_views(row)
        title = row.get("title", "")
        words = set(re.findall(r"[A-Za-z0-9]{4,}", title.lower())) - stop
        for word in words:
            counts[word] += max(1, views)
    return [word for word, _ in counts.most_common(10)]


def _infer_category(title):
    text = str(title or "").casefold()
    rules = [
        ("strange_place", ["island", "place", "town", "city", "lake", "desert", "cave", "mountain", "ocean", "river"]),
        ("survival_extreme", ["survive", "survived", "survival", "trapped", "lightning", "disaster"]),
        ("real_mystery", ["mystery", "mysterious", "unexplained", "unknown", "vanished", "disappear"]),
        ("shocking_history", ["war", "roman", "ancient", "king", "emperor", "history", "battle", "190", "medieval"]),
        ("archaeology_discovery", ["archaeolog", "excavated", "discovered", "ruins", "artifact", "buried"]),
        ("bizarre_nature", ["animal", "bird", "whale", "octopus", "tree", "plant", "snake", "rain", "volcano"]),
        ("surprising_technology", ["phone", "computer", "robot", "ai", "technology", "machine", "internet", "charger"]),
        ("surprising_science", ["space", "black hole", "quantum", "science", "scientist", "physics", "gravity", "particle"]),
        ("hidden_everyday", ["why does", "why do", "why is", "button", "window", "airplane", "charger", "everyday"]),
    ]
    for category, words in rules:
        if any(word in text for word in words):
            return category
    return "other"


def _infer_hook_style(title):
    text = str(title or "").casefold().strip()
    if text.startswith(("why ", "how ", "what ", "where ", "can ")):
        return "curiosity_question"
    if any(x in text for x in ("warning", "never", "danger", "don't")):
        return "warning"
    if any(x in text for x in ("secret", "hidden", "tiny", "little", "nobody notices")):
        return "hidden_detail"
    if any(x in text for x in ("survive", "survived", "trapped", "escaped")):
        return "survival_setup"
    if any(x in text for x in ("mystery", "mysterious", "unexplained", "unknown")):
        return "mystery_setup"
    if any(x in text for x in ("first", "smallest", "largest", "oldest", "fastest", "biggest")):
        return "comparison"
    if any(x in text for x in ("impossible", "shouldn't", "should not", "can't be")):
        return "impossible_claim"
    return "shocking_reveal"


def _subscriber_rate(row):
    views = _performance_views(row)
    subs = float(row.get("subscribersGained", 0) or 0)
    return (subs / views * 1000.0) if views > 0 else 0.0


def _group_performance(rows, key):
    groups = defaultdict(list)
    for row in rows:
        value = str(row.get(key, "")).strip()
        if value:
            groups[value].append(row)
    ranked = []
    for name, group in groups.items():
        ranked.append((
            sum(_performance_views(r) for r in group) / len(group),
            sum(_subscriber_rate(r) for r in group) / len(group),
            len(group),
            name,
        ))
    ranked.sort(reverse=True)
    return ranked


def build_performance_insights(rows, timezone_name="Asia/Kolkata"):
    if not rows:
        return "No reliable Shorts performance data is available yet. Prioritize high-curiosity, broad-appeal subjects and strong 1-2 second hooks."

    top = sorted(rows, key=_performance_views, reverse=True)[:5]
    best_titles = [r.get("title", "") for r in top if r.get("title")]
    keywords = _title_keywords(rows)
    category_rank = _group_performance(rows, "category")
    hook_rank = _group_performance(rows, "hook_style")
    analytics_rows = [r for r in rows if r.get("analyticsAvailable")]

    tz = ZoneInfo(timezone_name)
    hour_views = defaultdict(list)
    day_views = defaultdict(list)
    hour_retention = defaultdict(list)
    hour_subs = defaultdict(list)
    for row in rows:
        published = row.get("publishedAt")
        if not published:
            continue
        dt = _parse_date(published).astimezone(tz)
        hour_views[dt.hour].append(_performance_views(row))
        if analytics_rows:
            hour_retention[dt.hour].append(float(row.get("averageViewPercentage", 0) or 0))
            hour_subs[dt.hour].append(float(row.get("subscribersGained", 0) or 0))
        day_views[dt.strftime("%A")].append(_performance_views(row))

    ranked_hours = sorted(hour_views, key=lambda h: sum(hour_views[h]) / len(hour_views[h]), reverse=True)[:5]
    hour_details = []
    for h in ranked_hours:
        ret = sum(hour_retention[h]) / len(hour_retention[h]) if hour_retention[h] else 0
        subs = sum(hour_subs[h]) / len(hour_subs[h]) if hour_subs[h] else 0
        hour_details.append(f"{h:02d}:00 (avg {_performance_views({'engagedViews': sum(hour_views[h])/len(hour_views[h])}):.0f} comparable views, {ret:.1f}% watched, +{subs:.1f} subs)")
    best_day = max(day_views, key=lambda d: sum(day_views[d]) / len(day_views[d])) if day_views else None

    avg_views = sum(_performance_views(r) for r in rows) / len(rows)
    avg_sub_rate = sum(_subscriber_rate(r) for r in rows) / len(rows)
    if analytics_rows:
        avg_ret = sum(float(r.get("averageViewPercentage", 0) or 0) for r in analytics_rows) / len(analytics_rows)
        avg_subs = sum(float(r.get("subscribersGained", 0) or 0) for r in analytics_rows) / len(analytics_rows)
        quality_text = f"average viewed={avg_ret:.1f}%, average subscribers={avg_subs:.2f}/Short"
    else:
        quality_text = "detailed retention/subscriber metrics are still catching up"

    category_text = ", ".join(f"{name} ({views:.0f} views/Short; {subs:.2f} subs/1K; n={n})" for views, subs, n, name in category_rank[:5]) or "not enough metadata yet"
    hook_text = ", ".join(f"{name} ({views:.0f} views/Short; {subs:.2f} subs/1K; n={n})" for views, subs, n, name in hook_rank[:5]) or "not enough metadata yet"

    return (
        f"Last {len(rows)} Shorts analyzed. Average comparable views={avg_views:.0f}/Short; average subscriber conversion={avg_sub_rate:.2f} subs/1K comparable views; {quality_text}. "
        f"Top titles: {', '.join(best_titles[:3])}. Strong title words: {', '.join(keywords[:8]) or 'none yet'}. "
        f"Best categories: {category_text}. Best hook styles: {hook_text}. "
        f"Top publish windows: {'; '.join(hour_details) or 'not enough data'}. Best weekday: {best_day or 'not enough data'}. "
        "Use winning signals as a bias, not a guarantee. Keep exploration, but reject generic/technical topics with weak cold-viewer appeal."
    )

def recommend_publish_slots(rows, count, timezone_name="Asia/Kolkata"):
    """Choose future slots using comparable performance, retention and subscriber conversion.

    The optimizer favors historically strong hours but keeps exploration so the channel
    does not lock itself permanently into one time window.
    """
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    hour_stats = defaultdict(list)
    weekday_stats = defaultdict(list)
    for row in rows:
        published = row.get("publishedAt")
        if not published:
            continue
        dt = _parse_date(published).astimezone(tz)
        hour_stats[dt.hour].append(row)
        weekday_stats[dt.weekday()].append(row)

    def mean(values):
        return sum(values) / len(values) if values else 0.0

    overall_views = mean([_performance_views(r) for r in rows]) or 1.0
    overall_ret = mean([float(r.get("averageViewPercentage", 0) or 0) for r in rows if r.get("analyticsAvailable")])
    overall_sub_rate = mean([_subscriber_rate(r) for r in rows])

    hour_scores = {}
    for hour, group in hour_stats.items():
        views = mean([_performance_views(r) for r in group])
        sub_rate = mean([_subscriber_rate(r) for r in group])
        detailed = [r for r in group if r.get("analyticsAvailable")]
        ret = mean([float(r.get("averageViewPercentage", 0) or 0) for r in detailed]) if detailed else overall_ret
        view_score = min(2.0, views / overall_views)
        ret_score = (ret / overall_ret) if overall_ret > 0 else 1.0
        sub_score = (sub_rate / overall_sub_rate) if overall_sub_rate > 0 else 1.0
        sample_bonus = min(0.12, len(group) * 0.03)
        # Views are the main early signal; retention and subscriber conversion keep
        # the optimizer from choosing a window that gets views but weak loyalty.
        hour_scores[hour] = 0.58 * view_score + 0.22 * ret_score + 0.20 * sub_score + sample_bonus

    ranked_hours = sorted(hour_scores, key=hour_scores.get, reverse=True)
    candidate_hours = []
    for hour in ranked_hours:
        for candidate in (hour, (hour - 1) % 24, (hour + 1) % 24):
            if candidate not in candidate_hours:
                candidate_hours.append(candidate)
    for hour in (8, 10, 12, 15, 18, 20, 21, 22):
        if hour not in candidate_hours:
            candidate_hours.append(hour)

    slots = []
    cursor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    used_hours = Counter()
    used_weekdays = Counter()

    for _ in range(count):
        best = None
        # Search up to 7 days ahead for the best candidate time.
        for offset in range(0, 24 * 7):
            candidate = cursor + timedelta(hours=offset)
            hour = candidate.hour
            if hour not in candidate_hours:
                continue
            base_rank = candidate_hours.index(hour)
            hour_score = hour_scores.get(hour, 0.85)
            weekday_group = weekday_stats.get(candidate.weekday(), [])
            weekday_views = mean([_performance_views(r) for r in weekday_group]) if weekday_group else overall_views
            weekday_score = weekday_views / overall_views if overall_views else 1.0
            # Small exploration bonus for an untested hour/day; repetition penalty
            # prevents dumping many Shorts into the same window.
            exploration = 0.25 if hour not in hour_stats else 0.0
            repetition = used_hours[hour] * 0.35 + used_weekdays[candidate.weekday()] * 0.08
            candidate_score = hour_score + 0.08 * weekday_score + exploration - repetition - base_rank * 0.002
            if best is None or candidate_score > best[0]:
                best = (candidate_score, candidate)

        if best is None:
            best = (0.0, cursor)
        slot = best[1]
        slots.append(slot)
        used_hours[slot.hour] += 1
        used_weekdays[slot.weekday()] += 1
        cursor = slot + timedelta(hours=1)

    return slots

def learning_report(rows, timezone_name="Asia/Kolkata"):
    """Return concise human-readable evidence of what the optimizer learned."""
    if not rows:
        return {"summary": "Not enough Shorts data yet.", "signals": [], "hours": []}

    ranked = sorted(rows, key=_performance_views, reverse=True)
    analytics_rows = [r for r in rows if r.get("analyticsAvailable")]
    avg_views = sum(_performance_views(r) for r in rows) / len(rows)
    avg_sub_rate = sum(_subscriber_rate(r) for r in rows) / len(rows)
    category_rank = _group_performance(rows, "category")
    hook_rank = _group_performance(rows, "hook_style")

    tz = ZoneInfo(timezone_name)
    hours = defaultdict(list)
    for r in rows:
        if r.get("publishedAt"):
            h = _parse_date(r["publishedAt"]).astimezone(tz).hour
            hours[h].append(_performance_views(r))
    hour_rank = sorted(hours, key=lambda h: sum(hours[h]) / len(hours[h]), reverse=True)

    signals = [
        f"Analyzed {len(rows)} Shorts ({len(analytics_rows)} with detailed Analytics metrics).",
        f"Average comparable performance: {avg_views:,.0f} views/Short.",
        f"Average subscriber conversion: {avg_sub_rate:.2f} subscribers per 1,000 comparable views.",
    ]
    if analytics_rows:
        avg_ret = sum(float(r.get("averageViewPercentage", 0) or 0) for r in analytics_rows) / len(analytics_rows)
        signals.append(f"Average viewed: {avg_ret:.1f}% on Shorts with detailed Analytics data.")
    else:
        signals.append("Detailed retention/subscriber Analytics are still catching up; public performance is used meanwhile.")
    if ranked:
        signals.append(f"Top performer: {ranked[0].get('title','Untitled')} ({_performance_views(ranked[0]):,.0f} comparable views; {_subscriber_rate(ranked[0]):.2f} subs/1K).")
    if category_rank:
        signals.append("Winning categories: " + ", ".join(f"{x[3]} ({x[0]:.0f} views/Short; {x[1]:.2f} subs/1K; n={x[2]})" for x in category_rank[:3]) + ".")
    if hook_rank:
        signals.append("Winning hook styles: " + ", ".join(f"{x[3]} ({x[0]:.0f} views/Short; {x[1]:.2f} subs/1K; n={x[2]})" for x in hook_rank[:3]) + ".")
    if hour_rank:
        signals.append("Best historical publish windows: " + ", ".join(f"{h:02d}:00" for h in hour_rank[:3]) + f" ({timezone_name}).")
    keywords = _title_keywords(rows)
    if keywords:
        signals.append("Strong title words/signals: " + ", ".join(keywords[:6]) + ".")
    signals.append("Next batch will aggressively favor high-curiosity, broad-appeal patterns and stronger opening hooks, while reserving some slots for exploration.")
    return {"summary": " | ".join(signals), "signals": signals, "hours": hour_rank[:5]}

