import os
import json
import re
import time
import random
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="전국 화원사 수집기", page_icon="🌷", layout="wide")
st.title("📊 전국 화원사 자동 수집기")

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
    st.info("💡 동일 상호 타 지역 매장 번호 혼입 방지를 위한 주소 연고성 검증이 추가되었습니다.")

col1, col2 = st.columns([1, 1])

with col1:
    target_region = st.text_input(
        "수집할 지역 입력 (필수)", 
        value="", 
        placeholder="예:대구 동구, 부산 남구, 단양군"
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
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/101.0.4951.44 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36"
]

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

def format_phone_number(raw):
    if not raw:
        return ""
    nums = re.sub(r"[^\d]", "", str(raw))
    
    if not nums.startswith("0") and not nums.startswith("15") and not nums.startswith("16") and not nums.startswith("18"):
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
    tokens = [t.strip() for t in target_region_str.strip().split() if t.strip()]
    full_road = road_addr or ""
    full_jibun = jibun_addr or ""
    
    match_road = all(token in full_road for token in tokens)
    match_jibun = all(token in full_jibun for token in tokens)
    return match_road or match_jibun

def is_valid_flower_category(category_str):
    if not category_str:
        return True
    return any(keyword in category_str for keyword in ALLOWED_CATEGORIES)

# 💡 [주소 연고성 검증 강화된 모바일 번호 추적 함수]
def fetch_phone_from_mobile(title, address):
    if not address:
        return ""

    addr_tokens = [t.strip() for t in address.split() if t.strip()]
    
    # 1. 주소를 검색어 앞에 두어 타 지역 본점 우위 현상 차단
    short_addr = " ".join(addr_tokens[:3]) if len(addr_tokens) >= 3 else address
    search_kw = f"{short_addr} {title}"

    url = f"https://m.search.naver.com/search.naver?query={requests.utils.quote(search_kw)}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS)
    }
    try:
        time.sleep(random.uniform(0.3, 0.6))
        res = requests.get(url, headers=headers, timeout=4)
        if res.status_code == 200:
            html = res.text
            
            # 2. 주소 연고성 검증: 구, 동, 도로명 단어가 모바일 검색 페이지에 실제로 있는지 확인
            # (예: '북구', '동천동' 등이 HTML 페이지 내에 한 번도 나오지 않으면 타 지역 검색 결과로 간주하여 차단)
            region_keywords = [
                t for t in addr_tokens 
                if len(t) >= 2 and t not in ["대한민국", "서울특별시", "광역시", "특별자치시", "도"]
            ]
            
            if region_keywords and not any(kw in html for kw in region_keywords):
                return "" # 주소 연고가 없는 타 지역 결과이므로 수집 중단

            # 3. tel: 태그 우선 추출
            tel_links = re.findall(r'href=["\']tel:([0-9\-\.]+)', html)
            for t in tel_links:
                formatted = format_phone_number(t)
                if formatted:
                    return formatted

            # 4. 본문 일반 전화번호 패턴 추출
            pattern = r"(050\d|02|0[3-9]\d|01[016789])[-.)\s]?(\d{3,4})[-.)\s]?(\d{4})"
            match = re.search(pattern, html)
            if match:
                raw_found = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
                return format_phone_number(raw_found)
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
                        tel = format_phone_number(clean_html(item.get("telephone", "")).strip())
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

        # 전화번호 누락 매장 추가 추적 (주소 연고성 검증 포함)
        missing_shops = [s for s in shops if not s["전화번호"]]
        if missing_shops:
            st.info(f"📞 전화번호 누락 매장 {len(missing_shops)}개 추가 정밀 추적 중...")
            phone_bar = st.progress(0)
            for p_idx, shop in enumerate(missing_shops):
                found_tel = fetch_phone_from_mobile(shop["상호명"], shop["도로명주소"])
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
