"""
models.py — 프로그램 전체에서 주고받는 데이터의 형태를 정의한다.

- Item  : 수집기가 모은 기사·보도자료 1건
- Issue : 같은 사건을 다룬 Item들을 하나로 묶고 AI 분석 결과를 붙인 이슈 1건

(PRD 9.5절 데이터 구조 기준)
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    """수집 항목 1건 (뉴스 기사 또는 공정위 보도자료)"""

    id: str                     # 원문 URL의 해시값
    title: str                  # 제목
    url: str                    # 원문 링크
    source: str                 # 매체명 또는 "공정거래위원회"
    source_type: str            # "news"(언론) | "official"(공식 기관)
    published_at: datetime      # 공개일시 (한국 시간)
    content: str = ""           # 요약문 또는 본문 발췌 (AI 입력용)
    category: str | None = None       # AI 분류 결과 (2-2단계에서 채움)
    is_relevant: bool | None = None   # AI 관련성 판단 결과 (2-2단계에서 채움)


@dataclass
class Issue:
    """같은 사건을 다룬 항목들을 묶은 이슈 1건"""

    id: str
    title: str                  # AI가 작성한 이슈 제목
    category: str               # POLICY | SANCTION | COURT | CORPORATE | GLOBAL
    importance: str             # High | Medium | Low
    importance_reason: str      # 중요도 판단 이유 (한 문장)
    published_at: datetime      # 묶인 항목 중 가장 이른 공개일 (수집 데이터에서 가져옴)
    key_points: list[str]       # 핵심 내용 (3~5개)
    checkpoints: list[str]      # 준법 Check Point (2~3개)
    main_item: Item             # 대표 출처·원문 링크
    related_items: list[Item] = field(default_factory=list)  # 같은 사건을 다룬 다른 기사들
