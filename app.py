# app.py
import json
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

# -----------------------------
# Page Config
# -----------------------------
st.set_page_config(page_title="AI 습관 트래커", page_icon="📊", layout="wide")
st.title("📊 AI 습관 트래커")
st.caption("오늘의 습관 체크인 → 달성률/차트 → 날씨/강아지 + AI 코치 리포트 + 기분 맞춤 음악 추천!")

# -----------------------------
# Sidebar: API Keys
# -----------------------------
with st.sidebar:
    st.header("🔑 API 설정")
    openai_api_key = st.text_input("OpenAI API Key", type="password", help="예: sk-... (필수: AI 리포트 생성)")
    owm_api_key = st.text_input("OpenWeatherMap API Key", type="password", help="필수: 날씨 불러오기")

    st.divider()
    st.subheader("🎵 YouTube API (음악 추천)")
    yt_api_key = st.text_input(
        "YouTube Data API Key",
        type="password",
        help="YouTube Data API v3 키 (Search API 사용). 없으면 음악 추천은 비활성화됩니다.",
    )
    st.caption("Tip: 키는 세션에만 사용되며 저장되지 않아요.")

# -----------------------------
# Constants
# -----------------------------
HABITS = [
    ("기상 미션", "⏰"),
    ("물 마시기", "💧"),
    ("공부/독서", "📚"),
    ("운동하기", "🏃"),
    ("수면", "😴"),
]

# ✅ OpenWeatherMap 404/모호성 방지: “도시,KR”
CITY_OPTIONS = [
    ("Seoul", "Seoul,KR"),
    ("Busan", "Busan,KR"),
    ("Incheon", "Incheon,KR"),
    ("Daegu", "Daegu,KR"),
    ("Daejeon", "Daejeon,KR"),
    ("Gwangju", "Gwangju,KR"),
    ("Ulsan", "Ulsan,KR"),
    ("Suwon", "Suwon,KR"),
    ("Changwon", "Changwon,KR"),
    ("Jeju", "Jeju City,KR"),
]

COACH_STYLES = {
    "스파르타 코치": "엄격하고 직설적이며 행동을 강하게 요구하는 코치",
    "따뜻한 멘토": "다정하고 공감하며 작은 성취도 크게 칭찬하는 멘토",
    "게임 마스터": "RPG 퀘스트/레벨업 톤으로 재미있게 이끄는 게임 마스터",
}

# -----------------------------
# Session State Init
# -----------------------------
def _init_demo_history():
    """6일 샘플 데이터(데모) 생성"""
    today = datetime.now().date()
    base = []
    for i in range(6, 0, -1):
        d = today - timedelta(days=i)
        achieved = max(0, min(5, 1 + (i % 5)))
        mood = max(1, min(10, 6 + (2 - (i % 5))))
        base.append(
            {
                "date": d.isoformat(),
                "achieved": achieved,
                "rate": round(achieved / 5 * 100, 1),
                "mood": mood,
            }
        )
    return base


if "history" not in st.session_state:
    st.session_state["history"] = _init_demo_history()
if "latest_report" not in st.session_state:
    st.session_state["latest_report"] = None
if "latest_share_text" not in st.session_state:
    st.session_state["latest_share_text"] = None
if "latest_music" not in st.session_state:
    st.session_state["latest_music"] = None  # 추천 목록 저장

# -----------------------------
# API Helpers
# -----------------------------
def get_weather(city_query: str, api_key: str):
    """
    OpenWeatherMap에서 날씨 가져오기 (한국어, 섭씨)
    ✅ 실패 시 (None, 에러메시지) 반환 / timeout=10
    """
    if not city_query or not api_key:
        return None, "Missing city or API key"

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city_query, "appid": api_key.strip(), "units": "metric", "lang": "kr"}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            try:
                msg = r.json().get("message", "")
            except Exception:
                msg = (r.text or "")[:200]
            return None, f"HTTP {r.status_code}: {msg}"

        data = r.json()
        weather_desc = (data.get("weather") or [{}])[0].get("description")
        main = data.get("main", {}) or {}
        wind = data.get("wind", {}) or {}
        return (
            {
                "city": city_query,
                "description": weather_desc,
                "temp_c": main.get("temp"),
                "feels_like_c": main.get("feels_like"),
                "humidity": main.get("humidity"),
                "wind_ms": wind.get("speed"),
            },
            None,
        )
    except Exception as e:
        return None, f"Exception: {e}"


