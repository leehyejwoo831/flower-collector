import os
import json
import re
import time
import random
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from bs4 import BeautifulSoup

st.set_page_config(page_title="전국 화원사 수집기", page_icon="🌷", layout="wide")
st.title("📊 전국 화원사 자동 수집기 (고정밀 모드)")

# 서버 환경 변수에서 API 키 불러오기
NCP_CLIENT_ID = os.getenv("NCP_CLIENT_ID", "")
NCP_CLIENT_SECRET = os.getenv("NCP_CLIENT_SECRET", "")

with st.sidebar:
    st.header("🔒 보안 & 필터링 설정")
    if NCP_CLIENT_ID and NCP_CLIENT_SECRET:
        st.success("✅ 서버 API 인증 키 정상 연결")
    else:
        st.error("⚠️ 서버 환경 변수를 확인해 주세요.")
    st.markdown("---")
    st.info("💡 네이버 지도 API + 플레이스 전용 파싱으로 전화번호 수집 정밀도가 강화되었습니다.")

col1, col2 = st.columns([1, 1])

with col1:
    target_region = st.text_input(
        "수집할 지역 입력 (필수)", 
        value="", 
        placeholder="예: 제주 서귀포시, 대구 동구, 단양군"
    )

with col2:
    custom_sub_locations = st.text_input(
        "세부 동,읍,면,리 이름 (선택 - 쉼표 구분)", 
        value="", 
        placeholder="예: 동천동, 산격동, 침산동"
    )

UNIVERSAL_KEYWORDS = [
    "꽃집", "화원", "플라워", "꽃배달", "난원", "분재", 
    "식물원", "가드닝", "꽃농원", "화분", "생화", "플라워샵"
]

ALLOWED_CATEGORIES = [
    "꽃", "화원", "플라워", "원예", "화훼", "난원", "분재", 
    "가드닝", "식물", "조경", "농원", "화분", "종묘"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36"
]

# 광역지자체/지역별 유효 지역번호 매핑
REGION_AREA_CODE_MAP = {
    "서울": "02",
    "경기": "031", "인천": "032", "강원": "033",
    "충북": "043", "충남": "041", "대전": "042", "세종": "044",
    "경북": "054", "경남": "055", "대구": "053", "부산": "051", "울산": "052",
    "전북": "063", "전남": "061", "광주": "062",
    "제주": "064"
}

def clean_html(text):
    if not text:
        return ""
    cleaned = re.sub(r"<.*?>", "", text)
    return (
        cleaned.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )

def get_allowed_area_codes_for_region(region_str):
    allowed = ["010", "011", "016", "017", "018", "019", "0507", "0502", "0503", "0504", "0505", "0506", "0508", "15", "16", "18"]
    for reg, code in REGION_AREA_CODE_MAP.items():
        if reg in region_str:
            allowed.append(code)
            
    if len(allowed) == 16:
        allowed.extend(list(REGION_AREA_CODE_MAP.values()))
        
    return tuple(allowed)

def format_phone_number(raw, allowed_codes=None):
    if not raw:
        return ""
    nums = re.sub(r"[^\d]", "", str(raw))
    
    if allowed_codes:
        if not any(nums.startswith(code) for code in allowed_codes):
            return ""

    if nums.startswith("050") and len(nums) == 12:
        return f"{nums[:4]}-{nums[4:8]}-{nums[8:]}"
    elif nums.startswith("050") and len(nums) == 11:
        return f"{nums[:4]}-{nums[4:7]}-{nums[7:]}"
    elif nums.startswith("02"):
        if len(nums) == 9:
            return f"{nums[:2]}-{nums[2:5]}-{nums[5:]}"
        elif len(nums) == 10:
            return f"{nums[:2]}-{nums[2:6]}-{nums[6:]}"
    elif len(nums) == 10:
        return f"{nums[:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}-{nums[3:7]}-{nums[7:]}"
    elif len(nums) == 8 and (nums.startswith("15") or nums.startswith("16") or nums.startswith("18")):
        return f"{nums[:4]}-{nums[4:]}"
        
    return raw

