"""
llm_client.py — LLM(AI) 호출을 담당한다. LLM 제공사를 바꿀 때 이 파일만 고치면 된다.

외부에서 쓰는 함수
  - ask_json(system_prompt, user_prompt) -> dict : LLM에 묻고 JSON 응답을 dict로 받는다.
  - check_settings() : 분석 시작 전에 제공사·API Key·SDK 설치 여부를 확인한다.

제공사는 .env의 LLM_PROVIDER(mock | openai | anthropic)로 고르고,
모델은 LLM_MODEL(비우면 config.DEFAULT_MODELS)로 고른다.

새 제공사 추가 방법
  1) _call_새제공사(system_prompt, user_prompt, model) -> str 함수를 만든다. (응답 텍스트를 돌려줌)
  2) 아래 PROVIDERS 사전에 한 줄 추가한다.
"""

import json
import re

import config


# ── 제공사별 호출 함수 ─────────────────────────────────────────
# 모두 같은 형태: (시스템 프롬프트, 사용자 프롬프트, 모델명) -> 응답 텍스트


def _call_openai(system_prompt: str, user_prompt: str, model: str) -> str:
    """OpenAI 공식 Python SDK(openai)로 호출한다."""
    from openai import OpenAI  # 이 제공사를 쓸 때만 불러온다

    client = OpenAI(api_key=config.get_secret("OPENAI_API_KEY"))
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "developer", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},  # JSON 객체로만 답하게 함
        max_completion_tokens=config.LLM_MAX_TOKENS,
    )
    return completion.choices[0].message.content or ""


def _call_anthropic(system_prompt: str, user_prompt: str, model: str) -> str:
    """Anthropic 공식 Python SDK(anthropic)로 호출한다."""
    import anthropic  # 이 제공사를 쓸 때만 불러온다

    client = anthropic.Anthropic(api_key=config.get_secret("ANTHROPIC_API_KEY"))
    request = dict(
        model=model,
        max_tokens=config.LLM_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if model == config.DEFAULT_MODELS["anthropic"]:
        # 기본 모델은 안전 필터가 요청을 거절하면 서버가 다른 모델로 자동 재시도하도록 한다.
        response = client.beta.messages.create(
            **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    else:
        response = client.messages.create(**request)

    if response.stop_reason == "refusal":
        raise RuntimeError("AI가 요청을 거절했습니다.")
    # 응답에는 여러 종류의 블록이 올 수 있으므로 text 블록만 모은다.
    return "".join(block.text for block in response.content if block.type == "text")


def _call_mock(system_prompt: str, user_prompt: str, model: str) -> str:
    """
    API 없이 가짜 응답을 만든다. (교육·화면 테스트용)
    사용자 프롬프트 첫 줄의 "작업: ..."으로 요청 종류를 구분하고, 자료의 제목·내용으로 그럴듯하게 채운다.
    """
    docs = re.findall(r'<item id="([^"]+)">\s*제목: (.*?)\n.*?내용: (.*?)</item>', user_prompt, re.S)
    task = user_prompt.splitlines()[0]

    if "분류" in task:
        results = [{"id": i, "is_relevant": True, "category": _mock_category(title)} for i, title, _ in docs]
        return json.dumps({"results": results}, ensure_ascii=False)

    if "중복 통합" in task:
        return json.dumps({"groups": [[i] for i, _, _ in docs]})  # 모두 따로 둔다

    # 이슈 분석
    if not docs:
        return "{}"
    _, title, content = docs[0]
    body = content.split("\n", 1)[-1]  # 첫 줄([구분] 담당부서)은 빼고 본문만
    sentences = [s.strip()[:120] for s in re.split(r"(?<=다\.)\s+", body.strip()) if len(s.strip()) > 10]
    return json.dumps({
        "title": title[:40],
        "key_points": ["[mock] " + s for s in sentences[:3]] or [f"[mock] {title}"],
        "checkpoints": [
            "[mock] 관련 부서의 거래 관행이 해당 사안과 유사한지 점검",
            "[mock] 사내 공정거래 자율준수 교육 자료에 반영할 필요가 있는지 검토",
        ],
        "importance": "High" if re.search("과징금|고발|개정", title) else "Medium",
        "importance_reason": "[mock] 제목의 키워드로 정한 가짜 중요도입니다.",
    }, ensure_ascii=False)


def _mock_category(title: str) -> str:
    """mock용: 제목 키워드로 카테고리를 대충 정한다."""
    if re.search("판결|법원|소송", title):
        return "COURT"
    if re.search("해외|EU|미국|국제", title):
        return "GLOBAL"
    if re.search("제재|과징금|시정명령|고발", title):
        return "SANCTION"
    if re.search("개정|입법|시행령|고시|법|정책|추진|예고", title):
        return "POLICY"
    return "CORPORATE"


PROVIDERS = {
    "mock": _call_mock,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


# ── 공개 함수 ─────────────────────────────────────────────────


def get_model() -> str:
    """사용할 모델명 (LLM_MODEL이 비어 있으면 제공사 기본값)"""
    provider = config.get_llm_provider()
    return config.get_secret("LLM_MODEL") or config.DEFAULT_MODELS.get(provider, "")


def check_settings() -> None:
    """제공사 이름, API Key, SDK 설치 여부를 확인한다. 문제가 있으면 이유를 담아 RuntimeError를 낸다."""
    provider = config.get_llm_provider()
    if provider not in PROVIDERS:
        raise RuntimeError(f"지원하지 않는 LLM_PROVIDER입니다: {provider} (mock | openai | anthropic)")
    if provider == "mock":
        return

    key_name = config.API_KEY_NAMES[provider]
    if not config.get_secret(key_name):
        raise RuntimeError(f"{key_name}가 설정되지 않았습니다. .env 파일에 입력하세요.")
    try:
        __import__(provider)  # openai 또는 anthropic 패키지
    except ImportError:
        raise RuntimeError(f"{provider} 패키지가 없습니다. pip install -r requirements.txt 를 실행하세요.")


def describe_error(error: Exception) -> str:
    """
    오류를 화면·로그에 보여줄 짧은 문구로 바꾼다.
    제공사 오류 문구에는 API Key 일부(예: sk-proj-****abcd)가 들어 있을 수 있으므로 가린다.
    """
    text = f"{type(error).__name__}: {error}"
    for key_name in config.API_KEY_NAMES.values():  # 설정된 키 값이 그대로 들어 있으면 가림
        key = config.get_secret(key_name)
        if key:
            text = text.replace(key, "***")
    text = re.sub(r"sk-[A-Za-z0-9_\-\*]+", "sk-***", text)  # 일부만 가려진 키 표시도 가림
    return text[:200]


def parse_json(text: str) -> dict:
    """AI 응답 텍스트에서 JSON 객체를 꺼낸다. (```json 코드블록이나 앞뒤 설명이 섞여도 처리)"""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("응답에서 JSON을 찾지 못했습니다.")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("응답이 JSON 객체가 아닙니다.")
    return data


def ask_json(system_prompt: str, user_prompt: str) -> dict:
    """LLM에 요청하고 응답을 dict로 돌려준다. JSON 해석에 실패하면 1번 더 요청한 뒤 예외를 낸다."""
    check_settings()
    call = PROVIDERS[config.get_llm_provider()]
    model = get_model()

    last_error = None
    for _ in range(2):  # 최초 1회 + 재시도 1회
        text = call(system_prompt, user_prompt, model)
        try:
            return parse_json(text)
        except (ValueError, json.JSONDecodeError) as error:
            last_error = error
    raise RuntimeError(f"AI 응답을 JSON으로 해석하지 못했습니다: {last_error}")
