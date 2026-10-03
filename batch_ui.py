import streamlit as st
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo

from batch_generator import (
    generate_topics,
    generate_topic_candidates,
    generate_shorts_batch,
    next_hour,
    build_schedule,
    load_title_history,
    remember_title,
)
from pexels import get_best_video
from tts import generate_voice
from video_engine import build_short
from analytics import fetch_shorts_performance, build_performance_insights, learning_report, recommend_publish_slots
from youtube_uploader import connect_youtube, is_connected, schedule_short, video_url
from content_learning import remember_content


def _fmt_num(v):
    try:
        return f"{float(v):,.0f}"
    except Exception:
        return "0"


def _analytics_table(rows):
    data = []
    for r in rows:
        views = float(r.get("views", 0) or 0)
        likes = float(r.get("likes", 0) or 0)
        subs = float(r.get("subscribersGained", 0) or 0)
        sub_rate = (subs / views * 1000) if views else 0
        data.append({
            "Title": r.get("title", ""),
            "Category": r.get("category", ""),
            "Hook": r.get("hook_style", ""),
            "Published": str(r.get("publishedAt", ""))[:16].replace("T", " "),
            "Views": int(views),
            "Engaged": int(float(r.get("engagedViews", 0) or 0)),
            "Avg % Watched": round(float(r.get("averageViewPercentage", 0) or 0), 1),
            "Avg View (sec)": round(float(r.get("averageViewDuration", 0) or 0), 1),
            "Likes": int(likes),
            "Shares": int(float(r.get("shares", 0) or 0)),
            "Subs +": int(subs),
            "Subs / 1K": round(sub_rate, 2),
            "Like %": round((likes / views * 100) if views else 0, 2),
            "Source": "Analytics" if r.get("analyticsAvailable") else "Public count (fallback)",
            "Video ID": r.get("video", ""),
        })
    return data