def is_strict_region_match(road_addr, jibun_addr, target_region_str):
    if not target_region_str.strip():
        return True
        
    target_tokens = [t.strip() for t in target_region_str.strip().split() if t.strip()]
    full_road = road_addr or ""
    full_jibun = jibun_addr or ""
    
    road_tokens = full_road.split()
    jibun_tokens = full_jibun.split()
    
    def match_single_target(target, tokens, full_text):
        if any(target.endswith(unit) for unit in ["구", "군", "읍", "면", "동", "리"]):
            return any(token == target for token in tokens)
        return target in full_text

    def check_all_targets(tokens, full_text):
        for target in target_tokens:
            if not match_single_target(target, tokens, full_text):
                return False
        return True

    return check_all_targets(road_tokens, full_road) or check_all_targets(jibun_tokens, full_jibun)

def is_valid_flower_category(category_str):
    if not category_str:
        return True
    return any(keyword in category_str for keyword in ALLOWED_CATEGORIES)

def fetch_phone_via_map_api(title, address, allowed_codes):
    """1순위: 네이버 지도 API 우회를 통한 정확한 전화번호 추출"""
    addr_tokens = [t.strip() for t in address.split() if t.strip()]
    short_addr = " ".join(addr_tokens[:3]) if len(addr_tokens) >= 3 else address
    search_kw = f"{short_addr} {title}"

    url = f"https://map.naver.com/p/api/search/instant?coords=37.5665,126.9780&query={requests.utils.quote(search_kw)}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://map.naver.com/"
    }
    try:
        time.sleep(random.uniform(0.8, 1.5))  # 차단 방지를 위한 충분한 딜레이
        res = requests.get(url, headers=headers, timeout=3)
        if res.status_code == 200:
            data = res.json()
            place_list = data.get("place", [])
            if place_list:
                tel = place_list[0].get("tel", "")
                formatted = format_phone_number(tel, allowed_codes)
                if formatted:
                    return formatted
    except Exception:
        pass
    return ""

def fetch_phone_from_mobile(title, address, allowed_codes):
    """2순위: 네이버 모바일 웹 BeautifulSoup 카드 전용 추출"""
    if not address:
        return ""

    addr_tokens = [t.strip() for t in address.split() if t.strip()]
    short_addr = " ".join(addr_tokens[:3]) if len(addr_tokens) >= 3 else address
    search_kw = f"{short_addr} {title}"

    url = f"https://m.search.naver.com/search.naver?query={requests.utils.quote(search_kw)}"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    try:
        time.sleep(random.uniform(1.2, 2.0))  # 봇 감지 우회용 딜레이
        res = requests.get(url, headers=headers, timeout=4)
        if res.status_code == 200:
            html = res.text
            
            # 봇 감지/캡차 페이지 검증
            if "captcha" in html.lower() or "비정상적인 접근" in html:
                time.sleep(3)
                return ""

            # 주소 연고성 검증
            region_keywords = [
                t for t in addr_tokens 
                if len(t) >= 2 and t not in ["대한민국", "서울특별시", "광역시", "특별자치시", "도"]
            ]
            if region_keywords and not any(kw in html for kw in region_keywords):
                return ""

            soup = BeautifulSoup(html, "html.parser")
            
            # 플레이스 관련 주요 영역 컨테이너만 지정
            place_cards = soup.select(".api_subject_bx, .place_section, .composite_card, ._list_item")
            target_text = " ".join([card.get_text() for card in place_cards]) if place_cards else html

            # 1. tel: 태그 우선 추출
            tel_links = re.findall(r'href=["\']tel:([0-9\-\.]+)', html)
            for t in tel_links:
                formatted = format_phone_number(t, allowed_codes)
                if formatted:
                    return formatted

            # 2. 플레이스 본문 영역 내 전화번호 패턴 추출
            pattern = r"(050\d|02|0[3-9]\d|01[016789])[-.)\s]?(\d{3,4})[-.)\s]?(\d{4})"
            matches = re.findall(pattern, target_text)
            for match in matches:
                raw_found = f"{match[0]}-{match[1]}-{match[2]}"
                formatted = format_phone_number(raw_found, allowed_codes)
                if formatted:
                    return formatted
                    
        elif res.status_code == 429:
            time.sleep(3)
    except Exception:
        pass
    return ""

def get_phone_number_smart(title, address, allowed_codes):
    """지도의 API 방식과 모바일 크롤링 방식을 결합한 하이브리드 수집"""
    # 1. 지도 API 우선 시도
    tel = fetch_phone_via_map_api(title, address, allowed_codes)
    if tel:
        return tel
    # 2. 실패 시 모바일 웹 플레이스 카드 파싱 시도
    return fetch_phone_from_mobile(title, address, allowed_codes)

