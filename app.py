import inspect
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import streamlit as st

from video_engine import build_short
from generator import generate_short
from tts import generate_voice
from pexels import get_best_video
from batch_ui import render_batch_ui
from youtube_uploader import connect_youtube, is_connected, upload_short, upload_long_video, set_thumbnail, video_url
from analytics import fetch_long_video_performance, build_performance_insights

from long_generator import (
    generate_long_video_plan,
    build_long_video
)


# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="AI Video Generator",
    page_icon="🎬",
    layout="wide"
)


# =========================================================
# CUSTOM CSS
# =========================================================

st.markdown(
    """
    <style>

    .main-title {
        font-size: 48px;
        font-weight: 800;
        margin-bottom: 0px;
    }

    .subtitle {
        color: #9ca3af;
        font-size: 17px;
        margin-bottom: 25px;
    }

    .section-title {
        font-size: 30px;
        font-weight: 750;
        margin-top: 25px;
        margin-bottom: 10px;
    }

    .info-box {
        background: #17324d;
        padding: 16px 20px;
        border-radius: 10px;
        margin: 15px 0;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# =========================================================
# HEADER
# =========================================================

st.markdown(
    '<div class="main-title">🎬 AI Video Generator</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Create YouTube Shorts and long-form videos with Gemini + real footage fallback'
    '</div>',
    unsafe_allow_html=True
)


# =========================================================
# MODE SELECTION
# =========================================================

st.markdown("### Choose what you want to create")

mode = st.radio(
    "Video type",
    [
        "📱 20-Second Short",
        "🚀 24 Shorts / Day",
        "🎥 5–12 Minute Video"
    ],
    horizontal=True,
    label_visibility="collapsed"
)


# =========================================================
#
#                    SHORT WORKFLOW
#
# =========================================================

if mode == "📱 20-Second Short":

    st.markdown(
        '<div class="section-title">📱 Short Video Generator</div>',
        unsafe_allow_html=True
    )

    st.caption(
        "Enter a title, choose a voice and style. "
        "Everything else is automatic."
    )

    # =====================================================
    # INPUTS
    # =====================================================

    st.markdown("### 💡 Create Your Short")

    title = st.text_input(
        "📌 Title / Topic",
        placeholder="Example: Why are black holes so mysterious?",
        key="short_title"
    )

    col1, col2 = st.columns(2)

    with col1:

        video_type = st.selectbox(
            "🎨 Type",
            [
                "Documentary",
                "Educational",
                "Mysterious",
                "Funny",
                "Dramatic",
                "Storytelling",
                "News-style",
                "Cinematic"
            ],
            key="short_type"
        )

    with col2:

        short_voice = st.selectbox(
            "🎙️ Voice",
            [
                "Kore",
                "Puck",
                "Charon",
                "Zephyr",
                "Fenrir",
                "Leda",
                "Aoede",
                "Iapetus",
                "Algieba",
                "Gacrux"
            ],
            key="short_voice"
        )

    st.write("")

    # =====================================================
    # ONE CLICK GENERATION
    # =====================================================

    generate_button = st.button(
        "🎬 Generate Complete Short",
        use_container_width=True,
        type="primary",
        key="generate_complete_short"
    )

    if generate_button:

        # =================================================
        # VALIDATION
        # =================================================

        if not title.strip():

            st.warning(
                "⚠️ Please enter a title/topic first."
            )

            st.stop()

        # =================================================
        # CLEAR PREVIOUS RESULT
        # =================================================

        st.session_state.pop(
            "short_final_video",
            None
        )

        st.session_state.pop(
            "short_result",
            None
        )

        st.session_state.pop(
            "short_audio_data",
            None
        )

        st.session_state.pop(
            "short_videos",
            None
        )

        # =================================================
        # GENERATION
        # =================================================

        try:

            with st.status(
                "🎬 Creating your Short...",
                expanded=True
            ) as status:

                # =========================================
                # STEP 1 — GENERATE SCRIPT
                # =========================================

                st.write(
                    "🧠 Gemini is writing the Short..."
                )

                result = generate_short(
                    topic=title,
                    style=video_type
                )

                if not result:

                    raise RuntimeError(
                        "Gemini returned no content."
                    )

                st.session_state[
                    "short_result"
                ] = result

                # =========================================
                # STEP 2 — GENERATE VOICE
                # =========================================

                st.write(
                    f"🎙️ Generating {short_voice} voice..."
                )

                audio_data = generate_voice(
                    text=result["voiceover"],
                    voice=short_voice,
                    style=video_type
                )

                if not audio_data:

                    raise RuntimeError(
                        "Gemini TTS returned no audio."
                    )

                st.session_state[
                    "short_audio_data"
                ] = audio_data

                # =========================================
                # STEP 3 — FIND PEXELS FOOTAGE
                # =========================================

                st.write(
                    "🎥 Finding stock footage..."
                )

                videos = []

                scenes = result.get(
                    "scenes",
                    []
                )

                if not scenes:

                    raise RuntimeError(
                        "No scenes were generated."
                    )

                total = len(scenes)

                progress = st.progress(
                    0,
                    text="Searching stock footage..."
                )

                for index, scene in enumerate(scenes):

                    query = scene.get(
                        "search_query",
                        ""
                    ).strip()

                    if not query:

                        videos.append(None)

                    else:

                        video = get_best_video(
                            query
                        )

                        videos.append(
                            video
                        )

                    progress.progress(
                        (index + 1) / total,
                        text=(
                            f"Finding footage "
                            f"{index + 1}/{total}"
                        )
                    )

                st.session_state[
                    "short_videos"
                ] = videos

                # =========================================
                # VALIDATE FOOTAGE
                # =========================================

                if any(
                    video is None
                    for video in videos
                ):

                    missing = [
                        str(index + 1)
                        for index, video
                        in enumerate(videos)
                        if video is None
                    ]

                    raise RuntimeError(
                        "No suitable Pexels footage "
                        "was found for scene(s): "
                        + ", ".join(missing)
                    )

                # =========================================
                # STEP 4 — BUILD FINAL VIDEO
                # =========================================

                st.write(
                    "🎞️ Rendering final video..."
                )

                final_video = build_short(
                    videos=videos,
                    audio_data=audio_data,
                    scenes=scenes,
                    voiceover=result.get("voiceover", "")
                )

                if not final_video:

                    raise RuntimeError(
                        "Video renderer returned "
                        "an empty video."
                    )

                st.session_state[
                    "short_final_video"
                ] = final_video

                status.update(
                    label="🎉 Short ready!",
                    state="complete"
                )

            st.success(
                "🎉 Your complete Short is ready!"
            )

        except Exception as e:

            st.error(
                "❌ Short generation failed:\n\n"
                + str(e)
            )

    # =====================================================
    # FINAL SHORT OUTPUT
    # =====================================================

    if "short_final_video" in st.session_state:

        st.divider()

        st.markdown(
            "## 🎬 Your Final Short"
        )

        final_video = st.session_state[
            "short_final_video"
        ]

        # -----------------------------------------------
        # VIDEO
        # -----------------------------------------------

        st.video(
            final_video
        )

        # -----------------------------------------------
        # DOWNLOAD
        # -----------------------------------------------

        st.download_button(
            label="⬇️ Download MP4",
            data=final_video,
            file_name="ai_short.mp4",
            mime="video/mp4",
            use_container_width=True,
            key="download_final_short"
        )

        st.write("")
        if is_connected():
            if st.button("🚀 Upload Short to YouTube", use_container_width=True, type="primary", key="upload_single_short"):
                try:
                    result = st.session_state.get("short_result", {})
                    upload_title = str(result.get("title", title)).strip()[:100]
                    upload_description = str(result.get("description", result.get("caption", ""))).strip()
                    upload_tags = [str(x).lstrip("#").strip() for x in result.get("hashtags", []) if str(x).strip()]
                    with st.spinner("Uploading Short to YouTube..."):
                        response = upload_short(video_bytes=final_video, title=upload_title, description=upload_description, tags=upload_tags, category_id="27", made_for_kids=False)
                    st.success("🎉 Short uploaded successfully!")
                    if response.get("id"):
                        st.markdown(f"[▶️ Open on YouTube]({video_url(response['id'])})")
                except Exception as e:
                    st.error("❌ YouTube upload failed:\n\n" + str(e))
        else:
            st.info("🔐 Connect YouTube to upload this Short.")
            if st.button("🔐 Connect YouTube", use_container_width=True, key="connect_single_short"):
                try:
                    connect_youtube()
                    st.success("YouTube connected. You can now upload the Short.")
                    st.rerun()
                except Exception as e:
                    st.error("❌ YouTube connection failed:\n\n" + str(e))

        # =================================================
        # GENERATED YOUTUBE INFORMATION
        # =================================================

        if "short_result" in st.session_state:

            result = st.session_state[
                "short_result"
            ]

            st.divider()

            st.markdown(
                "## 📋 YouTube Information"
            )

            # ---------------------------------------------
            # TITLE
            # ---------------------------------------------

            generated_title = str(
                result.get(
                    "title",
                    title
                )
            ).strip()

            st.markdown(
                "### 📌 Title"
            )

            st.text_input(
                "YouTube Title",
                value=generated_title,
                key="generated_short_title",
                label_visibility="collapsed"
            )

            # ---------------------------------------------
            # DESCRIPTION
            # ---------------------------------------------

            # Your current generator returns "caption"
            # rather than "description".
            #
            # Therefore caption is used as the description
            # until generator.py is updated to explicitly
            # return a description.

            generated_description = str(
                result.get(
                    "description",
                    result.get(
                        "caption",
                        ""
                    )
                )
            ).strip()

            st.markdown(
                "### 📝 Description"
            )

            if generated_description:

                st.text_area(
                    "YouTube Description",
                    value=generated_description,
                    height=160,
                    key="generated_short_description",
                    label_visibility="collapsed"
                )

            else:

                st.info(
                    "No description was generated."
                )

            # ---------------------------------------------
            # HASHTAGS
            # ---------------------------------------------

            hashtags = result.get(
                "hashtags",
                []
            )

            if not isinstance(
                hashtags,
                list
            ):

                hashtags = []

            hashtags = [
                str(tag).strip()
                for tag in hashtags
                if str(tag).strip()
            ]

            # ---------------------------------------------
            # TAGS
            # ---------------------------------------------

            # Convert:
            #
            # #shorts
            # #blackholes
            #
            # into:
            #
            # shorts, blackholes

            tags = []

            for tag in hashtags:

                clean_tag = tag.lstrip("#").strip()

                if clean_tag:

                    tags.append(
                        clean_tag
                    )

            st.markdown(
                "### 🏷️ Tags"
            )

            if tags:

                tag_text = ", ".join(
                    tags
                )

                st.text_area(
                    "YouTube Tags",
                    value=tag_text,
                    height=100,
                    key="generated_short_tags",
                    label_visibility="collapsed"
                )

            else:

                st.info(
                    "No tags were generated."
                )

            # ---------------------------------------------
            # HASHTAGS
            # ---------------------------------------------

            if hashtags:

                st.markdown(
                    "### #️⃣ Hashtags"
                )

                hashtag_text = " ".join(
                    hashtags
                )

                st.text_area(
                    "YouTube Hashtags",
                    value=hashtag_text,
                    height=80,
                    key="generated_short_hashtags",
                    label_visibility="collapsed"
                )

            # ---------------------------------------------
            # HOOK
            # ---------------------------------------------

            hook = str(
                result.get(
                    "hook",
                    ""
                )
            ).strip()

            if hook:

                st.markdown(
                    "### 🪝 Hook"
                )

                st.write(
                    hook
                )


# =========================================================
#
#                    24 SHORTS WORKFLOW
#
# =========================================================

elif mode == "🚀 24 Shorts / Day":

    render_batch_ui()


# =========================================================
#
#                    LONG WORKFLOW
#
# =========================================================

else:

    st.markdown(
        '<div class="section-title">🎥 Long Video Generator</div>',
        unsafe_allow_html=True
    )

    st.caption(
        "Create a complete documentary-style video automatically."
    )

    # =====================================================
    # TOPIC SOURCE
    # =====================================================

    topic_mode = st.radio(
        "🧠 Topic",
        ["✍️ I choose the topic", "🤖 AI chooses from my YouTube data"],
        horizontal=True,
        key="long_topic_mode"
    )

    if topic_mode == "✍️ I choose the topic":
        long_title = st.text_input(
            "📌 Topic / Working Title",
            placeholder="Example: Why Did the Roman Empire Fall?",
            key="long_title"
        )
    else:
        long_title = ""
        st.info(
            "AI will study your recent long-form YouTube performance only (last 21 days: topics, "
            "titles, views, watch time, retention and subscriber conversion) and choose "
            "a fresh long-form topic with the strongest potential. This uses YouTube data, not "
            "an extra Gemini request."
        )

    # =====================================================
    # STYLE + VOICE
    # =====================================================

    col1, col2 = st.columns(2)

    with col1:

        long_style = st.selectbox(
            "🎨 Style",
            [
                "Documentary",
                "Educational",
                "Mysterious",
                "Dramatic",
                "Storytelling",
                "Investigative",
                "News-style",
                "Cinematic"
            ],
            key="long_style"
        )

    with col2:

        long_voice = st.selectbox(
            "🎙️ Voice",
            [
                "Kore",
                "Puck",
                "Charon",
                "Zephyr",
                "Fenrir",
                "Leda",
                "Aoede",
                "Iapetus",
                "Algieba",
                "Gacrux"
            ],
            key="long_voice"
        )

    # =====================================================
    # TARGET DURATION
    # =====================================================

    target_duration = st.selectbox(
        "⏱️ Video Duration",
        ["3 minutes", "4 minutes"],
        index=0,
        key="long_target_duration"
    )

    target_minutes = int(target_duration.split()[0])

    # =====================================================
    # INFO
    # =====================================================

    st.markdown(
        """
        <div class="info-box">
        💡 Long videos are now locked to a focused 3–4 minute format. You choose the exact
        publish date and time below. AI topic mode uses your existing YouTube data without
        spending another Gemini request just to pick a topic.
        </div>
        """,
        unsafe_allow_html=True
    )

    # =====================================================
    # VOICE PREVIEW
    # =====================================================

    st.markdown(
        "### 🎧 Voice Preview"
    )

    long_preview_text = (
        "Imagine a story that begins quietly, "
        "but slowly reveals something much bigger. "
        "Stay with me, because what happened next "
        "changed everything."
    )

    preview_button = st.button(
        "▶️ Preview Selected Voice",
        use_container_width=True,
        key="long_voice_preview"
    )

    if preview_button:

        preview_key = f"{long_voice}|{long_style}"
        preview_cache = st.session_state.setdefault(
            "long_voice_preview_cache", {}
        )

        if preview_key in preview_cache:
            st.session_state["long_preview_audio"] = preview_cache[preview_key]
        else:
            with st.spinner(
                "🎙️ Generating voice preview... (uses one TTS request only for a new voice/style)"
            ):

                try:

                    preview_audio = generate_voice(
                        text=long_preview_text,
                        voice=long_voice,
                        style=long_style
                    )

                    preview_cache[preview_key] = preview_audio
                    st.session_state[
                        "long_preview_audio"
                    ] = preview_audio

                except Exception as e:

                    st.error(
                        f"❌ Voice preview failed: {e}"
                    )

    if "long_preview_audio" in st.session_state:

        st.audio(
            st.session_state[
                "long_preview_audio"
            ],
            format="audio/wav"
        )

    # =====================================================
    # GENERATE LONG VIDEO
    # =====================================================

    st.write("")

    generate_long_button = st.button(
        "✨ Generate Complete Long Video",
        use_container_width=True,
        type="primary",
        key="generate_long_video"
    )

    if generate_long_button:

        if topic_mode == "✍️ I choose the topic" and not long_title.strip():

            st.warning(
                "⚠️ Please enter a topic first."
            )

        else:

            st.session_state.pop(
                "long_plan",
                None
            )

            st.session_state.pop(
                "long_final_video",
                None
            )

            st.session_state.pop(
                "long_video_duration",
                None
            )

            try:

                with st.status(
                    "🤖 Creating your documentary...",
                    expanded=True
                ) as status:

                    st.write(
                        "🧠 Planning narration and visual shots..."
                    )

                    # =====================================
                    # TOPIC / CHANNEL SIGNALS
                    # =====================================

                    performance_insights = ""

                    if topic_mode == "🤖 AI chooses from my YouTube data":
                        st.write("📊 Reading your recent YouTube performance...")
                        try:
                            rows = fetch_long_video_performance(days=21)
                            performance_insights = build_performance_insights(rows)
                            st.session_state["long_topic_performance_insights"] = performance_insights
                        except Exception as analytics_error:
                            performance_insights = (
                                "YouTube analytics could not be loaded. Choose a broad, high-curiosity "
                                "topic appropriate for the channel and avoid repeating recent subjects. "
                                f"Analytics error: {analytics_error}"
                            )

                    # =====================================
                    # PLAN — ONE GEMINI REQUEST
                    # =====================================

                    plan_function = (
                        generate_long_video_plan
                    )

                    try:

                        signature = inspect.signature(
                            plan_function
                        )

                        params = signature.parameters

                        kwargs = {}

                        if "title" in params:

                            kwargs["title"] = long_title

                        elif "topic" in params:

                            kwargs["topic"] = long_title

                        if "style" in params:

                            kwargs["style"] = long_style

                        if "performance_insights" in params:

                            kwargs["performance_insights"] = performance_insights

                        if "target_duration" in params:

                            kwargs[
                                "target_duration"
                            ] = target_minutes

                        elif "target_minutes" in params:

                            kwargs[
                                "target_minutes"
                            ] = target_minutes

                        elif "duration" in params:

                            kwargs[
                                "duration"
                            ] = target_minutes

                        plan = plan_function(
                            **kwargs
                        )

                    except Exception:

                        try:

                            plan = plan_function(
                                long_title,
                                long_style,
                                target_minutes
                            )

                        except Exception:

                            plan = plan_function(
                                long_title,
                                long_style
                            )

                    if not isinstance(
                        plan,
                        dict
                    ):

                        raise RuntimeError(
                            "Long-video planner returned "
                            "an unexpected result."
                        )

                    st.session_state[
                        "long_plan"
                    ] = plan

                    if topic_mode == "🤖 AI chooses from my YouTube data":
                        st.success(
                            f"🤖 AI selected: {plan.get('title', 'Untitled')}"
                        )

                    narration = plan.get(
                        "narration",
                        ""
                    )

                    visual_shots = plan.get(
                        "visual_shots",
                        []
                    )

                    if not narration:

                        raise RuntimeError(
                            "Gemini did not return narration."
                        )

                    if not visual_shots:

                        raise RuntimeError(
                            "Gemini did not return visual shots."
                        )

                    st.write(
                        f"🎙️ Narration received "
                        f"({len(narration.split())} words)."
                    )

                    st.write(
                        f"🎥 Visual shots: "
                        f"{len(visual_shots)}"
                    )

                    progress_placeholder = st.empty()

                    def long_progress(
                        current,
                        total,
                        message
                    ):

                        if total > 0:

                            progress_placeholder.progress(
                                min(
                                    current / total,
                                    1.0
                                ),
                                text=message
                            )

                    st.write(
                        "🎬 Downloading footage, "
                        "building visuals and adding narration..."
                    )

                    video_result = build_long_video(
                        narration=narration,
                        visual_shots=visual_shots,
                        voice=long_voice,
                        style=long_style,
                        thumbnail_text=plan.get("thumbnail_text", ""),
                        progress_callback=long_progress
                    )

                    if not video_result:

                        raise RuntimeError(
                            "Long-video builder returned no result."
                        )

                    final_video = video_result.get(
                        "video"
                    )

                    if not final_video:

                        raise RuntimeError(
                            "Long-video builder did not "
                            "return video data."
                        )

                    st.session_state[
                        "long_final_video"
                    ] = final_video

                    st.session_state[
                        "long_video_duration"
                    ] = video_result.get(
                        "duration"
                    )
                    st.session_state[
                        "long_thumbnail_path"
                    ] = video_result.get("thumbnail_path")

                    status.update(
                        label="🎉 Long video ready!",
                        state="complete"
                    )

                st.success(
                    "🎉 Your complete long video is ready!"
                )

            except Exception as e:

                st.error(
                    f"❌ Long-video generation failed: {e}"
                )

    # =====================================================
    # DISPLAY LONG PLAN
    # =====================================================

    if "long_plan" in st.session_state:

        plan = st.session_state[
            "long_plan"
        ]

        st.divider()

        st.markdown(
            "## 📝 Generated Documentary"
        )

        # -----------------------------------------------
        # TITLE
        # -----------------------------------------------

        if plan.get("title"):

            st.markdown(
                "### 📌 Title"
            )

            st.write(
                plan["title"]
            )

        # -----------------------------------------------
        # DESCRIPTION
        # -----------------------------------------------

        if plan.get("description"):

            st.markdown(
                "### 📄 Description"
            )

            st.write(
                plan["description"]
            )

        # -----------------------------------------------
        # CHAPTERS / TAGS / THUMBNAIL
        # -----------------------------------------------

        chapters = plan.get("chapters", [])
        if chapters:
            st.markdown("### ⏱️ Chapters")
            st.write("  ".join(f"{c.get('time','')} {c.get('title','')}" for c in chapters if isinstance(c, dict)))

        tags = plan.get("tags", [])
        if tags:
            st.markdown("### 🏷️ Tags")
            st.write(", ".join(str(x) for x in tags))

        if plan.get("thumbnail_text"):
            st.markdown("### 🖼️ Thumbnail concept")
            st.write(plan.get("thumbnail_text"))

        # -----------------------------------------------
        # NARRATION
        # -----------------------------------------------

        if plan.get("narration"):

            narration = plan[
                "narration"
            ]

            st.markdown(
                "### 🎙️ Narration"
            )

            st.caption(
                f"{len(narration.split())} words"
            )

            st.text_area(
                "Generated narration",
                value=narration,
                height=350,
                label_visibility="collapsed",
                key="long_narration_display"
            )

        # -----------------------------------------------
        # VISUAL SHOTS
        # -----------------------------------------------

        visual_shots = plan.get(
            "visual_shots",
            []
        )

        if visual_shots:

            st.markdown(
                f"### 🎥 Visual Plan "
                f"({len(visual_shots)} shots)"
            )

            for shot in visual_shots:

                shot_number = shot.get(
                    "shot_number",
                    ""
                )

                query = shot.get(
                    "search_query",
                    ""
                )

                caption = shot.get(
                    "caption",
                    ""
                )

                with st.expander(
                    f"Shot {shot_number} — {query}"
                ):

                    st.write(
                        f"**Search:** `{query}`"
                    )

                    st.write(
                        f"**Caption:** {caption}"
                    )

    # =====================================================
    # FINAL LONG VIDEO
    # =====================================================

    if "long_final_video" in st.session_state:

        st.divider()

        st.markdown(
            "## 🎬 Final Long Video"
        )

        duration = st.session_state.get(
            "long_video_duration"
        )

        if duration:

            minutes = int(
                duration // 60
            )

            seconds = int(
                duration % 60
            )

            st.success(
                f"Video duration: "
                f"{minutes}:{seconds:02d}"
            )

        final_video = st.session_state[
            "long_final_video"
        ]

        st.video(
            final_video
        )

        st.download_button(
            label="⬇️ Download Complete MP4",
            data=final_video,
            file_name="ai_long_video.mp4",
            mime="video/mp4",
            use_container_width=True,
            key="download_long_video"
        )

        thumbnail_path = st.session_state.get("long_thumbnail_path")
        if thumbnail_path and os.path.exists(thumbnail_path):
            st.markdown("### 🖼️ Generated Thumbnail")
            st.image(thumbnail_path, use_container_width=True)
            with open(thumbnail_path, "rb") as thumb_file:
                st.download_button(
                    "⬇️ Download Thumbnail",
                    data=thumb_file.read(),
                    file_name="youtube_thumbnail.jpg",
                    mime="image/jpeg",
                    key="download_long_thumbnail"
                )

        st.divider()
        st.markdown("### 🚀 Publish to YouTube")
        st.caption("The app will upload the generated video with the AI title, description, tags, chapters and thumbnail.")

        if is_connected():
            publish_mode = st.radio(
                "Publishing",
                ["Publish now", "Schedule"],
                horizontal=True,
                key="long_publish_mode"
            )
            publish_at = None
            if publish_mode == "Schedule":
                c1, c2 = st.columns(2)
                with c1:
                    schedule_date = st.date_input("Date", value=datetime.now().date() + timedelta(days=1), key="long_schedule_date")
                with c2:
                    schedule_time = st.time_input("Time", value=datetime.now().time().replace(second=0, microsecond=0), key="long_schedule_time")
                publish_at = datetime.combine(schedule_date, schedule_time, tzinfo=ZoneInfo("Asia/Kolkata"))

            upload_button = st.button("🚀 Upload Long Video to YouTube", type="primary", use_container_width=True, key="upload_long_youtube")
            if upload_button:
                try:
                    with st.spinner("Uploading video and setting thumbnail..."):
                        response = upload_long_video(
                            video_bytes=final_video,
                            title=plan.get("title", long_title),
                            description=plan.get("description", ""),
                            tags=plan.get("tags", []),
                            publish_at=publish_at,
                            category_id="27",
                            contains_synthetic_media=True,
                        )
                        video_id = response.get("id")
                        if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
                            set_thumbnail(video_id, thumbnail_path)
                    st.success("🎉 Uploaded successfully!")
                    if video_id:
                        st.markdown(f"[Open on YouTube]({video_url(video_id)})")
                except Exception as e:
                    st.error(f"❌ YouTube upload failed: {e}")
        else:
            st.info("YouTube is not connected yet.")
            if st.button("🔐 Connect YouTube", use_container_width=True, key="connect_long_youtube"):
                try:
                    connect_youtube()
                    st.success("YouTube connected. Generate/upload again from this section.")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ YouTube connection failed: {e}")


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "AI Video Generator • Gemini + real footage fallback + Pexels"
)