def _parse_custom_slots(text, timezone_name, count):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if len(lines) != count:
        raise ValueError(f"Custom schedule needs exactly {count} lines; you entered {len(lines)}.")
    tz = ZoneInfo(timezone_name)
    formats = ["%d %b %Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y %I:%M %p"]
    slots = []
    for line in lines:
        parsed = None
        for fmt in formats:
            try:
                parsed = datetime.strptime(line, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            raise ValueError(f"Could not parse custom time: {line}")
        slots.append(parsed.replace(tzinfo=tz))
    if any(slots[i] >= slots[i + 1] for i in range(len(slots) - 1)):
        raise ValueError("Custom publish times must be in strictly increasing order.")
    return slots


def render_batch_ui():
    st.markdown('<div class="section-title">🚀 AI Shorts Studio <span style="font-size:14px;color:#9ca3af">• Adaptive + Manual Scheduling</span></div>', unsafe_allow_html=True)
    st.caption("Choose how many Shorts to make. Then choose whether AI or YOU control the exact publish times.")

    # =========================================================
    # YOUTUBE CONNECTION
    # =========================================================
    st.markdown("### ▶️ YouTube")
    if is_connected():
        st.success("YouTube is connected — Upload + Analytics access is ready.")
    else:
        st.warning("YouTube needs a fresh OAuth connection because Analytics access is now enabled.")

    if st.button("🔐 Connect / Reconnect YouTube", use_container_width=True, key="connect_youtube"):
        try:
            with st.spinner("Opening Google authorization..."):
                connect_youtube()
            st.success("✅ YouTube connected with upload + analytics access.")
            st.rerun()
        except Exception as e:
            st.error("❌ YouTube connection failed:\n\n" + str(e))

    # =========================================================
    # PERFORMANCE DASHBOARD
    # =========================================================
    st.markdown("### 📈 Last 21 Days — Shorts Performance")
    a1, a2, a3 = st.columns([1, 1, 4])
    with a1:
        refresh = st.button("🔄 Refresh Analytics", key="refresh_analytics")
    with a2:
        if st.session_state.get("analytics_rows"):
            st.success("Live")

    if refresh or "analytics_rows" not in st.session_state:
        if is_connected():
            try:
                with st.spinner("Reading your last 21 days of Shorts analytics..."):
                    st.session_state["analytics_rows"] = fetch_shorts_performance(days=21)
            except Exception as e:
                st.session_state["analytics_error"] = str(e)
        else:
            st.session_state["analytics_rows"] = []

    rows = st.session_state.get("analytics_rows", [])
    if st.session_state.get("analytics_error"):
        st.error("Analytics error: " + st.session_state.pop("analytics_error"))

    if rows:
        report = learning_report(rows, "Asia/Kolkata")
        st.markdown("#### 🧠 What the system learned")
        for signal in report["signals"]:
            st.write("• " + signal)
        st.success("The next batch will use these learned signals for topic selection and automatic scheduling.")

        total_views = sum(float(r.get("views", 0) or 0) for r in rows)
        total_subs = sum(float(r.get("subscribersGained", 0) or 0) for r in rows)
        analytics_rows = [r for r in rows if r.get("analyticsAvailable")]
        avg_retention = (
            sum(float(r.get("averageViewPercentage", 0) or 0) for r in analytics_rows) / len(analytics_rows)
            if analytics_rows else 0
        )
        avg_views = total_views / len(rows)
        avg_sub_rate = (total_subs / total_views * 1000) if total_views else 0
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Shorts", len(rows))
        c2.metric("Total views", _fmt_num(total_views))
        c3.metric("Avg views / Short", _fmt_num(avg_views))
        c4.metric("Avg % watched", f"{avg_retention:.1f}%" if analytics_rows else "Pending")
        c5.metric("Subs / 1K views", f"{avg_sub_rate:.2f}")

        st.table(_analytics_table(sorted(rows, key=lambda x: float(x.get("views", 0) or 0), reverse=True)))
        analytics_count = sum(1 for r in rows if r.get("analyticsAvailable"))
        fallback_count = len(rows) - analytics_count
        source_note = f"{analytics_count} with Analytics metrics"
        if fallback_count:
            source_note += f"; {fallback_count} using current public counts while Analytics catches up"
        st.caption(f"Subscribers gained from these Shorts: {total_subs:.0f}. Data source: {source_note}.")
        st.info("🧠 The next batch uses these results to favor winning categories, hook styles, title signals and subscriber-converting patterns, while still exploring new ideas.")
    else:
        st.warning("No recent Shorts were returned from YouTube. The system checks recent uploads through the YouTube Data API first, then identifies Shorts from duration/aspect metadata. If you have Shorts visible in Studio from the last 7 days, click **Refresh Analytics** once more; if it still stays empty, the connection/query needs diagnosis rather than waiting for more Shorts.")

    # =========================================================
    # BATCH SETTINGS
    # =========================================================
    st.markdown("### ⚙️ Batch Settings")
    st.caption("Set the number of Shorts, then choose your publishing mode below. The AI will aggressively filter for broad, curiosity-driven topics and strong first-2-second hooks.")
    col1, col2 = st.columns(2)
    with col1:
        niche = st.text_area(
            "🧠 Channel niche / topic area",
            value="science, human body, animals and nature, history, space, everyday science and amazing facts",
            height=90,
            key="batch_niche",
        )
        style = st.selectbox(
            "🎨 Style",
            ["Documentary", "Educational", "Mysterious", "Funny", "Dramatic", "Storytelling", "News-style", "Cinematic"],
            key="batch_style",
        )
        short_count = st.slider("📦 Number of Shorts", min_value=1, max_value=24, value=24, step=1, key="batch_count")
        trending_count = st.slider("🔥 Trending topics", min_value=0, max_value=short_count, value=min(6, short_count), step=1, key="batch_trending")

    with col2:
        voice = st.selectbox(
            "🎙️ Voice",
            ["Kore", "Puck", "Charon", "Zephyr", "Fenrir", "Leda", "Aoede", "Iapetus", "Algieba", "Gacrux"],
            key="batch_voice",
        )
        timezone_name = st.selectbox(
            "🌍 Timezone",
            ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles", "UTC"],
            index=0,
            key="batch_timezone",
        )

    # Publishing can be fully automatic OR manually controlled by the user.
    st.markdown("## 🕐 PUBLISHING MODE")
    st.info("Choose **🤖 AI** to let the system learn the best windows, or choose **✋ Manual** to enter the exact date and time for every Short.")
    schedule_mode = st.radio(
        "Who decides the publish time?",
        ["🤖 Let AI choose", "✋ I'll choose the times"],
        horizontal=True,
        key="schedule_mode",
        help="AI mode learns from your recent Shorts. Manual mode lets you choose the exact date and time for every Short.",
    )

    manual_slots = []
    manual_error = None
    if schedule_mode == "🤖 Let AI choose":
        planned_slots = recommend_publish_slots(rows, short_count, timezone_name=timezone_name)
        st.markdown("### 🤖 AI Publishing Schedule")
        if rows:
            st.caption("Publishing-time optimization may still use your analytics; topic selection is intentionally independent of analytics during the channel-identity phase.")
        else:
            st.caption("Not enough analytics yet — using a conservative fallback schedule until your channel has more data.")
        with st.expander("🗓️ See what the AI chose", expanded=True):
            st.table(
                [{"Short": i + 1, "Publish": slot.strftime("%d %b %Y, %I:%M %p %Z"), "Reason": "Learned window" if rows else "Fallback"} for i, slot in enumerate(planned_slots)]
            )
    else:
        st.markdown("### ✋ Your Publishing Schedule")
        st.caption("Choose the exact publish date and time for each Short. Times use your selected timezone.")
        tz = ZoneInfo(timezone_name)
        default_start = datetime.now(tz) + timedelta(hours=1)
        for i in range(short_count):
            default_dt = default_start + timedelta(hours=i)
            c1, c2 = st.columns(2)
            with c1:
                d = st.date_input(f"Short {i + 1} — date", value=default_dt.date(), key=f"manual_date_{i}")
            with c2:
                t = st.time_input(f"Short {i + 1} — time", value=default_dt.time().replace(second=0, microsecond=0), key=f"manual_time_{i}")
            manual_slots.append(datetime.combine(d, t).replace(tzinfo=tz))

        try:
            if any(manual_slots[i] >= manual_slots[i + 1] for i in range(len(manual_slots) - 1)):
                raise ValueError("Manual publish times must be in strictly increasing order.")
            if manual_slots and manual_slots[0] <= datetime.now(tz):
                raise ValueError("The first manual publish time must be in the future.")
            planned_slots = manual_slots
        except ValueError as exc:
            planned_slots = manual_slots
            manual_error = str(exc)
            st.error("⚠️ " + manual_error)

    contains_synthetic_media = st.checkbox(
        "🤖 Disclose realistic AI / altered media",
        value=False,
        help="Enable when the Short contains realistic AI-generated or meaningfully altered people, places, events, or scenes.",
        key="batch_synthetic_media",
    )

    if planned_slots and schedule_mode == "✋ I'll choose the times":
        with st.expander("🗓️ Preview your schedule", expanded=False):
            st.table(
                [{"Short": i + 1, "Publish": slot.strftime("%d %b %Y, %I:%M %p %Z")} for i, slot in enumerate(planned_slots)]
            )

    st.markdown('<div class="info-box">💡 Generation remains sequential for stability. Gemini creates topics + all scripts in minimal requests, while TTS/Pexels/rendering run one Short at a time.</div>', unsafe_allow_html=True)

    if st.button(f"🚀 Generate & Schedule {short_count} Shorts", use_container_width=True, type="primary", key="run_batch"):
        if not niche.strip():
            st.warning("Please enter a channel niche.")
            return
        if not is_connected():
            st.error("Please reconnect YouTube so the project has both upload and Analytics permissions.")
            return
        if len(planned_slots) != short_count:
            st.error("Please provide a complete valid schedule before starting.")
            return
        if manual_error:
            st.error("Fix the manual publishing times before starting the batch.")
            return
        now_by_tz = datetime.now(planned_slots[0].tzinfo)
        if planned_slots[0] <= now_by_tz:
            st.error("The first publish time must be in the future.")
            return

        st.session_state["batch_results"] = []
        try:
            with st.status(f"🚀 Preparing {short_count} Shorts...", expanded=True) as batch_status:
                used_titles = load_title_history()
                performance_insights = ""
                st.write("🧭 Topic engine is in 1-month channel-identity mode — analytics are intentionally ignored for topic selection.")
                st.write("🔥 Finding fresh trend signals + generating unique topics...")
                candidates = generate_topic_candidates(
                    n=short_count,
                    niche=niche,
                    avoid_topics=used_titles,
                    trending_count=trending_count,
                    performance_insights=performance_insights,
                )
                topics = [x["topic"] for x in candidates]
                candidate_map = {x["topic"].casefold(): x for x in candidates}
                st.session_state["batch_topics"] = topics
                st.session_state["batch_topic_candidates"] = candidates

                st.write("🧠 Quality gate passed: high-curiosity, broad-appeal topics selected; technical/generic candidates rejected.")
                with st.expander("🔎 See the selected topic signals", expanded=False):
                    st.table([
                        {
                            "#": i + 1,
                            "Topic": x["topic"],
                            "Category": x.get("category", ""),
                            "Hook style": x.get("hook_style", ""),
                            "Quality": round(float(x.get("quality_score", 0) or 0)),
                            "Trending": "Yes" if x.get("is_trending") else "No",
                        }
                        for i, x in enumerate(candidates)
                    ])

                st.write(f"🧠 Generating all {short_count} scripts in ONE Gemini request with 3-hook testing per Short...")
                scripts = generate_shorts_batch(topics=topics, style=style)
                script_map = {str(x.get("topic", "")).strip().casefold(): x for x in scripts}

                progress = st.progress(0, text=f"Starting Short 1/{short_count}")
                used_slots = []

                for index, topic in enumerate(topics, start=1):
                    try:
                        st.write(f"🎬 Short {index}/{short_count} — {topic}")
                        result = script_map.get(topic.casefold())
                        if result is None:
                            raise RuntimeError(f"No batch script returned for: {topic}")

                        audio_data = generate_voice(text=result["voiceover"], voice=voice, style=style)
                        scenes = result.get("scenes", [])
                        videos = []
                        for scene in scenes:
                            query = str(scene.get("search_query", "")).strip()
                            videos.append(get_best_video(query) if query else None)
                            time.sleep(0.15)
                        if any(v is None for v in videos):
                            raise RuntimeError("No suitable Pexels footage for one or more scenes.")

                        video_bytes = build_short(
                            videos=videos,
                            audio_data=audio_data,
                            scenes=scenes,
                            voiceover=result.get("voiceover", "")
                        )

                        slot = planned_slots[index - 1]
                        now_local = datetime.now(slot.tzinfo)
                        if slot <= now_local:
                            slot = next_hour(now_local)
                        if used_slots and slot <= used_slots[-1]:
                            slot = used_slots[-1] + timedelta(hours=1)
                        used_slots.append(slot)

                        tags = [str(x).lstrip("#").strip() for x in result.get("hashtags", []) if str(x).strip()]
                        description = result.get("description", result.get("caption", ""))
                        response = schedule_short(
                            video_bytes=video_bytes,
                            title=topic,
                            description=description,
                            tags=tags,
                            publish_at=slot,
                            category_id="27",
                            made_for_kids=False,
                            contains_synthetic_media=contains_synthetic_media,
                        )
                        video_id = response["id"]
                        remember_title(topic)
                        candidate_meta = candidate_map.get(topic.casefold(), {})
                        remember_content(video_id, {
                            **candidate_meta,
                            "topic": topic,
                            "selected_hook": result.get("selected_hook", result.get("hook", "")),
                            "cta": result.get("cta", ""),
                        })
                        st.session_state["batch_results"].append({
                            "number": index,
                            "topic": topic,
                            "title": topic,
                            "publish_at": slot.isoformat(),
                            "status": "Scheduled",
                            "video_id": video_id,
                            "youtube_url": video_url(video_id),
                        })
                        st.success(f"✅ {index}/{short_count} scheduled — {slot.strftime('%d %b, %I:%M %p %Z')}")
                    except Exception as item_error:
                        st.session_state["batch_results"].append({
                            "number": index,
                            "topic": topic,
                            "title": "",
                            "publish_at": "",
                            "status": "FAILED",
                            "video_id": "",
                            "youtube_url": "",
                            "error": str(item_error),
                        })
                        st.error(f"❌ Short {index}/{short_count} failed: {item_error}")
                    progress.progress(index / short_count, text=f"Completed {index}/{short_count}")

                batch_status.update(label=f"🎉 Batch finished. {len([x for x in st.session_state['batch_results'] if x.get('status') == 'Scheduled'])}/{short_count} scheduled.", state="complete")
        except Exception as e:
            st.error("❌ Batch failed before completion:\n\n" + str(e))

    if st.session_state.get("batch_results"):
        st.divider()
        st.markdown("## 📊 Batch Results")
        st.table([
            {"Short": r.get("number"), "Title": r.get("title") or r.get("topic"), "Publish": r.get("publish_at"), "Status": r.get("status"), "YouTube ID": r.get("video_id")} for r in st.session_state["batch_results"]
        ])