def build_dynamic_queries(region_str, sub_locs_str):
    clean_region = region_str.strip()
    queries = []
    
    for kw in UNIVERSAL_KEYWORDS:
        queries.append(f"{clean_region} {kw}")
        
    if sub_locs_str.strip():
        sub_list = [s.strip() for s in sub_locs_str.split(",") if s.strip()]
        for sub in sub_list:
            for kw in ["꽃집", "화원", "플라워"]:
                queries.append(f"{clean_region} {sub} {kw}")
                
    return list(set(queries))

if st.button("🚀 선택 지역 전수 수집 시작", type="primary", use_container_width=True):
    if not NCP_CLIENT_ID or not NCP_CLIENT_SECRET:
        st.error("⚠️ 서버 환경 변수에 Naver API 키가 설정되지 않았습니다.")
    elif not target_region.strip():
        st.warning("⚠️ 수집할 지역을 입력하세요.")
    else:
        allowed_codes = get_allowed_area_codes_for_region(target_region)
        
        url = "https://naverapihub.apigw.ntruss.com/search/v1/local"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NCP_CLIENT_ID.strip(),
            "X-NCP-APIGW-API-KEY": NCP_CLIENT_SECRET.strip(),
        }

        sub_queries = build_dynamic_queries(target_region, custom_sub_locations)
        shops = []
        seen = set()

        st.write(f"🔍 **'{target_region}'** 정밀 데이터 수집 중...")
        progress_bar = st.progress(0)
        status_text = st.empty()

        for idx, q in enumerate(sub_queries):
            status_text.text(f"[{idx+1}/{len(sub_queries)}] '{q}' 검색 중... (수집: {len(shops)}개)")
            
            params = {"query": q, "display": 5, "start": 1, "sort": "comment"}
            try:
                res = requests.get(url, headers=headers, params=params, timeout=5)
                if res.status_code == 200:
                    items = res.json().get("items", [])
                    for item in items:
                        title = clean_html(item.get("title", ""))
                        road_addr = item.get("roadAddress", "")
                        jibun_addr = item.get("address", "")
                        tel = format_phone_number(clean_html(item.get("telephone", "")).strip(), allowed_codes)
                        category = item.get("category", "")

                        target_addr = road_addr if road_addr else jibun_addr
                        key = (title, target_addr)

                        if key in seen:
                            continue
                        seen.add(key)

                        if not is_strict_region_match(road_addr, jibun_addr, target_region):
                            continue

                        if not is_valid_flower_category(category):
                            continue

                        shops.append({
                            "상호명": title,
                            "전화번호": tel,
                            "도로명주소": target_addr,
                            "지번주소": jibun_addr
                        })
                elif res.status_code == 429:
                    time.sleep(1)
            except Exception:
                pass
            
            time.sleep(0.15)
            progress_bar.progress((idx + 1) / len(sub_queries))

        status_text.empty()
        progress_bar.empty()

        # 전화번호 누락 매장 추가 정밀 추적 (지도 API + 카드 파싱)
        missing_shops = [s for s in shops if not s["전화번호"]]
        if missing_shops:
            st.info(f"📞 전화번호 누락 매장 {len(missing_shops)}개 추가 정밀 추적 중...")
            phone_bar = st.progress(0)
            for p_idx, shop in enumerate(missing_shops):
                found_tel = get_phone_number_smart(shop["상호명"], shop["도로명주소"], allowed_codes)
                if found_tel:
                    shop["전화번호"] = found_tel
                phone_bar.progress((p_idx + 1) / len(missing_shops))
            phone_bar.empty()

        # 전화번호가 최종적으로 존재하는 유효 항목만 구성
        final_shops = [s for s in shops if s["전화번호"]]

        if final_shops:
            formatted_data = []
            for item in final_shops:
                phone = item["전화번호"]
                is_mobile = phone.startswith("010")
                
                formatted_data.append({
                    "상호명": item["상호명"],
                    "휴대폰(010)": phone if is_mobile else "",
                    "전화번호(0507/지역번호/매장번호)": phone if not is_mobile else "",
                    "도로명주소": item["도로명주소"]
                })

            df = pd.DataFrame(formatted_data)
            st.success(f"🎉 **{target_region}** 총 **{len(df)}개** 유효 매장 수집 완료!")

            tab1, tab2 = st.tabs(["📊 화면 표 보기", "📋 1초 전체 복사하기"])

            with tab1:
                st.dataframe(df, use_container_width=True, height=450)

            with tab2:
                tsv_text = df.to_csv(sep="\t", index=False)
                tsv_json = json.dumps(tsv_text)
                
                copy_code = f"""
                    <script>
                    function copyToClipboard() {{
                        const text = {tsv_json};
                        navigator.clipboard.writeText(text).then(function() {{
                            alert('📋 지정된 4개 컬럼 데이터가 복사되었습니다!\\n엑셀에 바로 붙여넣기(Ctrl+V)하세요.');
                        }});
                    }}
                    </script>
                    <button onclick="copyToClipboard()" style="
                        background-color: #ff4b4b;
                        color: white;
                        padding: 12px 24px;
                        font-size: 16px;
                        font-weight: bold;
                        border: none;
                        border-radius: 8px;
                        cursor: pointer;
                        width: 100%;
                        margin-bottom: 12px;
                    ">📋 전체 데이터 1초 복사하기 (클릭)</button>
                """
                components.html(copy_code, height=70)

                st.caption("👇 또는 아래 상자 우측 상단의 복사 버튼(📋)을 클릭하세요.")
                st.code(tsv_text, language="text")
        else:
            st.error(f"'{target_region}'에서 유효한 전화번호가 있는 화원 매장을 찾지 못했습니다.")    custom_sub_locations = st.text_input(
        "세부 동,읍,면,리 이름 (선택 - 쉼표 구분)", 
        value="", 
        placeholder="예: 동천동, 산격동, 침산동"
    )

