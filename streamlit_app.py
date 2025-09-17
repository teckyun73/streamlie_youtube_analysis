import os
from typing import Dict, List, Optional

import requests
import streamlit as st
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# ---- App Config ----
st.set_page_config(
    page_title="YouTube 인기 동영상",
    page_icon="▶️",
    layout="wide",
)

# API 키는 배포 호환성을 위해 Streamlit secrets에서만 읽습니다.
try:
    API_KEY = st.secrets["YOUTUBE_API_KEY"]  # secrets.toml이 없으면 예외 발생
except Exception:
    API_KEY = None
DEFAULT_REGION = "KR"  # Change to your preference
MAX_RESULTS = 30


def format_views(n: Optional[str]) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return "-"


def fetch_popular_videos(api_key: str, region: str, max_results: int) -> List[Dict]:
    """Fetch most popular videos using YouTube Data API v3.

    Returns a list of simplified dicts with keys:
    id, title, channel_title, channel_id, view_count, like_count, thumbnail_url, url
    """
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": region,
        "maxResults": max_results,
        "key": api_key,
    }

    resp = requests.get(url, params=params, timeout=15)
    if not resp.ok:
        # Try to extract error message
        try:
            err = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            err = resp.text
        raise RuntimeError(f"YouTube API 오류: {resp.status_code} - {err}")

    data = resp.json()
    items = data.get("items", [])
    results = []
    for it in items:
        vid = it.get("id")
        sn = it.get("snippet", {})
        stt = it.get("statistics", {})
        thumbs = sn.get("thumbnails", {})
        # pick a decent thumbnail available
        thumb = (
            thumbs.get("maxres")
            or thumbs.get("standard")
            or thumbs.get("high")
            or thumbs.get("medium")
            or thumbs.get("default")
            or {}
        )
        results.append(
            {
                "id": vid,
                "title": sn.get("title", "(제목 없음)"),
                "channel_title": sn.get("channelTitle", "(채널 없음)"),
                "channel_id": sn.get("channelId"),
                "view_count": stt.get("viewCount"),
                "like_count": stt.get("likeCount"),
                "thumbnail_url": thumb.get("url"),
                "url": f"https://www.youtube.com/watch?v={vid}" if vid else None,
            }
        )
    return results


def fetch_channel_subscribers(api_key: str, channel_ids: List[str]) -> Dict[str, Optional[str]]:
    """Fetch subscriberCount for given channel IDs. Returns {channel_id: subscriberCount(str or None)}.
    Batches into a single request (max 50 IDs supported by API).
    """
    # Clean and limit size defensively
    ids = [cid for cid in channel_ids if cid]
    if not ids:
        return {}
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "statistics",
        "id": ",".join(ids[:50]),
        "key": api_key,
    }
    resp = requests.get(url, params=params, timeout=15)
    if not resp.ok:
        try:
            err = resp.json().get("error", {}).get("message", resp.text)
        except Exception:
            err = resp.text
        # Return empty mapping on error to avoid breaking main list
        return {}
    data = resp.json()
    out: Dict[str, Optional[str]] = {}
    for it in data.get("items", []):
        cid = it.get("id")
        stats = it.get("statistics", {})
        out[cid] = stats.get("subscriberCount")
    return out


@st.cache_data(show_spinner=True, ttl=300)
def get_popular_cached(api_key: str, region: str, max_results: int) -> List[Dict]:
    return fetch_popular_videos(api_key, region, max_results)


@st.cache_data(show_spinner=False, ttl=300)
def get_channel_subscribers_cached(api_key: str, channel_ids_tuple: tuple) -> Dict[str, Optional[str]]:
    # channel_ids_tuple is hashable for cache; convert back to list
    return fetch_channel_subscribers(api_key, list(channel_ids_tuple))


# ---- UI ----
st.title("YouTube 인기 동영상 Top 30")
st.caption("간단한 Streamlit 앱 • 지역별 인기 영상 • 새로고침 가능")

with st.sidebar:
    st.subheader("설정")
    countries = [
        ("KR", "대한민국"),
        ("US", "미국"),
        ("JP", "일본"),
        ("GB", "영국"),
        ("DE", "독일"),
        ("FR", "프랑스"),
        ("IN", "인도"),
        ("BR", "브라질"),
        ("CA", "캐나다"),
        ("AU", "호주"),
    ]
    display_options = [f"{name} ({code})" for code, name in countries]
    selected_display = st.selectbox(
        "지역 코드 (국가명)",
        options=display_options,
        index=0,
        help="YouTube 인기 동영상을 조회할 국가 코드",
    )
    # Extract the code from the selected label: e.g., "대한민국 (KR)" -> KR
    region = selected_display.split("(")[-1].rstrip(")")
    refresh = st.button("🔄 새로고침")

# API Key validation
if not API_KEY or (isinstance(API_KEY, str) and not API_KEY.strip()):
    st.error(
        "YOUTUBE_API_KEY가 설정되지 않았습니다. 아래 경로 중 하나에 secrets.toml을 생성하고 키를 넣어주세요.\n\n"
        "- 로컬: `.streamlit/secrets.toml`\n"
        "- 사용자 경로: `~/.streamlit/secrets.toml`\n\n"
        "예시 (secrets.toml):\n"
        "`YOUTUBE_API_KEY = \"YOUR_YOUTUBE_DATA_API_KEY\"`"
    )
    st.stop()

# Handle manual refresh by clearing cache
if refresh:
    get_popular_cached.clear()
    get_channel_subscribers_cached.clear()

try:
    with st.spinner("인기 동영상 불러오는 중..."):
        videos = get_popular_cached(API_KEY, region, MAX_RESULTS)
        # Fetch channel subscriber counts in batch
        channel_ids = sorted({v.get("channel_id") for v in videos if v.get("channel_id")})
        subs_map = get_channel_subscribers_cached(API_KEY, tuple(channel_ids)) if channel_ids else {}
        for v in videos:
            v["subscriber_count"] = subs_map.get(v.get("channel_id"))
except Exception as e:
    st.error(f"데이터를 불러오는 중 오류가 발생했습니다: {e}")
    st.stop()

if not videos:
    st.warning("표시할 동영상이 없습니다.")
    st.stop()

# ---- List rendering ----
for idx, v in enumerate(videos, start=1):
    cols = st.columns([1, 5])
    with cols[0]:
        if v.get("thumbnail_url"):
            st.image(v["thumbnail_url"], use_container_width=True)
        else:
            st.write(":grey[미리보기 없음]")
    with cols[1]:
        title = v.get("title") or "(제목 없음)"
        url = v.get("url") or "#"
        st.markdown(f"**{idx}. [" + title.replace("(", "\\(").replace(")", "\\)") + f"]({url})**")
        st.write(f"채널: {v.get('channel_title') or '-'}")
        # 조회수, 좋아요, 구독자 표시
        views = format_views(v.get("view_count"))
        likes = format_views(v.get("like_count"))
        subs = format_views(v.get("subscriber_count"))
        st.write(f"조회수: {views} · 좋아요: {likes} · 구독자: {subs}명")
    st.divider()

st.success("완료! 최신 인기 동영상이 표시되었습니다.")
