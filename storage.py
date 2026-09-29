"""
storage.py — 조회기간별 브리핑 결과를 JSON 파일로 저장하고 불러온다. (PRD 6.4절)
같은 기간을 다시 조회할 때 수집·AI 분석을 반복하지 않기 위해 사용한다.

파일 위치: data/briefings/브리핑_시작일_종료일.json

캐시 키(파일 이름) 기준
  - 조회기간은 이미 "날짜 단위"로 계산된다. (예: 최근 1일 = 어제~오늘, app.get_period 참고)
  - 그래서 같은 날 "최근 1일"을 여러 번 조회하면 시작일·종료일이 같아 같은 파일을 다시 쓴다.
  - 날짜가 바뀌면 기간도 바뀌므로 새 파일이 된다.
  - 오늘 늦게 올라온 보도자료는 저장 이후 반영되지 않으므로, 필요하면 "새로 생성"으로 다시 만든다.

저장 내용: 이슈 목록, 수집한 전체 항목, 소스별 수집 상태, 생성 시각, 사용한 AI 제공사·모델
"""

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from config import KST
from models import Issue, Item

DATA_DIR = Path(__file__).parent / "data" / "briefings"


def get_path(start: date, end: date) -> Path:
    """조회기간에 해당하는 저장 파일 경로"""
    return DATA_DIR / f"브리핑_{start}_{end}.json"


# ── dataclass ↔ dict 변환 (datetime은 ISO 문자열로) ───────────────


def _item_to_dict(item: Item) -> dict:
    data = asdict(item)
    data["published_at"] = item.published_at.isoformat()
    return data


def _item_from_dict(data: dict) -> Item:
    data = dict(data)
    data["published_at"] = datetime.fromisoformat(data["published_at"])
    return Item(**data)


def _issue_to_dict(issue: Issue) -> dict:
    data = asdict(issue)
    data["published_at"] = issue.published_at.isoformat()
    data["main_item"] = _item_to_dict(issue.main_item)
    data["related_items"] = [_item_to_dict(item) for item in issue.related_items]
    return data


def _issue_from_dict(data: dict) -> Issue:
    data = dict(data)
    data["published_at"] = datetime.fromisoformat(data["published_at"])
    data["main_item"] = _item_from_dict(data["main_item"])
    data["related_items"] = [_item_from_dict(item) for item in data["related_items"]]
    return Issue(**data)


# ── 저장 / 불러오기 ─────────────────────────────────────────────


def save_briefing(start: date, end: date, issues: list[Issue], status: list[dict],
                  items: list[Item] | None = None, provider: str = "", model: str = "") -> datetime:
    """브리핑 결과를 파일로 저장하고 생성 시각을 돌려준다."""
    generated_at = datetime.now(KST)
    data = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_at": generated_at.isoformat(),
        "provider": provider,
        "model": model,
        "status": status,
        "issues": [_issue_to_dict(issue) for issue in issues],
        "items": [_item_to_dict(item) for item in (items or [])],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    get_path(start, end).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return generated_at


def load_briefing(start: date, end: date) -> dict | None:
    """
    저장된 브리핑을 불러온다.
    - 파일이 없으면 None
    - 파일이 깨졌으면 ValueError (호출하는 쪽에서 안내 후 새로 생성)
    돌려주는 값: {"issues", "items", "status", "generated_at", "provider", "model"}
    """
    path = get_path(start, end)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "issues": [_issue_from_dict(issue) for issue in data["issues"]],
            "items": [_item_from_dict(item) for item in data.get("items", [])],
            "status": data["status"],
            "generated_at": datetime.fromisoformat(data["generated_at"]),
            "provider": data.get("provider", ""),
            "model": data.get("model", ""),
        }
    except Exception as error:  # JSON 문법 오류, 항목 누락 등
        raise ValueError(f"저장 파일을 읽을 수 없습니다({path.name}): {error}")
