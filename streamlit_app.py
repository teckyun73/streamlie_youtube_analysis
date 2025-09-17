import os
from typing import Dict, List, Optional

import requests
import streamlit as st
from dotenv import load_dotenv
import re
import csv
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta

# Load .env if present
load_dotenv()

# ---- App Config ----
st.set_page_config(
    page_title="YouTube 인기 동영상",
    page_icon="▶️",
    layout="wide",
)

# Rerun helper for Streamlit versions (st.rerun preferred, fallback to experimental)
def _rerun():
    try:
        # Streamlit >= 1.23
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# ---- Simple Auth (Intro Login) ----
ADMIN_ID = "admin"
ADMIN_PW = "$$teckyun73@@"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "role" not in st.session_state:
    st.session_state.role = None  # 'admin' or 'general'
if "user_name" not in st.session_state:
    st.session_state.user_name = None

def login_view():
    st.title("로그인")
    st.caption("인증 후 인기 동영상 대시보드가 표시됩니다.")

    tab_general, tab_admin = st.tabs(["일반인", "관리자"])

    with tab_general:
        st.subheader("일반인 섹션")
        st.info(
            "비밀번호는 YTB001 ~ YTB100 범위에서 입력하세요.\n\n"
            "예시: YTB001, YTB010, YTB042, YTB100"
        )
        with st.form("login_form_general", clear_on_submit=False):
            name = st.text_input("성명", value="", placeholder="예) 홍길동")
            pw = st.text_input("Password", value="", type="password", placeholder="예) YTB001")
            submitted_g = st.form_submit_button("로그인")
            if submitted_g:
                # Validate name
                if not name.strip():
                    st.error("성명을 입력하세요.")
                else:
                    # Validate password pattern YTB001..YTB100
                    m = re.fullmatch(r"YTB(\d{3})", pw.strip())
                    if not m:
                        st.error("Password 형식이 올바르지 않습니다. 예) YTB001 ~ YTB100")
                    else:
                        n = int(m.group(1))
                        if 1 <= n <= 100:
                            st.session_state.authenticated = True
                            st.session_state.role = "general"
                            st.session_state.user_name = name.strip()
                            # Start visit session
                            st.session_state.visit_start_ts = time.time()
                            st.session_state.visit_id = st.session_state.get("visit_id") or str(uuid.uuid4())
                            st.success("로그인 성공! 잠시만 기다려주세요…")
                            _rerun()
                        else:
                            st.error("Password 범위는 YTB001 ~ YTB100 입니다.")

    with tab_admin:
        st.subheader("관리자 섹션")
        with st.form("login_form_admin", clear_on_submit=False):
            user_id = st.text_input("ID", value="", autocomplete="username")
            user_pw = st.text_input("Password", value="", type="password", autocomplete="current-password")
            submitted_a = st.form_submit_button("로그인")
            if submitted_a:
                if user_id == ADMIN_ID and user_pw == ADMIN_PW:
                    st.session_state.authenticated = True
                    st.session_state.role = "admin"
                    st.session_state.user_name = user_id
                    st.success("로그인 성공! 잠시만 기다려주세요…")
                    _rerun()
                else:
                    st.error("ID 또는 Password가 올바르지 않습니다.")