def _extract_breed_from_url(image_url: str):
    """Dog CEO 이미지 URL에서 품종 추정"""
    try:
        parts = image_url.split("/breeds/")[1].split("/")
        breed_part = parts[0].replace("-", " ")
        words = breed_part.split()
        if len(words) >= 2:
            return f"{words[1].title()} {words[0].title()}"
        return breed_part.title()
    except Exception:
        return "Unknown"


def get_dog_image():
    """Dog CEO에서 랜덤 강아지 사진 URL+품종 (실패 시 None), timeout=10"""
    url = "https://dog.ceo/api/breeds/image/random"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") != "success":
            return None
        image_url = data.get("message")
        if not image_url:
            return None
        return {"image_url": image_url, "breed": _extract_breed_from_url(image_url)}
    except Exception:
        return None


def _system_prompt_for_style(style: str) -> str:
    if style == "스파르타 코치":
        return (
            "너는 매우 엄격하고 직설적인 코치다. "
            "핑계를 허용하지 않고, 구체적 행동을 강하게 요구한다. "
            "짧고 임팩트 있게 말하되, 실천 가능한 지시를 반드시 포함해라."
        )
    if style == "게임 마스터":
        return (
            "너는 RPG 세계관의 게임 마스터다. "
            "사용자는 플레이어이며, 습관은 퀘스트/스탯/레벨업으로 표현한다. "
            "재미있고 몰입감 있게, 하지만 실제로 실행 가능한 조언을 제공해라."
        )
    return (
        "너는 따뜻하고 공감하는 멘토다. "
        "사용자의 노력과 감정을 인정하고, 작은 성취도 칭찬한다. "
        "부담 없는 다음 행동을 제안해라."
    )


# -----------------------------
# YouTube (Music Recommendation via YouTube Data API)
# -----------------------------
def _mood_to_music_queries(mood: int, weather: dict | None):
    """
    기분(1~10) + 날씨(옵션)를 바탕으로 검색 키워드 세트 생성
    """
    w = ""
    if weather and weather.get("description"):
        # 날씨가 비/눈/맑음 등일 때 감성 키워드 보정
        desc = str(weather.get("description"))
        if any(k in desc for k in ["비", "소나기", "장마", "우천"]):
            w = "비 오는 날 "
        elif any(k in desc for k in ["눈", "폭설"]):
            w = "눈 오는 날 "
        elif any(k in desc for k in ["맑", "쾌청"]):
            w = "맑은 날 "
        elif any(k in desc for k in ["흐림", "구름"]):
            w = "흐린 날 "

    # 기분 구간별 추천 결
    if mood <= 3:
        return [
            f"{w}위로되는 잔잔한 플레이리스트",
            f"{w}힐링 피아노 음악",
            f"{w}감성 발라드 플레이리스트",
        ]
    if mood <= 6:
        return [
            f"{w}집중 잘되는 로파이",
            f"{w}카페 음악 플레이리스트",
            f"{w}기분 전환 인디 팝",
        ]
    if mood <= 8:
        return [
            f"{w}신나는 K-POP 플레이리스트",
            f"{w}드라이브 음악 플레이리스트",
            f"{w}리듬 좋은 팝 플레이리스트",
        ]
    return [
        f"{w}파티 EDM 플레이리스트",
        f"{w}하이텐션 운동 음악",
        f"{w}댄스 음악 플레이리스트",
    ]