UNIVERSAL_KEYWORDS = [
    "꽃집", "화원", "플라워", "꽃배달", "난원", "분재", 
    "식물원", "가드닝", "꽃농원", "화분", "생화", "플라워샵"
]

ALLOWED_CATEGORIES = [
    "꽃", "화원", "플라워", "원예", "화훼", "난원", "분재", 
    "가드닝", "식물", "조경", "농원", "화분", "종묘"
]

USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/101.0.4951.44 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36"
]

# 광역지자체/지역별 유효 지역번호 매핑
REGION_AREA_CODE_MAP = {
    "서울": "02",
    "경기": "031", "인천": "032", "강원": "033",
    "충북": "043", "충남": "041", "대전": "042", "세종": "044",
    "경북": "054", "경남": "055", "대구": "053", "부산": "051", "울산": "052",
    "전북": "063", "전남": "061", "광주": "062",
    "제주": "064"
}

def clean_html(text):
    if not text:
        return ""
    cleaned = re.sub(r"<.*?>", "", text)
    return (
        cleaned.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )

def get_allowed_area_codes_for_region(region_str):
    """입력된 지역 문자열을 바탕으로 해당 지역에서 허용 가능한 전화번호 서두(지역번호/휴대폰/안심번호) 목록 추출"""
    allowed = ["010", "011", "016", "017", "018", "019", "0507", "0502", "0503", "0504", "0505", "0506", "0508", "15", "16", "18"]
    
    # 입력된 지역명에서 해당 도/광역시 지역번호 찾기
    for reg, code in REGION_AREA_CODE_MAP.items():
        if reg in region_str:
            allowed.append(code)
            
    # 특정 지역 식별이 안 될 경우 전국 지역번호 전체 허용 (Fallback)
    if len(allowed) == 16:
        allowed.extend(list(REGION_AREA_CODE_MAP.values()))
        
    return tuple(allowed)

def format_phone_number(raw, allowed_codes=None):
    if not raw:
        return ""
    nums = re.sub(r"[^\d]", "", str(raw))
    
    # 💡 허용된 지역번호/국번이 아니면 차단 (타 지역 번호 유입 차단 핵심)
    if allowed_codes:
        if not any(nums.startswith(code) for code in allowed_codes):
            return ""

    if nums.startswith("050") and len(nums) == 12:
        return f"{nums[:4]}-{nums[4:8]}-{nums[8:]}"
    elif nums.startswith("050") and len(nums) == 11:
        return f"{nums[:4]}-{nums[4:7]}-{nums[7:]}"
    elif nums.startswith("02"):
        if len(nums) == 9:
            return f"{nums[:2]}-{nums[2:5]}-{nums[5:]}"
        elif len(nums) == 10:
            return f"{nums[:2]}-{nums[2:6]}-{nums[6:]}"
    elif len(nums) == 10:
        return f"{nums[:3]}-{nums[3:6]}-{nums[6:]}"
    elif len(nums) == 11:
        return f"{nums[:3]}-{nums[3:7]}-{nums[7:]}"
    elif len(nums) == 8 and (nums.startswith("15") or nums.startswith("16") or nums.startswith("18")):
        return f"{nums[:4]}-{nums[4:]}"
        
    return raw

