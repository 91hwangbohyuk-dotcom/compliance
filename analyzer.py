"""
analyzer.py — 수집된 Item 목록을 AI로 분석해 Issue 목록을 만든다. (PRD 7.3절)

흐름
  1) classify_items   : 관련성 판단 + 카테고리 분류 (20건씩 묶어 요청)
  2) group_duplicates : 같은 사건을 다룬 항목끼리 묶기
  3) analyze_issue    : 묶음마다 제목·핵심 내용·Check Point·중요도 작성
  build_briefing()이 위 3단계를 순서대로 실행한다.

원천정보 보호 (PRD 7.6절)
  공개일·출처·원문 링크는 AI 응답을 쓰지 않고 수집된 Item에서 코드로 채운다.
  AI에게는 짧은 번호(id="1", "2", ...)만 보여주고, 답을 받으면 번호로 원래 Item을 찾는다.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import llm_client
import prompts
from models import Issue, Item

DEFAULT_CATEGORY = "CORPORATE"
DEFAULT_IMPORTANCE = "Medium"
SUMMARY_FAILED = "요약 실패"


def format_documents(items: list[Item], content_chars: int) -> str:
    """AI에게 보낼 자료 묶음을 만든다. 각 자료에는 목록 순서대로 1, 2, 3... 번호를 붙인다."""
    parts = []
    for number, item in enumerate(items, start=1):
        parts.append(
            f'<item id="{number}">\n'
            f"제목: {item.title}\n"
            f"출처: {item.source}\n"
            f"공개일: {item.published_at:%Y-%m-%d}\n"
            f"내용: {item.content[:content_chars]}\n"
            f"</item>"
        )
    return "<documents>\n" + "\n".join(parts) + "\n</documents>"


def _chunks(items: list, size: int) -> list[list]:
    """목록을 size개씩 나눈다."""
    return [items[i : i + size] for i in range(0, len(items), size)]


# ── 1) 관련성 판단 + 카테고리 분류 ───────────────────────────────


def classify_items(items: list[Item], errors: list | None = None) -> None:
    """
    각 Item의 is_relevant, category를 채운다. AI 응답이 없거나 잘못되면 '관련 있음 + 기본 카테고리'로 둔다.
    errors 목록을 넘기면 AI 호출 실패 내용을 거기에 적는다. (아래 함수들도 같음)
    """
    for batch in _chunks(items, config.CLASSIFY_BATCH_SIZE):
        # 먼저 기본값을 넣어 두고, AI 답이 오면 덮어쓴다. (응답 누락·실패 시에도 항목을 잃지 않음)
        for item in batch:
            item.is_relevant, item.category = True, DEFAULT_CATEGORY
        try:
            prompt = prompts.CLASSIFY_PROMPT.format(documents=format_documents(batch, 300))
            answer = llm_client.ask_json(prompts.SYSTEM_PROMPT, prompt)
        except Exception as error:
            _record_error(errors, "분류", error)
            continue

        for result in answer.get("results", []):
            if not isinstance(result, dict):
                continue
            index = _to_index(result.get("id"), len(batch))
            if index is None:  # 존재하지 않는 번호는 무시
                continue
            item = batch[index]
            item.is_relevant = result.get("is_relevant") is not False
            if result.get("category") in config.CATEGORIES:
                item.category = result["category"]


def _to_index(value, size: int) -> int | None:
    """AI가 돌려준 번호("1", 2 등)를 목록 위치(0부터)로 바꾼다. 범위를 벗어나면 None."""
    try:
        index = int(value) - 1
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < size else None


# ── 2) 중복 통합 ────────────────────────────────────────────────


def group_duplicates(items: list[Item], errors: list | None = None) -> list[list[Item]]:
    """같은 사건을 다룬 항목끼리 묶어 [[Item, ...], ...] 형태로 돌려준다."""
    if len(items) <= config.GROUP_MAX_ITEMS:
        return _group_once(items, errors)

    # 항목이 많으면 카테고리별로 나눠서 요청한다.
    groups = []
    for category in config.CATEGORIES:
        same_category = [item for item in items if item.category == category]
        for chunk in _chunks(same_category, config.GROUP_MAX_ITEMS):
            groups.extend(_group_once(chunk, errors))
    return groups


def _group_once(items: list[Item], errors: list | None = None) -> list[list[Item]]:
    """AI에 한 번 요청해 묶는다. 응답에 빠진 항목은 혼자 한 묶음, 없는 번호·중복 번호는 무시한다."""
    if len(items) <= 1:
        return [[item] for item in items]
    try:
        prompt = prompts.GROUP_PROMPT.format(documents=format_documents(items, 150))
        answer = llm_client.ask_json(prompts.SYSTEM_PROMPT, prompt)
        raw_groups = answer.get("groups", [])
    except Exception as error:
        _record_error(errors, "중복 통합", error)  # 실패하면 모두 개별 이슈로 처리
        raw_groups = []

    used, groups = set(), []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list):
            continue
        group = []
        for value in raw_group:
            index = _to_index(value, len(items))
            if index is not None and index not in used:
                used.add(index)
                group.append(items[index])
        if group:
            groups.append(group)

    # AI 응답에 빠진 항목은 단독 이슈로 추가 (누락 방지)
    groups.extend([item] for index, item in enumerate(items) if index not in used)
    return groups


# ── 3) 이슈 분석 ────────────────────────────────────────────────


def pick_main_item(items: list[Item]) -> Item:
    """대표 출처: 공식 출처 우선, 그다음 가장 먼저 공개된 항목"""
    return min(items, key=lambda item: (item.source_type != "official", item.published_at))


def analyze_issue(items: list[Item], errors: list | None = None) -> Issue:
    """한 사건의 항목들로 Issue를 만든다. AI 분석이 실패해도 원천정보로 Issue를 만들어 '요약 실패'로 표시한다."""
    main_item = pick_main_item(items)
    related_items = [item for item in items if item is not main_item]

    # 원천정보(공개일·출처·링크)는 수집 데이터에서만 가져온다.
    issue = Issue(
        id=main_item.id,
        title=main_item.title,
        category=main_item.category if main_item.category in config.CATEGORIES else DEFAULT_CATEGORY,
        importance=DEFAULT_IMPORTANCE,
        importance_reason=SUMMARY_FAILED,
        published_at=min(item.published_at for item in items),
        key_points=[f"{SUMMARY_FAILED}: AI 분석 중 오류가 발생했습니다. 원문을 확인하세요."],
        checkpoints=[],
        main_item=main_item,
        related_items=related_items,
    )

    # AI 입력: 공식 출처 → 이른 공개일 순으로 최대 ISSUE_MAX_ITEMS건
    selected = sorted(items, key=lambda item: (item.source_type != "official", item.published_at))
    selected = selected[: config.ISSUE_MAX_ITEMS]
    try:
        prompt = prompts.ANALYZE_PROMPT.format(documents=format_documents(selected, config.FTC_CONTENT_MAX_CHARS))
        answer = llm_client.ask_json(prompts.SYSTEM_PROMPT, prompt)
    except Exception as error:
        _record_error(errors, f"이슈 분석({main_item.title[:20]})", error)
        return issue

    # 핵심 내용이 없으면 쓸 수 있는 요약이 아니므로 '요약 실패'로 둔다.
    key_points = _string_list(answer.get("key_points"))[:5]
    if not key_points:
        _record_error(errors, f"이슈 분석({main_item.title[:20]})", ValueError("AI 응답에 핵심 내용(key_points)이 없습니다"))
        return issue

    # AI가 준 값 중 제목·요약·점검사항·중요도만 검증해서 사용한다.
    if isinstance(answer.get("title"), str) and answer["title"].strip():
        issue.title = answer["title"].strip()
    issue.key_points = key_points
    issue.checkpoints = _string_list(answer.get("checkpoints"))[:3]
    issue.importance = answer.get("importance") if answer.get("importance") in config.IMPORTANCE_LEVELS else DEFAULT_IMPORTANCE
    reason = answer.get("importance_reason")
    issue.importance_reason = reason.strip() if isinstance(reason, str) and reason.strip() else ""
    return issue


def _record_error(errors: list | None, step: str, error: Exception) -> None:
    """AI 호출 실패를 기록한다. API Key 일부가 섞일 수 있어 describe_error로 가린 문구만 남긴다."""
    message = f"[{step} 실패] {llm_client.describe_error(error)}"
    print(message)
    if errors is not None:
        errors.append(message)


def _string_list(value) -> list[str]:
    """AI 응답 값에서 비어 있지 않은 문자열만 골라 목록으로 만든다."""
    if not isinstance(value, list):
        return []
    return [text.strip() for text in value if isinstance(text, str) and text.strip()]


# ── 브리핑 정리: 정렬·필터 (화면과 분리된 함수, PRD 6.3절 / 4.F7) ─────────


def has_official_source(issue: Issue) -> bool:
    """이슈에 공식 출처(공정위 등) 자료가 포함되어 있는지"""
    return any(item.source_type == "official" for item in [issue.main_item] + issue.related_items)


def sort_issues(issues: list[Issue]) -> list[Issue]:
    """중요도(High→Low) → 공식 출처 포함 → 관련 자료 수(많은 순) → 공개일(최신순)으로 정렬한 새 목록"""
    def sort_key(issue: Issue):
        importance_rank = config.IMPORTANCE_LEVELS.index(issue.importance) if issue.importance in config.IMPORTANCE_LEVELS else len(config.IMPORTANCE_LEVELS)
        return (
            importance_rank,
            not has_official_source(issue),
            -len(issue.related_items),
            -issue.published_at.timestamp(),
        )
    return sorted(issues, key=sort_key)


def filter_issues(issues: list[Issue], categories=None, importances=None, keyword: str = "") -> list[Issue]:
    """
    조건에 맞는 이슈만 돌려준다. 조건을 비워 두면(None 또는 빈 값) 그 조건은 적용하지 않는다.
    keyword는 제목·핵심 내용·Check Point·출처에서 대소문자 구분 없이 부분 일치로 찾는다.
    """
    keyword = keyword.strip().casefold()
    result = []
    for issue in issues:
        if categories and issue.category not in categories:
            continue
        if importances and issue.importance not in importances:
            continue
        if keyword:
            sources = [item.source for item in [issue.main_item] + issue.related_items]
            searchable = " ".join([issue.title, *issue.key_points, *issue.checkpoints, *sources]).casefold()
            if keyword not in searchable:
                continue
        result.append(issue)
    return result


# ── 전체 실행 ───────────────────────────────────────────────────


def build_briefing(items: list[Item], progress_callback=None, errors: list | None = None) -> list[Issue]:
    """
    수집 항목을 분석해 Issue 목록을 만든다. (sort_issues 기준으로 정렬)
    progress_callback(메시지)를 넘기면 진행 단계를 알려준다.
    errors 목록을 넘기면 분석 중 AI 호출 실패 내용이 담긴다. (비어 있으면 모두 성공)
    AI 설정(API Key 등)에 문제가 있으면 시작 전에 RuntimeError를 낸다.
    """
    def report(message: str) -> None:
        if progress_callback:
            progress_callback(message)

    llm_client.check_settings()
    if not items:
        return []

    report(f"관련성·카테고리 분류 중 ({len(items)}건)")
    classify_items(items, errors)
    relevant = [item for item in items if item.is_relevant]

    report(f"중복 통합 중 (관련 항목 {len(relevant)}건)")
    groups = group_duplicates(relevant, errors)

    issues = []
    report(f"이슈 분석 중 (0/{len(groups)})")
    with ThreadPoolExecutor(max_workers=config.ANALYZE_WORKERS) as executor:
        futures = [executor.submit(analyze_issue, group, errors) for group in groups]
        # 진행 알림은 이 반복문(메인 스레드)에서만 한다. (Streamlit 화면은 메인 스레드에서만 갱신 가능)
        for done, future in enumerate(as_completed(futures), start=1):
            issues.append(future.result())
            report(f"이슈 분석 중 ({done}/{len(groups)})")

    return sort_issues(issues)


# 단독 실행: python -m analyzer  → 최근 1일 수집 → 분석 결과 출력
if __name__ == "__main__":
    from datetime import datetime, timedelta

    from collectors import collect_all

    today = datetime.now(config.KST).date()
    start = today - timedelta(days=1)
    collected, status = collect_all(start, today)
    print(f"수집 {start} ~ {today}: {len(collected)}건 {status}")
    print(f"LLM: {config.get_llm_provider()} / {llm_client.get_model()}")

    errors = []
    result = build_briefing(collected, progress_callback=print, errors=errors)
    excluded = sum(1 for item in collected if item.is_relevant is False)
    print(f"\n이슈 {len(result)}개 (관련 없음 제외 {excluded}건, AI 호출 실패 {len(errors)}건)")
    for issue in result[:3]:
        print(f"\n[{issue.importance}] [{config.CATEGORIES[issue.category]}] {issue.title}")
        print(f"  공개일 {issue.published_at:%Y-%m-%d} | 출처 {issue.main_item.source} | 관련 자료 {len(issue.related_items)}건")
        print(f"  {issue.main_item.url}")
        for point in issue.key_points:
            print(f"  - {point}")
        for checkpoint in issue.checkpoints:
            print(f"  ✔ {checkpoint}")