def get_youtube_music_recommendations(mood: int, api_key: str, weather: dict | None = None, max_results: int = 5):
    """
    YouTube Data API v3 검색으로 '음악 추천' 리스트를 가져옵니다.
    - 실패 시 (None, err) 반환
    - timeout=10
    반환 형식: [{"title":..., "channel":..., "video_url":..., "thumb":...}, ...]
    """
    if not api_key:
        return None, "YouTube API Key가 없어요."

    queries = _mood_to_music_queries(mood, weather)

    # 여러 쿼리를 시도해서 결과를 채움(중복은 제거)
    collected = []
    seen_ids = set()

    base_url = "https://www.googleapis.com/youtube/v3/search"

    try:
        for q in queries:
            if len(collected) >= max_results:
                break
            params = {
                "part": "snippet",
                "q": q,
                "type": "video",
                "maxResults": 5,
                "key": api_key.strip(),
                "safeSearch": "strict",
                "relevanceLanguage": "ko",
                "videoEmbeddable": "true",
            }
            r = requests.get(base_url, params=params, timeout=10)
            if r.status_code != 200:
                # 키 문제(401/403)면 즉시 종료하는 게 낫다
                try:
                    msg = r.json()
                except Exception:
                    msg = (r.text or "")[:200]
                return None, f"HTTP {r.status_code}: {msg}"

            data = r.json()
            for item in data.get("items", []):
                vid = (item.get("id") or {}).get("videoId")
                if not vid or vid in seen_ids:
                    continue
                sn = item.get("snippet") or {}
                title = sn.get("title", "Untitled")
                channel = sn.get("channelTitle", "")
                thumb = ((sn.get("thumbnails") or {}).get("high") or {}).get("url")
                collected.append(
                    {
                        "title": title,
                        "channel": channel,
                        "video_url": f"https://www.youtube.com/watch?v={vid}",
                        "thumbnail": thumb,
                        "query_hint": q,
                    }
                )
                seen_ids.add(vid)
                if len(collected) >= max_results:
                    break

        if not collected:
            return None, "검색 결과가 없어요. (키/쿼터/검색어 문제일 수 있어요)"
        return collected[:max_results], None

    except Exception as e:
        return None, f"Exception: {e}"