def is_strict_region_match(road_addr, jibun_addr, target_region_str):
    if not target_region_str.strip():
        return True
        
    target_tokens = [t.strip() for t in target_region_str.strip().split() if t.strip()]
    full_road = road_addr or ""
    full_jibun = jibun_addr or ""
    
    road_tokens = full_road.split()
    jibun_tokens = full_jibun.split()
    
    def match_single_target(target, tokens, full_text):
        if any(target.endswith(unit) for unit in ["구", "군", "읍", "면", "동", "리"]):
            return any(token == target for token in tokens)
        return target in full_text

    def check_all_targets(tokens, full_text):
        for target in target_tokens:
            if not match_single_target(target, tokens, full_text):
                return False
        return True

    return check_all_targets(road_tokens, full_road) or check_all_targets(jibun_tokens, full_jibun)

def is_valid_flower_category(category_str):
    if not category_str:
        return True
    return any(keyword in category_str for keyword in ALLOWED_CATEGORIES)

def fetch_phone_from_mobile(title, address, allowed_codes):
    if not address:
        return ""

    addr_tokens = [t.strip() for t in address.split() if t.strip()]
    short_addr = " ".join(addr_tokens[:3]) if len(addr_tokens) >= 3 else address
    search_kw = f"{short_addr} {title}"

    url = f"https://m.search.naver.com/search.naver?query={requests.utils.quote(search_kw)}"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    try:
        time.sleep(random.uniform(0.3, 0.6))
        res = requests.get(url, headers=headers, timeout=4)
        if res.status_code == 200:
            html = res.text
            
            # 주소 연고성 검증
            region_keywords = [
                t for t in addr_tokens 
                if len(t) >= 2 and t not in ["대한민국", "서울특별시", "광역시", "특별자치시", "도"]
            ]
            
            if region_keywords and not any(kw in html for kw in region_keywords):
                return ""

            # 1. tel: 태그 우선 추출
            tel_links = re.findall(r'href=["\']tel:([0-9\-\.]+)', html)
            for t in tel_links:
                formatted = format_phone_number(t, allowed_codes)
                if formatted:
                    return formatted

            # 2. 본문 일반 전화번호 패턴 추출
            pattern = r"(050\d|02|0[3-9]\d|01[016789])[-.)\s]?(\d{3,4})[-.)\s]?(\d{4})"
            matches = re.findall(pattern, html)
            for match in matches:
                raw_found = f"{match[0]}-{match[1]}-{match[2]}"
                formatted = format_phone_number(raw_found, allowed_codes)
                if formatted:
                    return formatted
                    
        elif res.status_code == 429:
            time.sleep(2)
    except Exception:
        pass
    return ""

def build_dynamic_queries(region_str, sub_locs_str):
    clean_region = region_str.strip()
    queries = []
    
    for kw in UNIVERSAL_KEYWORDS:
        queries.append(f"{clean_region} {kw}")
        
    if sub_locs_str.strip():
        sub_list = [s.strip() for s in sub_locs_str.split(",") if s.strip()]
        for sub in sub_list:
            for kw in ["꽃집", "화원", "플라워"]:
                queries.append(f"{clean_region} {sub} {kw}")
                
    return list(set(queries))

