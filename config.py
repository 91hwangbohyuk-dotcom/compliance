"""
config.py — 프로그램의 설정값과 비밀정보(API Key) 읽기를 한곳에 모은다.

비밀정보는 코드에 직접 쓰지 않고 get_secret()으로 아래 순서대로 찾는다.
  1) 환경변수
  2) 프로젝트 폴더의 .env 파일
  3) .streamlit/secrets.toml (Streamlit 배포용)
"""

import os
from datetime import timedelta, timezone

from dotenv import load_dotenv

# .env 파일이 있으면 그 내용을 환경변수로 불러온다. (없어도 오류 없음)
# 이미 설정된 환경변수는 덮어쓰지 않으므로 "환경변수 → .env" 순서가 지켜진다.
load_dotenv()

# 모든 날짜·시각은 한국 시간(KST, UTC+9) 기준으로 다룬다.
KST = timezone(timedelta(hours=9))


# ── 카테고리 (PRD 4.F4) ─────────────────────────────────────
# 코드: 화면에 보여줄 한글 이름
CATEGORIES = {
    "POLICY": "법령·정책 동향",
    "SANCTION": "공정위 제재·심결",
    "COURT": "법원 판례",
    "CORPORATE": "주요 기업 공정거래 이슈",
    "GLOBAL": "해외 경쟁법 동향",
}

# ── 중요도 (PRD 7.5절) ──────────────────────────────────────
# 높은 순서대로 나열한다. 내부 값은 영어로 두고, 화면에는 IMPORTANCE_LABELS의 한글로 보여준다.
IMPORTANCE_LEVELS = ["High", "Medium", "Low"]
IMPORTANCE_LABELS = {"High": "높음", "Medium": "보통", "Low": "낮음"}

# ── 공정거래위원회 보도자료 수집 설정 (PRD 5.1절) ────────────────
# 2026-09-29 실제 홈페이지에서 확인한 값이다.
# 홈페이지 구조가 바뀌어 수집이 실패하면 이 부분만 고치면 된다.
FTC_LIST_URL = (
    "https://www.ftc.go.kr/www/selectBbsNttList.do"
    "?bordCd=3&key=12&searchCtgry=01,02&pageUnit={page_size}&pageIndex={page}"
)
FTC_DETAIL_URL = (
    "https://www.ftc.go.kr/www/selectBbsNttView.do"
    "?key=12&bordCd=3&searchCtgry=01,02&nttSn={ntt_sn}"
)
FTC_PAGE_SIZE = 30          # 목록 한 페이지에 보여줄 게시물 수
FTC_MAX_PAGES = 5           # 최대 몇 페이지까지 읽을지 (30건 x 5 = 약 1~2개월 분량)
FTC_ROW_SELECTOR = "tbody tr"             # 목록에서 게시물 1건(행)
FTC_TITLE_SELECTOR = "td.p-subject a"     # 행 안의 제목 링크
FTC_CONTENT_SELECTOR = "td.p-table__content"  # 상세 페이지 본문
FTC_CONTENT_MAX_CHARS = 1500              # AI 입력용으로 가져올 본문 길이

# ── HTTP 요청 설정 (모든 수집기 공통) ───────────────────────────
HTTP_TIMEOUT = 10           # 요청 1번의 최대 대기 시간(초)
HTTP_RETRIES = 2            # 실패 시 다시 시도하는 횟수
REQUEST_DELAY = 1.0         # 같은 사이트에 연속 요청할 때 쉬는 시간(초)
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ComplianceBriefing/0.1; educational project)"
}

# ── AI 설정 ─────────────────────────────────────────────────
# 제공사를 정하지 않았으면 API 없이 동작하는 "mock"을 쓴다.
DEFAULT_LLM_PROVIDER = "mock"

# LLM_MODEL을 비워 두면 쓰는 제공사별 기본 모델 (모델명은 여기에만 둔다)
DEFAULT_MODELS = {
    "openai": "gpt-5.5",
    "anthropic": "claude-opus-5-5",
    "mock": "mock",
}
# 제공사별로 필요한 API Key 이름
API_KEY_NAMES = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
LLM_MAX_TOKENS = 16000      # AI 응답 최대 길이(토큰)

# AI 분석 단위 (PRD 7.3절)
CLASSIFY_BATCH_SIZE = 20    # 관련성·분류: 한 번에 보낼 항목 수
GROUP_MAX_ITEMS = 80        # 중복 통합: 이보다 많으면 카테고리별로 나눠 요청
ISSUE_MAX_ITEMS = 5         # 이슈 분석: 이슈 하나에 넣을 최대 자료 수
ANALYZE_WORKERS = 4         # 이슈 분석을 동시에 몇 개씩 처리할지


def get_secret(name: str) -> str | None:
    """비밀정보나 설정값을 읽는다. 어디에도 없으면 None을 돌려준다."""
    # 1) 환경변수 (+ load_dotenv로 불러온 .env 값)
    value = os.getenv(name)
    if value:
        return value

    # 2) Streamlit secrets (.streamlit/secrets.toml)
    #    secrets 파일이 없거나 Streamlit 밖에서 실행하면 오류가 나므로 조용히 넘어간다.
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass

    return None


def get_llm_provider() -> str:
    """사용할 LLM 제공사 이름을 돌려준다. (mock | openai | anthropic)"""
    return (get_secret("LLM_PROVIDER") or DEFAULT_LLM_PROVIDER).lower()
