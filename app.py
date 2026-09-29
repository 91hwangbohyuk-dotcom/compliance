"""
app.py — Streamlit 화면 (프로그램 진입점).

실행: streamlit run app.py

화면 흐름 (2-3단계)
  1) 사이드바에서 조회기간을 고르고 "브리핑 생성"을 누른다.
  2) 저장된 결과가 있으면 바로 보여주고, 없으면 수집 → AI 분석 → 저장 후 보여준다.
     ("다시 수집·분석"은 저장된 결과를 무시하고 다시 수집·분석한다.)
  3) 요약 지표, 주요 공정거래 이슈 Top 5, 카테고리 탭, 이슈 카드를 보여주고 사이드바 필터·검색을 적용한다.

디자인(3단계): 색상·여백 등 스타일은 style.css, 기본 테마는 .streamlit/config.toml에 있다.
배지·카드 같은 화면 조각은 아래 "화면 도우미" 함수들이 HTML로 만든다.
"""

import html
from datetime import date, datetime, timedelta
from pathlib import Path

import streamlit as st

import llm_client
import storage
from analyzer import SUMMARY_FAILED, build_briefing, filter_issues, sort_issues
from collectors import collect_all
from config import CATEGORIES, IMPORTANCE_LABELS, IMPORTANCE_LEVELS, KST, get_llm_provider
from models import Issue

PERIOD_OPTIONS = ["최근 1일", "최근 3일", "최근 1주", "직접 선택"]
MAX_CUSTOM_DAYS = 14  # 직접 선택 최대 기간(양 끝 날짜 포함 일수)
TOP_N = 5
STYLE_PATH = Path(__file__).parent / "style.css"


# ── 조회기간 ────────────────────────────────────────────────────


def get_period(option: str, custom_range) -> tuple[date, date] | None:
    """
    선택한 조회기간을 (시작일, 종료일)로 바꾼다. 날짜 단위이며 양 끝 날짜를 포함한다.
    공정위 게시판 등록일에 시각이 없어서 날짜 단위로 계산한다.
      - 최근 1일 = 어제 ~ 오늘
      - 최근 3일 = 3일 전 ~ 오늘
      - 최근 1주 = 7일 전 ~ 오늘
    직접 선택에서 날짜를 두 개 다 고르지 않았으면 None을 돌려준다.
    """
    today = datetime.now(KST).date()
    days = {"최근 1일": 1, "최근 3일": 3, "최근 1주": 7}
    if option in days:
        return today - timedelta(days=days[option]), today
    if isinstance(custom_range, (list, tuple)) and len(custom_range) == 2:
        return custom_range[0], custom_range[1]
    return None


def validate_period(start: date, end: date, today: date) -> str | None:
    """직접 선택한 기간이 올바른지 검사한다. 문제가 있으면 안내 문구, 없으면 None."""
    if start > end:
        return "시작일이 종료일보다 늦습니다."
    if end > today:
        return "미래 날짜는 선택할 수 없습니다."
    if (end - start).days + 1 > MAX_CUSTOM_DAYS:
        return f"조회기간은 최대 {MAX_CUSTOM_DAYS}일까지 선택할 수 있습니다."
    return None


# ── 브리핑 생성 (저장 결과 사용 또는 수집 → 분석 → 저장) ─────────────