if st.button("🚀 선택 지역 전수 수집 시작", type="primary", use_container_width=True):
    if not NCP_CLIENT_ID or not NCP_CLIENT_SECRET:
        st.error("⚠️ 서버 환경 변수에 Naver API 키가 설정되지 않았습니다.")
    elif not target_region.strip():
        st.warning("⚠️ 수집할 지역을 입력하세요.")
    else:
        # 검색 대상 지역의 허용 지역번호 설정 (예: 제주 -> 064, 010, 0507 등만 허용)
        allowed_codes = get_allowed_area_codes_for_region(target_region)
        
        url = "https://naverapihub.apigw.ntruss.com/search/v1/local"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": NCP_CLIENT_ID.strip(),
            "X-NCP-APIGW-API-KEY": NCP_CLIENT_SECRET.strip(),
        }

        sub_queries = build_dynamic_queries(target_region, custom_sub_locations)
        shops = []
        seen = set()

        st.write(f"🔍 **'{target_region}'** 정밀 데이터 수집 중...")
        progress_bar = st.progress(0)
        status_text = st.empty()

        for idx, q in enumerate(sub_queries):
            status_text.text(f"[{idx+1}/{len(sub_queries)}] '{q}' 검색 중... (수집: {len(shops)}개)")
            
            params = {"query": q, "display": 5, "start": 1, "sort": "comment"}
            try:
                res = requests.get(url, headers=headers, params=params, timeout=5)
                if res.status_code == 200:
                    items = res.json().get("items", [])
                    for item in items:
                        title = clean_html(item.get("title", ""))
                        road_addr = item.get("roadAddress", "")
                        jibun_addr = item.get("address", "")
                        tel = format_phone_number(clean_html(item.get("telephone", "")).strip(), allowed_codes)
                        category = item.get("category", "")

                        target_addr = road_addr if road_addr else jibun_addr
                        key = (title, target_addr)

                        if key in seen:
                            continue
                        seen.add(key)

                        if not is_strict_region_match(road_addr, jibun_addr, target_region):
                            continue

                        if not is_valid_flower_category(category):
                            continue

                        shops.append({
                            "상호명": title,
                            "전화번호": tel,
                            "도로명주소": target_addr,
                            "지번주소": jibun_addr
                        })
                elif res.status_code == 429:
                    time.sleep(1)
            except Exception:
                pass
            
            time.sleep(0.15)
            progress_bar.progress((idx + 1) / len(sub_queries))

        status_text.empty()
        progress_bar.empty()

        # 전화번호 누락 매장 추가 추적 (해당 지역 허용 번호만 추적)
        missing_shops = [s for s in shops if not s["전화번호"]]
        if missing_shops:
            st.info(f"📞 전화번호 누락 매장 {len(missing_shops)}개 추가 정밀 추적 중...")
            phone_bar = st.progress(0)
            for p_idx, shop in enumerate(missing_shops):
                found_tel = fetch_phone_from_mobile(shop["상호명"], shop["도로명주소"], allowed_codes)
                if found_tel:
                    shop["전화번호"] = found_tel
                phone_bar.progress((p_idx + 1) / len(missing_shops))
            phone_bar.empty()

        # 전화번호가 최종적으로 존재하는 유효 항목만 구성
        final_shops = [s for s in shops if s["전화번호"]]

        if final_shops:
            formatted_data = []
            for item in final_shops:
                phone = item["전화번호"]
                is_mobile = phone.startswith("010")
                
                formatted_data.append({
                    "상호명": item["상호명"],
                    "휴대폰(010)": phone if is_mobile else "",
                    "전화번호(0507/지역번호/매장번호)": phone if not is_mobile else "",
                    "도로명주소": item["도로명주소"]
                })

            df = pd.DataFrame(formatted_data)
            st.success(f"🎉 **{target_region}** 총 **{len(df)}개** 유효 매장 수집 완료!")

            tab1, tab2 = st.tabs(["📊 화면 표 보기", "📋 1초 전체 복사하기"])

            with tab1:
                st.dataframe(df, use_container_width=True, height=450)

            with tab2:
                tsv_text = df.to_csv(sep="\t", index=False)
                tsv_json = json.dumps(tsv_text)
                
                copy_code = f"""
                    <script>
                    function copyToClipboard() {{
                        const text = {tsv_json};
                        navigator.clipboard.writeText(text).then(function() {{
                            alert('📋 지정된 4개 컬럼 데이터가 복사되었습니다!\\n엑셀에 바로 붙여넣기(Ctrl+V)하세요.');
                        }});
                    }}
                    </script>
                    <button onclick="copyToClipboard()" style="
                        background-color: #ff4b4b;
                        color: white;
                        padding: 12px 24px;
                        font-size: 16px;
                        font-weight: bold;
                        border: none;
                        border-radius: 8px;
                        cursor: pointer;
                        width: 100%;
                        margin-bottom: 12px;
                    ">📋 전체 데이터 1초 복사하기 (클릭)</button>
                """
                components.html(copy_code, height=70)

                st.caption("👇 또는 아래 상자 우측 상단의 복사 버튼(📋)을 클릭하세요.")
                st.code(tsv_text, language="text")
        else:
            st.error(f"'{target_region}'에서 유효한 전화번호가 있는 화원 매장을 찾지 못했습니다.")
