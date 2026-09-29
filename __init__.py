"""
collectors — 데이터 수집기 모음.

각 수집기 파일은 collect(start, end) -> list[Item] 함수를 가진다.
collect_all()은 등록된 모든 수집기를 실행하고, 결과를 합쳐 정리한다.

새 데이터 소스 추가 방법
  1) collectors/ 폴더에 새 파일(예: news_rss.py)을 만들고 collect(start, end) 함수를 작성한다.
  2) 아래 get_collectors()에 한 줄 추가한다.
"""

import re
from datetime import date

from models import Item


def get_collectors() -> dict:
    """사용할 수집기 목록 {화면에 보여줄 소스 이름: collect 함수}"""
    # 함수 안에서 import하는 이유: python -m collectors.ftc_press 로 단독 실행할 때
    # 같은 파일이 두 번 불러와지는 것(경고 발생)을 막기 위해서다.
    from collectors import ftc_press

    return {
        "공정위 보도자료": ftc_press.collect,
        # "뉴스": news_rss.collect,   ← 새 소스는 여기에 추가
    }


def _normalize_title(title: str) -> str:
    """중복 비교용 제목: 공백·특수문자를 없애고 소문자로 바꾼다."""
    return re.sub(r"[\W_]+", "", title).lower()


def remove_duplicates(items: list[Item]) -> list[Item]:
    """원문 URL이나 정규화한 제목이 같은 항목은 하나만 남긴다. (공식 출처를 우선 남김)"""
    items = sorted(items, key=lambda item: item.source_type != "official")  # 공식 출처가 앞으로
    seen_urls, seen_titles, result = set(), set(), []
    for item in items:
        title_key = _normalize_title(item.title)
        if item.url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(item.url)
        seen_titles.add(title_key)
        result.append(item)
    return result


def collect_all(start: date, end: date, collectors: dict | None = None) -> tuple[list[Item], list[dict]]:
    """
    모든 수집기를 실행해 (수집 항목 목록, 소스별 상태) 를 돌려준다.
    - 한 수집기가 실패해도 멈추지 않고 상태(status)에 오류를 기록한다.
    - collectors를 넘기지 않으면 get_collectors()의 목록을 쓴다. (테스트할 때 바꿔 넣을 수 있음)
    """
    if collectors is None:
        collectors = get_collectors()

    all_items, status = [], []
    for name, collect in collectors.items():
        try:
            items = collect(start, end)
            all_items.extend(items)
            status.append({"source": name, "ok": True, "count": len(items), "error": ""})
        except Exception as error:  # 어떤 오류든 기록만 하고 다음 수집기로 넘어간다
            status.append({"source": name, "ok": False, "count": 0, "error": str(error)})

    # 조회기간 밖 항목 제거 → 중복 제거 → 최신순 정렬
    in_range = [item for item in all_items if start <= item.published_at.date() <= end]
    unique = remove_duplicates(in_range)
    unique.sort(key=lambda item: item.published_at, reverse=True)
    return unique, status