def create_briefing(start: date, end: date, force: bool) -> None:
    """
    브리핑을 만들어 st.session_state["briefing"]에 넣는다.
    force=False면 저장된 결과를 먼저 찾고, force=True("다시 수집·분석" 버튼)면 무조건 다시 수집·분석한다.
    """
    provider, model = get_llm_provider(), llm_client.get_model()

    if not force:
        try:
            saved = storage.load_briefing(start, end)
        except ValueError as error:
            st.warning(f"{error} → 다시 수집·분석합니다.")
            saved = None
        if saved and saved["provider"] != provider:
            st.info(f"저장된 결과는 다른 AI 제공사({saved['provider']})로 만든 것이어서 다시 수집·분석합니다.")
            saved = None
        if saved:
            print(f"[브리핑] 저장된 결과 사용: {start} ~ {end} (수집·AI 호출 없음)")
            st.session_state.pop("last_status", None)
            st.session_state["briefing"] = {**saved, "period": (start, end), "from_cache": True}
            return

    # AI 설정 문제(API Key 없음 등)는 수집 전에 알려 준다.
    try:
        llm_client.check_settings()
    except RuntimeError as error:
        st.error(str(error))
        return

    print(f"[브리핑] 새로 생성: {start} ~ {end} (수집 + AI 분석, {provider}/{model})")
    with st.status("브리핑 생성 중...", expanded=True) as status_box:
        status_box.write("공정위 보도자료 수집 중...")
        items, status = collect_all(start, end)
        if not any(s["ok"] for s in status):
            status_box.update(label="수집에 실패했습니다", state="error", expanded=False)
            st.session_state["briefing"] = None
            st.session_state["last_status"] = status
            return
        errors = []  # AI 호출 실패 내용 (API Key는 가려진 문구)
        issues = build_briefing(items, progress_callback=status_box.write, errors=errors)
        if errors:
            status_box.update(label=f"브리핑 생성 완료(일부 AI 분석 실패): 이슈 {len(issues)}개", state="error", expanded=False)
        else:
            status_box.update(label=f"브리핑 생성 완료: 이슈 {len(issues)}개", state="complete", expanded=False)

    if errors:
        # 실패가 섞인 결과를 저장하면 다음 조회 때 그대로 재사용되므로 저장하지 않는다.
        generated_at = datetime.now(KST)
        st.warning(
            f"AI 분석 중 {len(errors)}건의 오류가 있어 결과를 저장하지 않았습니다. "
            "잠시 후 '다시 수집·분석'을 눌러 주세요. 계속 실패하면 .env의 API Key와 모델 설정을 확인하세요.\n\n"
            + "\n".join(f"- {message}" for message in errors[:3])
        )
    else:
        generated_at = storage.save_briefing(start, end, issues, status, items, provider, model)
    st.session_state.pop("last_status", None)
    st.session_state["briefing"] = {
        "period": (start, end), "issues": issues, "items": items, "status": status,
        "generated_at": generated_at, "provider": provider, "model": model, "from_cache": False,
    }


# ── 화면 도우미 (HTML 조각) ──────────────────────────────────────
# 화면에 넣는 모든 글자는 esc()로 감싸서 HTML로 해석되지 않게 한다. (AI 응답·원문 제목 보호)


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def badge(importance: str) -> str:
    """중요도 배지 (채운 모양). 내부 값(High 등)은 CSS 이름에만 쓰고 화면에는 한글로 표시"""
    return f'<span class="badge badge-{esc(importance)}">{esc(IMPORTANCE_LABELS.get(importance, importance))}</span>'


def chip(category: str) -> str:
    """카테고리 칩 (테두리 모양)"""
    return f'<span class="chip chip-{esc(category)}">{esc(CATEGORIES.get(category, category))}</span>'


def link(url: str, text: str, css_class: str = "") -> str:
    """새 탭에서 열리는 링크"""
    return f'<a class="{css_class}" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(text)}</a>'


# ── 사이드바 ────────────────────────────────────────────────────


def side_step(number: int, title: str) -> None:
    """사이드바 단계 제목 (① 조회기간 → ② 검색·필터 → ③ 수집 상태)"""
    st.sidebar.html(f'<div class="side-step"><span>{number}</span>{esc(title)}</div>')


def reset_filters() -> None:
    """검색·필터를 처음 상태(키워드 없음, 모든 중요도·카테고리 선택)로 되돌린다."""
    st.session_state["filter_keyword"] = ""
    st.session_state["filter_importances"] = list(IMPORTANCE_LEVELS)
    st.session_state["filter_categories"] = list(CATEGORIES)