# -----------------------------
# OpenAI (Coach Report)
# -----------------------------
def generate_report(
    openai_key: str,
    coach_style: str,
    habits_checked: dict,
    mood: int,
    weather: dict | None,
    dog: dict | None,
    music_list: list | None,
):
    """
    습관+기분+날씨+강아지 품종(+음악 추천 요약)을 모아서 OpenAI에 전달
    - 모델: gpt-5-mini
    - 출력 형식:
      컨디션 등급(S~D), 습관 분석, 날씨 코멘트, 내일 미션, 오늘의 한마디
    """
    if not openai_key:
        return None, "OpenAI API Key가 필요해요."

    habit_lines = []
    for name, emoji in HABITS:
        ok = habits_checked.get(name, False)
        habit_lines.append(f"- {emoji} {name}: {'완료' if ok else '미완료'}")

    achieved = sum(1 for v in habits_checked.values() if v)
    rate = achieved / 5 * 100

    weather_text = "날씨 정보 없음"
    if weather:
        weather_text = (
            f"{weather.get('city')} / {weather.get('description')} / "
            f"{weather.get('temp_c')}°C (체감 {weather.get('feels_like_c')}°C) / "
            f"습도 {weather.get('humidity')}% / 바람 {weather.get('wind_ms')}m/s"
        )

    dog_text = "강아지 정보 없음"
    if dog:
        dog_text = f"{dog.get('breed')} (이미지 URL 제공됨)"

    music_text = "음악 추천 없음"
    if music_list:
        top3 = music_list[:3]
        music_text = "\n".join([f"- {m['title']} ({m.get('channel','')})" for m in top3])

    system_prompt = _system_prompt_for_style(coach_style)

    user_prompt = f"""
[오늘 체크인 요약]
달성률: {rate:.0f}%
완료 습관 수: {achieved}/5
기분(1~10): {mood}

[습관 상세]
{chr(10).join(habit_lines)}

[날씨]
{weather_text}

[오늘의 랜덤 강아지]
{dog_text}

[오늘의 음악 추천(참고)]
{music_text}

[출력 형식 - 반드시 아래 섹션 제목 그대로 출력]
컨디션 등급: (S/A/B/C/D 중 하나)
습관 분석: (2~5줄, 핵심만)
날씨 코멘트: (1~2줄)
내일 미션: (불릿 3개)
오늘의 한마디: (한 문장)
""".strip()

    try:
        from openai import OpenAI

        client = OpenAI(api_key=openai_key.strip())

        # Responses API 우선
        try:
            resp = client.responses.create(
                model="gpt-5-mini",
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            text = getattr(resp, "output_text", None)
            if not text:
                text = str(resp)
            return text, None
        except Exception:
            # Chat Completions fallback
            chat = client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return chat.choices[0].message.content, None

    except Exception as e:
        return None, f"OpenAI 호출 실패: {e}"


# -----------------------------
# Habit Check-in UI
# -----------------------------
st.subheader("✅ 오늘의 습관 체크인")

colA, colB = st.columns([1.2, 1])

with colA:
    st.markdown("**습관 체크** (5개, 2열)")
    c1, c2 = st.columns(2)
    checked = {}

    for idx, (name, emoji) in enumerate(HABITS):
        target_col = c1 if idx % 2 == 0 else c2
        with target_col:
            checked[name] = st.checkbox(f"{emoji} {name}", value=False, key=f"habit_{name}")

    mood = st.slider("🙂 오늘 기분은 어때요?", min_value=1, max_value=10, value=6, step=1)

with colB:
    st.markdown("**환경 설정**")
    city_label = st.selectbox("🏙️ 도시 선택", [c[0] for c in CITY_OPTIONS], index=0)
    city_query = dict(CITY_OPTIONS)[city_label]
    coach_style = st.radio("🎭 코치 스타일", list(COACH_STYLES.keys()), index=1)
    st.caption(f"설명: {COACH_STYLES[coach_style]}")

# -----------------------------
# Metrics
# -----------------------------
achieved_cnt = sum(1 for v in checked.values() if v)
rate_pct = round(achieved_cnt / 5 * 100, 1)

m1, m2, m3 = st.columns(3)
m1.metric("달성률", f"{rate_pct}%")
m2.metric("달성 습관", f"{achieved_cnt}/5")
m3.metric("기분", f"{mood}/10")

# -----------------------------
# 7-day Chart (6 demo + today)
# -----------------------------
st.subheader("📈 최근 7일 달성률")

today_iso = datetime.now().date().isoformat()

chart_rows = [r for r in st.session_state["history"] if r.get("date") != today_iso]
chart_rows = chart_rows[-6:]
chart_rows.append({"date": today_iso, "achieved": achieved_cnt, "rate": float(rate_pct), "mood": mood})

df = pd.DataFrame(chart_rows)
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date")
st.bar_chart(df.set_index("date")[["rate"]])

# -----------------------------
# Music Recommendation (YouTube)
# -----------------------------
st.subheader("🎵 기분 맞춤 음악 추천 (YouTube)")

music_btn_col1, music_btn_col2 = st.columns([1, 3])
with music_btn_col1:
    music_btn = st.button("음악 추천 받기", use_container_width=True)
with music_btn_col2:
    st.caption("YouTube Data API Key가 있으면, 기분/날씨에 맞춰 검색 기반으로 음악(영상) 링크를 추천해요.")

# 미리보기: 날씨는 음악 추천에도 참고되므로, 버튼 누르면 같이 가져오도록
if music_btn:
    weather_for_music, weather_err_for_music = get_weather(city_query, owm_api_key)
    with st.spinner("오늘 기분에 맞는 음악을 찾는 중..."):
        music_list, music_err = get_youtube_music_recommendations(
            mood=mood,
            api_key=yt_api_key,
            weather=weather_for_music,
            max_results=5,
        )
    if music_err:
        st.warning("음악 추천을 가져오지 못했어요.")
        st.caption(f"원인: {music_err}")
        st.session_state["latest_music"] = None
    else:
        st.success("음악 추천 완료!")
        st.session_state["latest_music"] = music_list

# 표시 (최근 추천 유지)
music_list_to_show = st.session_state.get("latest_music")
if not yt_api_key:
    st.info("YouTube Data API Key를 사이드바에 넣으면 음악 추천 기능이 활성화돼요.")
elif music_list_to_show:
    cols = st.columns(2)
    for i, m in enumerate(music_list_to_show):
        with cols[i % 2]:
            st.markdown(f"**{i+1}. {m['title']}**")
            if m.get("channel"):
                st.caption(f"채널: {m['channel']}")
            # Streamlit은 유튜브 URL을 st.video로 임베드 가능
            st.video(m["video_url"])
            if m.get("query_hint"):
                st.caption(f"검색 힌트: {m['query_hint']}")
else:
    st.caption("아직 추천이 없어요. 위에서 '음악 추천 받기'를 눌러보세요.")

# -----------------------------
# Generate Report
# -----------------------------
st.subheader("🧠 AI 코치 리포트")

btn = st.button("컨디션 리포트 생성", type="primary")

if btn:
    # Save today's record into history (session_state)
    new_row = {
        "date": today_iso,
        "achieved": achieved_cnt,
        "rate": float(rate_pct),
        "mood": mood,
    }
    hist = [r for r in st.session_state["history"] if r.get("date") != today_iso]
    hist.append(new_row)
    hist = sorted(hist, key=lambda x: x["date"])[-14:]
    st.session_state["history"] = hist

    # Fetch APIs
    weather, weather_err = get_weather(city_query, owm_api_key)
    dog = get_dog_image()

    # Music: 이미 받아둔 것이 있으면 사용, 없으면(키가 있을 때만) 자동으로 한 번 시도
    music_list = st.session_state.get("latest_music")
    music_auto_err = None
    if yt_api_key and not music_list:
        music_list, music_auto_err = get_youtube_music_recommendations(
            mood=mood, api_key=yt_api_key, weather=weather, max_results=5
        )
        if music_list:
            st.session_state["latest_music"] = music_list

    # Generate AI report
    with st.spinner("AI 코치가 리포트를 작성 중..."):
        report, err = generate_report(
            openai_key=openai_api_key,
            coach_style=coach_style,
            habits_checked=checked,
            mood=mood,
            weather=weather,
            dog=dog,
            music_list=music_list,
        )

    if err:
        st.error(err)
    else:
        st.success("리포트 생성 완료!")

    # Render cards (weather + dog)
    left, right = st.columns(2)

    with left:
        st.markdown("### 🌦️ 오늘의 날씨")
        if weather:
            st.info(
                f"**{city_label}**  (`{weather.get('city')}`)\n\n"
                f"- 상태: {weather.get('description')}\n"
                f"- 기온: {weather.get('temp_c')}°C (체감 {weather.get('feels_like_c')}°C)\n"
                f"- 습도: {weather.get('humidity')}%\n"
                f"- 바람: {weather.get('wind_ms')} m/s"
            )
        else:
            st.warning("날씨 정보를 불러오지 못했어요.")
            if weather_err:
                st.caption(f"원인: {weather_err}")

    with right:
        st.markdown("### 🐶 오늘의 강아지 카드")
        if dog:
            st.image(dog["image_url"], caption=f"품종: {dog.get('breed')}", use_container_width=True)
        else:
            st.warning("강아지 이미지를 불러오지 못했어요.")

    # Music card (optional)
    st.markdown("### 🎵 오늘의 음악 추천")
    if not yt_api_key:
        st.info("YouTube Data API Key가 없어서 음악 추천을 건너뛰었어요.")
    elif music_list:
        # 상위 3개만 깔끔하게 노출
        top = music_list[:3]
        mc1, mc2, mc3 = st.columns(3)
        mcols = [mc1, mc2, mc3]
        for i, m in enumerate(top):
            with mcols[i]:
                st.markdown(f"**{i+1}. {m['title']}**")
                st.caption(m.get("channel", ""))
                st.video(m["video_url"])
    else:
        st.warning("음악 추천을 가져오지 못했어요.")
        if music_auto_err:
            st.caption(f"원인: {music_auto_err}")

    # Report display
    st.markdown("### 🧾 AI 코치 리포트")
    if report:
        st.write(report)

    # Share text
    share_payload = {
        "date": today_iso,
        "city": city_label,
        "city_query": city_query,
        "coach_style": coach_style,
        "rate_percent": rate_pct,
        "achieved": f"{achieved_cnt}/5",
        "mood": mood,
        "weather": weather,
        "weather_error": weather_err,
        "dog": dog,
        "music": (music_list[:5] if music_list else None),
        "report": report,
    }
    share_text = (
        f"[AI 습관 트래커 공유]\n"
        f"- 날짜: {today_iso}\n"
        f"- 도시: {city_label} ({city_query})\n"
        f"- 코치: {coach_style}\n"
        f"- 달성률: {rate_pct}% ({achieved_cnt}/5)\n"
        f"- 기분: {mood}/10\n\n"
        f"[음악 추천]\n"
        + (
            "\n".join([f"- {m['title']} ({m.get('channel','')}) {m['video_url']}" for m in (music_list[:3] if music_list else [])])
            if music_list
            else "(없음)"
        )
        + "\n\n"
        f"[리포트]\n{report or '(리포트 없음)'}\n\n"
        f"[원본 데이터(JSON)]\n{json.dumps(share_payload, ensure_ascii=False, indent=2)}"
    )
    st.session_state["latest_report"] = report
    st.session_state["latest_share_text"] = share_text

# If already generated earlier, show share text
if st.session_state.get("latest_share_text"):
    st.markdown("### 🔗 공유용 텍스트")
    st.code(st.session_state["latest_share_text"], language="text")

# -----------------------------
# Footer: API 안내
# -----------------------------
with st.expander("📌 API 안내 / 준비물"):
    st.markdown(
        """
**1) OpenAI API Key**
- AI 코치 리포트 생성에 필요해요.

**2) OpenWeatherMap API Key**
- 날씨 카드에 필요해요.
- 호출 옵션: `units=metric`(섭씨), `lang=kr`(한국어)
- 이 앱은 도시를 `Seoul,KR`처럼 국가코드를 붙여 요청합니다(404/모호성 방지).

**3) Dog CEO (무료, 키 불필요)**
- 랜덤 강아지 이미지를 가져옵니다.

**4) YouTube Data API Key (음악 추천)**
- *YouTube Music 전용 공식 API는 일반적으로 공개/권장되지 않아*, 실용적으로는 **YouTube Data API v3 검색**으로 음악(영상/플레이리스트)을 추천합니다.
- 기능 사용: Google Cloud Console → YouTube Data API v3 활성화 → API Key 발급
- 에러가 뜨면 보통 `HTTP 403(쿼터/권한)` 또는 `HTTP 400/401(키)`입니다.

**오류가 날 때**
- 날씨가 안 나오면 “원인: HTTP 401/404/429 …” 메시지를 확인해 주세요.
- 음악이 안 나오면 “원인: HTTP 403 …” (쿼터/권한) 여부를 확인해 주세요.
"""
    )
