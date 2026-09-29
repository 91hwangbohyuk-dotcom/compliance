"""
ftc_press.py — 공정거래위원회 홈페이지 보도자료 게시판에서 보도자료를 수집한다.

동작 순서
  1) 보도자료 목록 페이지를 1페이지부터 읽는다. (최신 등록일 순)
  2) 등록일이 조회기간 안에 있는 게시물만 골라 상세 페이지 본문 앞부분을 가져온다.
  3) 등록일이 조회 시작일보다 이른 게시물이 나오면 읽기를 멈춘다.

URL·HTML 선택자는 config.py에 있다. (PRD 5.1절)
등록일에는 시각이 없으므로 날짜 단위로 비교하고, 공개일시는 해당 날짜 00:00(KST)으로 저장한다.
"""

import hashlib
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config
from models import Item

SOURCE_NAME = "공정거래위원회"


def fetch_html(url: str) -> str:
    """URL의 HTML을 가져온다. 실패하면 config.HTTP_RETRIES번 다시 시도한다."""
    last_error = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            response = requests.get(url, headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT)
            response.raise_for_status()
            return response.text
        except requests.RequestException as error:
            last_error = error
            if attempt < config.HTTP_RETRIES:
                time.sleep(config.REQUEST_DELAY)
    raise RuntimeError(f"공정위 홈페이지 요청 실패: {last_error}")


def parse_list_page(html: str) -> list[dict]:
    """목록 페이지 HTML에서 게시물 정보(제목, 등록일, 구분, 담당부서, 상세 URL)를 뽑는다."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select(config.FTC_ROW_SELECTOR):
        link = tr.select_one(config.FTC_TITLE_SELECTOR)
        if link is None:  # 게시물 행이 아니면 건너뜀
            continue

        # 칸 순서: 번호, 구분, 제목, 담당부서, 등록일, 첨부파일
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        date_text = next((c for c in cells if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c)), None)
        ntt_sn = parse_qs(urlparse(urljoin(config.FTC_LIST_URL, link["href"])).query).get("nttSn", [None])[0]
        if date_text is None or ntt_sn is None:
            # 예상한 구조가 아니면 추측하지 않고 건너뛴다. (전체가 건너뛰어지면 collect에서 오류로 알림)
            continue

        rows.append({
            "title": link.get_text(" ", strip=True),
            "date": date.fromisoformat(date_text),
            "section": cells[1] if len(cells) > 1 else "",      # 구분: 보도 / 참고
            "department": cells[3] if len(cells) > 3 else "",   # 담당부서
            "url": config.FTC_DETAIL_URL.format(ntt_sn=ntt_sn),
        })
    return rows


def fetch_content(url: str) -> str:
    """상세 페이지 본문 앞부분을 가져온다. 실패하면 빈 문자열을 돌려준다(목록 정보는 살림)."""
    try:
        soup = BeautifulSoup(fetch_html(url), "html.parser")
    except RuntimeError:
        return ""
    body = soup.select_one(config.FTC_CONTENT_SELECTOR)
    if body is None:
        return ""
    text = " ".join(body.get_text(" ").split())  # 줄바꿈·공백 정리
    return text[: config.FTC_CONTENT_MAX_CHARS]


def collect(start: date, end: date) -> list[Item]:
    """조회기간(start~end, 날짜 포함)에 등록된 공정위 보도자료를 Item 목록으로 돌려준다."""
    items = []
    for page in range(1, config.FTC_MAX_PAGES + 1):
        url = config.FTC_LIST_URL.format(page_size=config.FTC_PAGE_SIZE, page=page)
        rows = parse_list_page(fetch_html(url))
        if not rows:
            if page == 1:
                # 첫 페이지에서 게시물을 하나도 못 찾으면 홈페이지 구조가 바뀌었을 가능성이 크다.
                raise RuntimeError("보도자료 목록을 해석하지 못했습니다. 홈페이지 구조가 바뀌었는지 확인하세요. (config.py의 FTC_ 설정)")
            break

        reached_older = False
        for row in rows:
            if row["date"] > end:       # 조회기간보다 최근 → 건너뜀
                continue
            if row["date"] < start:     # 조회기간보다 과거 → 이후는 모두 과거이므로 중단
                reached_older = True
                break

            time.sleep(config.REQUEST_DELAY)
            body = fetch_content(row["url"])
            # AI가 참고할 수 있도록 구분·담당부서를 본문 앞에 붙인다.
            content = f"[{row['section']}] 담당부서: {row['department']}\n{body}"

            items.append(Item(
                id=hashlib.md5(row["url"].encode()).hexdigest(),
                title=row["title"],
                url=row["url"],
                source=SOURCE_NAME,
                source_type="official",
                published_at=datetime.combine(row["date"], datetime.min.time(), tzinfo=config.KST),
                content=content,
            ))

        if reached_older:
            break
        time.sleep(config.REQUEST_DELAY)
    return items


# 단독 실행: python -m collectors.ftc_press  → 최근 3일 보도자료 출력
if __name__ == "__main__":
    today = datetime.now(config.KST).date()
    start = today - timedelta(days=3)
    result = collect(start, today)
    print(f"조회기간 {start} ~ {today}: {len(result)}건")
    for item in result[:5]:
        print(f"- {item.published_at:%Y-%m-%d} | {item.title}")
        print(f"  {item.url}")
        print(f"  {item.content[:80]}...")