def render_sidebar() -> dict:
    """조회기간·생성 버튼·필터·수집 상태. 사용자가 고른 값들을 dict로 돌려준다."""
    st.sidebar.html(
        '<div class="side-brand">⚖️ 공정거래 이슈 브리핑</div>'
        '<div class="side-brand-sub">기간 선택 → 브리핑 생성 → 필터로 좁혀 보기</div>'
    )

    side_step(1, "조회기간 선택")
    option = st.sidebar.radio("기간 선택", PERIOD_OPTIONS, label_visibility="collapsed")

    today = datetime.now(KST).date()
    custom_range = None
    if option == "직접 선택":
        custom_range = st.sidebar.date_input(
            "시작일 ~ 종료일", value=(today - timedelta(days=7), today), max_value=today
        )

    period = get_period(option, custom_range)
    error = "시작일과 종료일을 모두 선택하세요." if period is None else validate_period(*period, today)
    if error:
        st.sidebar.warning(error)
    else:
        st.sidebar.caption(f"📅 {period[0]} ~ {period[1]}")

    col1, col2 = st.sidebar.columns(2)
    generate = col1.button("브리핑 생성", type="primary", disabled=error is not None, width="stretch")
    regenerate = col2.button("다시 수집·분석", disabled=error is not None, width="stretch",
                             help="저장된 결과를 무시하고 다시 수집·분석합니다")

    side_step(2, "검색·필터")
    if "filter_keyword" not in st.session_state:  # 처음 한 번만 필터 기본값을 넣는다
        reset_filters()
    keyword = st.sidebar.text_input("키워드", key="filter_keyword", placeholder="제목·내용·점검사항·출처 검색")
    # 여러 개를 고를 수 있는 버튼형 선택 (모두 해제하면 전체를 보여준다)
    importances = st.sidebar.pills(
        "중요도", IMPORTANCE_LEVELS, selection_mode="multi", key="filter_importances",
        format_func=IMPORTANCE_LABELS.get, help="모두 해제하면 전체 중요도를 보여줍니다",
    )
    categories = st.sidebar.pills(
        "카테고리", list(CATEGORIES), selection_mode="multi", key="filter_categories",
        format_func=CATEGORIES.get, help="모두 해제하면 전체 카테고리를 보여줍니다",
    )
    st.sidebar.button("필터 초기화", on_click=reset_filters, width="stretch")

    return {
        "period": period if not error else None,
        "generate": generate,
        "regenerate": regenerate,
        "keyword": keyword,
        "categories": categories,
        "importances": importances,
    }


def render_source_status(status: list[dict]) -> None:
    """사이드바에 소스별 수집 상태를 보여준다."""
    side_step(3, "수집 상태")
    for s in status:
        if s["ok"]:
            st.sidebar.success(f"{s['source']}: {s['count']}건")
        else:
            st.sidebar.warning(f"{s['source']}: 수집 실패 — {s['error']}")


# ── 본문 ────────────────────────────────────────────────────────


def render_header(briefing: dict | None) -> None:
    """서비스명과 현재 브리핑 정보(조회기간·생성 시각·AI)"""
    meta = ""
    if briefing:
        start, end = briefing["period"]
        cache_text = "저장된 결과" if briefing["from_cache"] else "새로 수집·분석한 결과"
        meta = (
            f'<div class="app-meta">조회기간 <b>{esc(start)} ~ {esc(end)}</b><br>'
            f'{cache_text} · 생성 {briefing["generated_at"]:%Y-%m-%d %H:%M} · '
            f'AI {esc(briefing["provider"])}/{esc(briefing["model"])}</div>'
        )
    st.html(
        '<div class="app-header"><div>'
        '<div class="app-title">공정거래 이슈 브리핑</div>'
        '<div class="app-subtitle">공정거래위원회 공개자료 기반 · AI 분석 준법 브리핑</div>'
        f"</div>{meta}</div>"
    )


def render_summary(briefing: dict, issues: list[Issue]) -> None:
    """요약 지표: 조회기간, 수집 자료 수, 분석 이슈 수, 중요도별 이슈 수"""
    start, end = briefing["period"]
    excluded = sum(1 for item in briefing["items"] if item.is_relevant is False)
    cards = [
        ("", "조회기간", f"{start:%m.%d} ~ {end:%m.%d}", "small"),
        ("", "수집 자료", f"{len(briefing['items'])}건", ""),
        ("", "분석 이슈", f"{len(issues)}개", ""),
    ]
    cards += [(f"kpi-{level}", f"중요도 {IMPORTANCE_LABELS[level]}", sum(1 for i in issues if i.importance == level), "") for level in IMPORTANCE_LEVELS]
    html_cards = "".join(
        f'<div class="kpi {css}"><div class="kpi-label">{esc(label)}</div>'
        f'<div class="kpi-value {size}">{esc(value)}</div></div>'
        for css, label, value, size in cards
    )
    st.html(f'<div class="kpi-grid">{html_cards}</div>')
    if excluded:
        st.caption(f"AI가 준법업무와 관련 없다고 판단해 제외한 자료 {excluded}건은 아래 '수집 자료 목록'에서 확인할 수 있습니다.")