# Gate: show login until authenticated
if not st.session_state.authenticated:
    login_view()
    st.stop()

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
    st.divider()
    # User info
    if st.session_state.get("authenticated"):
        who = st.session_state.get("user_name") or "사용자"
        role = st.session_state.get("role") or "general"
        # Duration for general users
        if role == "general" and st.session_state.get("visit_start_ts"):
            elapsed = int(time.time() - st.session_state.get("visit_start_ts"))
            st.caption(f"로그인: {who} ({role}) • 이용시간: {elapsed}s")
        else:
            st.caption(f"로그인: {who} ({role})")
    # Logout control
    if st.button("로그아웃"):
        # Finalize visit record for general users
        try:
            if st.session_state.get("role") == "general" and st.session_state.get("visit_start_ts"):
                start_ts = st.session_state.get("visit_start_ts")
                end_ts = time.time()
                duration = int(end_ts - start_ts)
                visit_id = st.session_state.get("visit_id") or str(uuid.uuid4())
                user = st.session_state.get("user_name") or "(unknown)"
                logs_dir = Path("logs")
                logs_dir.mkdir(exist_ok=True)
                visits_csv = logs_dir / "visits.csv"
                # Append a complete visit row
                row = {
                    "visit_id": visit_id,
                    "user_name": user,
                    "start_time": datetime.fromtimestamp(start_ts).isoformat(timespec="seconds"),
                    "end_time": datetime.fromtimestamp(end_ts).isoformat(timespec="seconds"),
                    "duration_sec": duration,
                }
                write_header = not visits_csv.exists()
                with visits_csv.open("a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                    if write_header:
                        writer.writeheader()
                    writer.writerow(row)
        except Exception:
            pass
        st.session_state.authenticated = False
        st.session_state.role = None
        st.session_state.user_name = None
        st.session_state.visit_start_ts = None
        st.session_state.visit_id = None
        _rerun()

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
        # Click logging button for general users
        if st.session_state.get("authenticated") and st.session_state.get("role") == "general" and url != "#":
            if st.button("보기", key=f"open_{v.get('id')}"):
                try:
                    logs_dir = Path("logs")
                    logs_dir.mkdir(exist_ok=True)
                    clicks_csv = logs_dir / "clicks.csv"
                    visit_id = st.session_state.get("visit_id") or str(uuid.uuid4())
                    if not st.session_state.get("visit_id"):
                        st.session_state.visit_id = visit_id
                    user = st.session_state.get("user_name") or "(unknown)"
                    row = {
                        "visit_id": visit_id,
                        "user_name": user,
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "video_id": v.get("id"),
                        "title": title,
                        "channel_title": v.get("channel_title"),
                        "url": url,
                    }
                    write_header = not clicks_csv.exists()
                    with clicks_csv.open("a", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                        if write_header:
                            writer.writeheader()
                        writer.writerow(row)
                    st.success("클릭이 기록되었습니다. 아래 링크를 눌러 새 탭에서 열 수 있습니다.")
                    st.markdown(f"[새 탭에서 열기]({url})")
                except Exception as _:
                    st.warning("클릭 기록 중 문제가 발생했습니다. 링크를 직접 클릭해 주세요.")
    st.divider()

st.success("완료! 최신 인기 동영상이 표시되었습니다.")

# ---- Admin Dashboard: 방문 이력/클릭 요약 ----
if st.session_state.get("authenticated") and st.session_state.get("role") == "admin":
    st.header("관리자 대시보드 : 일반인 방문 이력")
    logs_dir = Path("logs")
    visits_csv = logs_dir / "visits.csv"
    clicks_csv = logs_dir / "clicks.csv"

    # Load logs
    visits: List[Dict] = []
    clicks: List[Dict] = []
    if visits_csv.exists():
        with visits_csv.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            visits = list(reader)
    if clicks_csv.exists():
        with clicks_csv.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            clicks = list(reader)

    # Metrics
    total_visits = len(visits)
    unique_users = len({row.get("user_name") for row in visits}) if visits else 0
    total_clicks = len(clicks)
    # Average duration
    durations = [int(row.get("duration_sec") or 0) for row in visits if (row.get("duration_sec") or "").isdigit()]
    avg_duration = int(sum(durations) / len(durations)) if durations else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("총 방문 수", f"{total_visits}")
    m2.metric("유니크 사용자", f"{unique_users}")
    m3.metric("총 클릭 수", f"{total_clicks}")
    m4.metric("평균 방문 시간(초)", f"{avg_duration}")

    st.subheader("최근 방문")
    if visits:
        # Show last 20
        st.dataframe(visits[-20:])
        st.download_button("visits.csv 다운로드", data=visits_csv.read_bytes(), file_name="visits.csv", mime="text/csv")
    else:
        st.write("방문 데이터가 없습니다.")

    st.subheader("최근 클릭")
    if clicks:
        st.dataframe(clicks[-50:])
        st.download_button("clicks.csv 다운로드", data=clicks_csv.read_bytes(), file_name="clicks.csv", mime="text/csv")
    else:
        st.write("클릭 데이터가 없습니다.")