def render_top5(issues: list[Issue]) -> None:
    """주요 공정거래 이슈 Top 5 (필터와 관계없이 전체 이슈 기준)"""
    header = (
        f'<div class="section-title">📌 주요 공정거래 이슈 Top {TOP_N}</div>'
        '<div class="section-desc">전체 이슈 기준 · 중요도 → 공식 출처 → 관련 자료 수 → 최신순 (사이드바 필터는 적용하지 않음)</div>'
    )
    if not issues:
        st.html(f'<div class="top5">{header}<p>해당 기간에 수집된 이슈가 없습니다.</p></div>')
        return
    rows = "".join(
        '<div class="top5-row">'
        f'<div class="top5-rank">{rank}</div><div>'
        f'<div class="top5-title">{link(issue.main_item.url, issue.title)}</div>'
        f'<div class="top5-meta">{badge(issue.importance)}{chip(issue.category)}'
        f"<span>{esc(issue.main_item.source)} · {issue.published_at:%Y-%m-%d}</span></div>"
        "</div></div>"
        for rank, issue in enumerate(issues[:TOP_N], start=1)
    )
    st.html(f'<div class="top5">{header}{rows}</div>')


def issue_card_html(issue: Issue) -> str:
    """이슈 카드: 제목 → 중요도·카테고리·공개일·출처 → 핵심 내용 → 준법 점검사항 → 원문 링크"""
    parts = [f'<div class="issue-card imp-{esc(issue.importance)}">']
    parts.append(f'<div class="issue-title">{esc(issue.title)}</div>')

    related_text = f" · 관련 자료 {len(issue.related_items)}건" if issue.related_items else ""
    parts.append(
        f'<div class="issue-meta">{badge(issue.importance)}{chip(issue.category)}'
        f"<span>{issue.published_at:%Y-%m-%d} · {esc(issue.main_item.source)}{related_text}</span></div>"
    )

    if issue.importance_reason == SUMMARY_FAILED:
        parts.append('<div class="issue-failed">AI 요약에 실패했습니다. 원문을 확인하세요.</div>')
    elif issue.importance_reason:
        parts.append(f'<div class="issue-reason">중요도 판단: {esc(issue.importance_reason)}</div>')

    parts.append('<div class="label">핵심 내용 · 원문 요약</div><ul class="key-points">')
    parts.extend(f"<li>{esc(point)}</li>" for point in issue.key_points)
    parts.append("</ul>")

    if issue.checkpoints:
        parts.append('<div class="checkpoints"><div class="label">준법 점검사항 · 우리 회사가 확인할 사항 (AI 제안)</div><ul>')
        parts.extend(f"<li>{esc(checkpoint)}</li>" for checkpoint in issue.checkpoints)
        parts.append("</ul></div>")

    parts.append(f'<div class="issue-links">{link(issue.main_item.url, "원문 보기 ↗", "source-link")}')
    if issue.related_items:
        items = "".join(
            f"<li>{esc(item.source)} · {link(item.url, item.title)} · {item.published_at:%Y-%m-%d}</li>"
            for item in issue.related_items
        )
        parts.append(f'<details class="related"><summary>관련 자료 {len(issue.related_items)}건 보기</summary><ul>{items}</ul></details>')
    parts.append("</div></div>")
    return "".join(parts)


def render_category_tabs(all_issues: list[Issue], filtered: list[Issue]) -> None:
    """카테고리 탭: 전체 + 5개 카테고리. 탭 제목의 건수는 필터 적용 후 기준."""
    tab_defs = [("전체", None)] + [(name, code) for code, name in CATEGORIES.items()]
    labels = []
    for name, code in tab_defs:
        count = len(filtered) if code is None else sum(1 for issue in filtered if issue.category == code)
        labels.append(f"{name} ({count})")

    for tab, (name, code) in zip(st.tabs(labels), tab_defs):
        with tab:
            shown = filtered if code is None else [issue for issue in filtered if issue.category == code]
            in_period = all_issues if code is None else [issue for issue in all_issues if issue.category == code]
            if not in_period:
                st.info("해당 기간에 수집된 이슈가 없습니다.")
            elif not shown:
                st.info("조건에 맞는 이슈가 없습니다. 사이드바의 검색·필터 조건을 확인하세요.")
            for issue in shown:
                st.html(issue_card_html(issue))


def render_collected_items(items: list) -> None:
    """수집한 원자료 목록 (AI 판단 결과 포함)"""
    with st.expander(f"수집 자료 목록 ({len(items)}건)"):
        rows = [
            {
                "공개일": item.published_at.strftime("%Y-%m-%d"),
                "제목": item.title,
                "출처": item.source,
                "AI 판단": "관련 없음(제외)" if item.is_relevant is False else "관련",
                "원문 링크": item.url,
            }
            for item in items
        ]
        st.dataframe(
            rows,
            hide_index=True,
            column_config={"원문 링크": st.column_config.LinkColumn("원문 링크", display_text="원문 보기")},
        )


def render_empty_state() -> None:
    """브리핑을 만들기 전 첫 화면 안내"""
    st.html(
        '<div class="empty-state"><b>공정거래 이슈 브리핑을 만들어 보세요.</b><ol>'
        "<li>왼쪽 <b>① 조회기간</b>에서 기간을 고릅니다. (기본: 최근 1일)</li>"
        "<li><b>브리핑 생성</b>을 누르면 공정위 보도자료를 수집하고 AI가 분석합니다. 이미 만든 기간은 저장된 결과가 바로 나옵니다.</li>"
        "<li><b>② 검색·필터</b>로 카테고리·중요도·키워드별로 좁혀 봅니다.</li>"
        "</ol></div>"
    )


def main() -> None:
    st.set_page_config(page_title="공정거래 이슈 브리핑", page_icon="⚖️", layout="wide")
    st.html(STYLE_PATH)  # style.css 적용

    header_slot = st.container()  # 헤더 자리를 먼저 잡아 두어 진행 상태 상자가 헤더 아래에 나오게 한다
    choice = render_sidebar()
    if choice["period"] and (choice["generate"] or choice["regenerate"]):
        create_briefing(*choice["period"], force=choice["regenerate"])

    briefing = st.session_state.get("briefing")
    with header_slot:
        render_header(briefing)

    if briefing is None:
        if "last_status" in st.session_state:
            render_source_status(st.session_state["last_status"])
            st.error("모든 데이터 소스 수집에 실패해 브리핑을 만들 수 없습니다. 사이드바의 수집 상태를 확인하세요.")
        else:
            render_empty_state()
            if get_llm_provider() == "mock":
                st.warning("현재 테스트(mock) 모드입니다. 실제 AI가 아닌 가짜 응답으로 흐름만 확인합니다. (.env의 LLM_PROVIDER로 변경)")
        return

    render_source_status(briefing["status"])
    issues = sort_issues(briefing["issues"])
    if briefing["provider"] == "mock":
        st.warning("테스트(mock) 모드 결과입니다. 요약·준법 점검사항·중요도는 가짜 응답입니다.")

    if not briefing["items"]:
        st.info("해당 기간에 등록된 공정위 보도자료가 없습니다. 조회기간을 늘려 보거나, "
                "오늘 새로 올라온 자료는 '다시 수집·분석'으로 확인하세요.")

    render_summary(briefing, issues)
    render_top5(issues)

    filtered = filter_issues(issues, choice["categories"], choice["importances"], choice["keyword"])
    st.html(
        '<div class="section-title">전체 이슈</div>'
        f'<div class="section-desc">필터 적용 결과 {len(filtered)}건 / 전체 {len(issues)}건 · 중요도순 정렬</div>'
    )
    render_category_tabs(issues, filtered)

    render_collected_items(briefing["items"])
    st.html('<div class="footer-note">AI 요약 · 참고용이며 법률 자문이 아닙니다. 중요한 판단은 반드시 원문을 확인하세요.</div>')


main()
