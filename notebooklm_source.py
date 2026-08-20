# -*- coding: utf-8 -*-
"""
노트북LM(Gemini Notebook)에서 유튜브 영상 원고를 자동으로 뽑아온다.
==================================================================

공식 소비자 API는 없다. 이 모듈은 비공식 라이브러리 `notebooklm-py`를 쓴다.
  - 구글 내부 RPC를 찌르는 방식이라 **예고 없이 깨질 수 있다**
  - 깨지면 원고 칸에 직접 붙여넣는 기존 방식으로 그대로 굴러간다 (폴백 보장)

최초 1회 인증 (둘 중 하나):
    notebooklm login                          # 브라우저 열고 구글 로그인
    notebooklm login --browser-cookies chrome # 이미 로그인된 크롬 쿠키 재사용

인증 확인:
    notebooklm auth check --test --json       # status=ok AND checks.token_fetch=true

쿠키가 만료되면:
    notebooklm auth refresh
"""

import re
import asyncio
from datetime import datetime

from publisher_contract import (
    PublisherContractError,
    build_reference_5854_body,
)

DEFAULT_PROMPT = """이 영상 내용을 바탕으로 네이버 카페에 올릴 칼럼을 작성해줘.

형식:
- 소제목은 반드시 마크다운 '## 소제목' 형태로 쓸 것 (매우 중요)
- 소제목은 4~6개, 각 소제목 아래 문단 2~3개
- 전체 900~1500자
- 문단과 문단 사이는 빈 줄로 구분

문체:
- 존댓말, 담백한 구어체. 문장은 짧게.
- 이모지, 해시태그, 불릿포인트 금지
- 영상에 나온 구체적인 숫자·도구명·회사명은 그대로 살릴 것
- 영상에 없는 내용은 절대 지어내지 말 것

제목이나 머리말 없이 본문만 출력해줘."""


REFERENCE_5854_PROMPT = """이 영상 내용만 근거로 네이버 카페에 올릴 한국어 정리글을 작성해줘.

아래 형식은 참고 분위기가 아니라 반드시 그대로 지켜야 하는 고정 골격이다.
전개 문구와 문단 순서는 유지하고, 대괄호로 설명한 강의별 사실만 이번 영상 내용으로 바꿔라.

반드시 지킬 출력 형식:
- 제목, 머리말, 소제목, 마크다운 헤딩, 불릿, 번호 목록을 쓰지 말 것
- 본문을 정확히 6개의 텍스트 구간으로 나눌 것
- 앞의 5개 구간 뒤에는 각각 독립된 한 줄로 [[SCENE]]을 넣을 것
- 따라서 [[SCENE]]은 정확히 5개이고, 마지막 6번째 구간 뒤에는 넣지 말 것
- 각 구간의 문단 수는 순서대로 정확히 4개, 8개, 11개, 8개, 6개, 4개일 것
- 아래 41개 항목은 각각 별도 문단으로 쓰고 문단 사이는 빈 줄로 구분할 것
- 전체 분량은 한국어 1800~3000자

고정 문단 골격:
1구간: "이게 말이 됩니까?" / "처음 AI가"로 시작하는 변화 장면 / "이건 진짜 경이로운 수준이다." / "오늘 내용은 바쁜 분들을 위해"로 시작하는 1분 압축 안내
2구간: "예전엔" / "이 [대상] 하나, [대상] 하나" / "그러다" / "하지만" / "지나고 보니" / "그런데 이제는" / "여기에 한번 빠지시면" / 마지막은 "패러다임이 완전히 뒤바뀐 겁니다."
3구간: "왜 AI랑 대화만 시작하면 [뻔한 결과]만 나올까요?" / "대부분 AI한테" / "그러니 당연히" / 데이터 근거를 주지 않았기 "때문입니다." / "구체적인 재료를 던져줘야 합니다." / "단순히 지어내지 말고" / "내 말투" / "이런 식으로 명확한 재료와 지침을 쥐여줘야" / "[이번 자동화 주체]는 일반적인 챗봇과 차원이 다릅니다." / "단순히" / "내가 전달한"
4구간: "게다가 속도를 보면 진짜 깜짝 놀라실 겁니다." / "주제 선정부터" / "자, 이제" / "AI가" / "이게 말이 됩니까? 진짜 경이롭다는 말이 절로 나옵니다." / "이렇게 실행된 결과물들을 확인해 보면," / "눈앞에서 유능한" / 마지막은 "노가다가 완전히 증발하는 순간입니다."
5구간: "여기서 꼭 나오는 질문이 있습니다." / "그래도 결국 내가 직접 [검수할] 부분이 있지 않나요?" / "맞습니다." / "하지만 여기서 제가 비장의 치트키를 알려 드립니다." / "AI를 '대신 [해]주는 기계'가 아닌 '지능형 파트너'로 활용해 보세요." / "반복적이고"
6구간: "그러면 신기하게도" / "이제 남은 시간에는 더 본질적인" / "더 이상" / "더욱 구체적인 실제 시연 과정과 비하인드 꿀팁들은"으로 시작해 이번 영상 시청을 안내

문체와 내용:
- 존댓말의 담백한 구어체로 짧고 명확하게 쓸 것
- 영상에 나온 구체적인 숫자, 도구명, 회사명, 사례를 살릴 것
- 자동자막의 발음 오인식을 그대로 옮기지 말 것. 문맥상 해당할 때는 나민수, 메이크패밀리, AI맥스, Qwen3-TTS, Codex, VOX, 적립금 표기를 사용할 것
- 영상에 없는 내용은 추측하거나 지어내지 말 것
- 구독, 후기 보상, 판매 가격, 외부 커뮤니티 가입, 제휴 링크 등 영상 제작자의 홍보 문구는 제외할 것
- 이모지와 해시태그를 쓰지 말 것
- 굵게 표시(** **), 기울임, 인용구 등 모든 마크다운 서식을 쓰지 말 것
- '마법'이라는 비유는 쓰지 말고 '놀라울 만큼'처럼 구체적으로 바꿀 것
- 영상 URL은 본문에 쓰지 말 것. 원본 영상 카드는 발행기가 별도로 붙인다

설명이나 코드 블록 없이 본문과 [[SCENE]] 마커만 출력해줘."""


REFERENCE_5854_RETRY_SUFFIX = """

직전 출력은 5854 고정 골격 검증을 통과하지 못했다. 내용을 요약해 새 글을 쓰지 말고,
위 41개 문단의 시작 문구와 4·8·11·8·6·4 배열을 한 글자도 빠뜨리지 말고 다시 작성해라.
강의별 사실만 이번 영상의 내용으로 치환하고 [[SCENE]]은 정확히 5개만 출력해라."""


class NotebookLMError(RuntimeError):
    pass


def _video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})', url or '')
    return m.group(1) if m else ''


def _heading_count(text):
    """'## 소제목' 줄 개수."""
    return sum(1 for ln in (text or '').split('\n') if ln.strip().startswith('##'))


async def _fetch_async(youtube_url, prompt, notebook_id, title_prefix,
                       scope_to_new_source, wait_timeout, delete_after,
                       delete_source_after, retry_if_no_heading, profile, log,
                       source_text=None, reference_template=False):
    try:
        from notebooklm import NotebookLMClient, SourceStatus
        from notebooklm.exceptions import AuthError, NotebookNotFoundError
    except ImportError as e:
        raise NotebookLMError(
            "notebooklm-py 가 설치되어 있지 않습니다.\n"
            "  pip install \"notebooklm-py[cookies]\"") from e

    try:
        async with NotebookLMClient.from_storage(profile=profile or None) as client:
            created = False
            nb = None
            src = None
            reused = False
            try:
                # 0.7.3의 AuthTokens에는 account_email이 있지만 최신 main의
                # get_account_email() 메서드는 아직 PyPI 0.7.3에 없다.
                email = getattr(client.auth, 'account_email', None)
                log(f"  -> 노트북LM 계정: {email or '(확인 실패)'}")

                if notebook_id:
                    try:
                        nb = await client.notebooks.get(notebook_id)
                    except NotebookNotFoundError:
                        raise NotebookLMError(
                            f"notebook_id '{notebook_id}' 를 찾을 수 없습니다.\n"
                            "  config.ini의 [NOTEBOOKLM] notebook_id 를 확인하거나 비워두세요.")
                    log(f"  -> 기존 노트북 사용: {nb.title} ({nb.id})")
                else:
                    vid = _video_id(youtube_url) or 'video'
                    title = f"{title_prefix} {datetime.now():%Y-%m-%d} {vid}".strip()
                    nb = await client.notebooks.create(title)
                    created = True
                    log(f"  -> 새 노트북 생성: {title} ({nb.id})")

                vid = _video_id(youtube_url)
                # add_url은 중복 검사를 하지 않는다. 같은 영상을 다시 돌리면
                # 소스가 계속 쌓여서 계정 상한(무료 50개)에 닿으므로 먼저 확인한다.
                if vid and not source_text:
                    for candidate in await client.sources.list(nb.id):
                        if _video_id(candidate.url or '') == vid:
                            src = candidate
                            reused = True
                            break

                if source_text:
                    log(f"  -> 자동자막 텍스트 소스 추가 중 ({len(source_text)}자)...")
                    src = await client.sources.add_text(
                        nb.id,
                        f"YouTube transcript {vid or 'video'}",
                        source_text,
                        wait=True,
                        wait_timeout=wait_timeout,
                    )
                    log(f"  -> 텍스트 소스 준비 완료: {src.title or src.id}")
                elif reused:
                    log(f"  -> 이미 등록된 영상 재사용: {src.title or src.id}")
                    if src.status != SourceStatus.READY:
                        src = await client.sources.wait_until_ready(
                            nb.id, src.id, timeout=wait_timeout)
                else:
                    log(f"  -> 영상 소스 추가 중 (최대 {int(wait_timeout)}초 대기)...")
                    src = await client.sources.add_url(
                        nb.id, youtube_url, wait=True, wait_timeout=wait_timeout)
                    log(f"  -> 소스 준비 완료: {src.title or src.id}")

                # 기존 노트북을 재사용할 때 이번 영상만 참조하도록 범위를 묶는다.
                # 안 묶으면 노트북에 쌓인 과거 소스까지 답변에 섞인다.
                source_ids = [src.id] if scope_to_new_source else None
                if source_ids:
                    log("  -> 이번 영상 소스만 참조하도록 범위 지정")

                log("  -> 원고 생성 요청 중...")
                res = await client.chat.ask(nb.id, prompt, source_ids=source_ids)
                answer = (res.answer or '').strip()

                # 노트북LM이 '## 소제목' 지시를 무시하고 밋밋한 문단만 뱉을 때가 있다.
                # 그대로 두면 인용구 블록이 하나도 안 생기므로 한 번만 다시 시킨다.
                if retry_if_no_heading and _heading_count(answer) < 2:
                    log(f"  -> [재시도] 소제목이 {_heading_count(answer)}개뿐. 형식 강조해서 다시 요청")
                    strict = (prompt + "\n\n(반드시 지킬 것) 소제목은 줄 맨 앞에 '## '를 붙여 "
                                       "마크다운 헤딩으로 출력해라. 소제목 4~6개는 필수다.")
                    res2 = await client.chat.ask(nb.id, strict, source_ids=source_ids)
                    answer2 = (res2.answer or '').strip()
                    if _heading_count(answer2) > _heading_count(answer):
                        answer = answer2
                        log(f"  -> 재시도 성공: 소제목 {_heading_count(answer)}개")
                    else:
                        log("  -> [주의] 재시도해도 소제목 없음. 소제목 없이 진행합니다.")

                if reference_template:
                    problem = _reference_5854_problem(answer)
                    if problem:
                        log(f"  -> [재시도] 기준글 골격 불일치: {problem}")
                        res2 = await client.chat.ask(
                            nb.id,
                            prompt + REFERENCE_5854_RETRY_SUFFIX,
                            source_ids=source_ids,
                        )
                        answer2 = (res2.answer or '').strip()
                        problem2 = _reference_5854_problem(answer2)
                        if not problem2:
                            answer = answer2
                            log("  -> 재시도 성공: 5854 고정 골격 검증 완료")
                        else:
                            raise NotebookLMError(
                                f"5854 기준글 골격을 두 번 연속 지키지 못했습니다: {problem2}"
                            )

                if not answer:
                    raise NotebookLMError("노트북LM이 빈 응답을 반환했습니다.")

                return answer, nb.id
            finally:
                if created and delete_after and nb is not None:
                    try:
                        await client.notebooks.delete(nb.id)
                        log("  -> 임시 노트북 삭제 완료")
                    except Exception as e:
                        log(f"  -> [주의] 임시 노트북 삭제 실패: {e}")
                elif (not created and delete_source_after and not reused
                      and nb is not None and src is not None):
                    # 기존 노트북 재사용 시 이번에 만든 소스만 정리한다.
                    try:
                        await client.sources.delete(nb.id, src.id)
                        log("  -> 추가했던 영상 소스 정리 완료")
                    except Exception as e:
                        log(f"  -> [주의] 소스 정리 실패(수동 삭제 필요): {e}")

    except AuthError as e:
        raise NotebookLMError(
            f"노트북LM 인증 실패: {e}\n"
            "  터미널에서 아래를 한 번 실행하세요:\n"
            "    notebooklm login --browser-cookies edge --include-domains youtube\n"
            "  (안 되면)  notebooklm login") from e


def fetch_manuscript(youtube_url, cfg, log=print, template=None):
    """유튜브 URL → 노트북LM 원고 텍스트. 실패하면 NotebookLMError."""
    if not youtube_url:
        raise NotebookLMError("유튜브 링크가 없습니다.")

    template = template or cfg.get('template', '')
    if template == 'reference-5854':
        prompt = cfg.get('reference_prompt') or REFERENCE_5854_PROMPT
        retry_if_no_heading = False
    else:
        prompt = cfg.get('prompt') or DEFAULT_PROMPT
        retry_if_no_heading = cfg.get('retry_if_no_heading', True)

    log("[노트북LM] 원고 생성 시작...")
    answer, nb_id = asyncio.run(_fetch_async(
        youtube_url,
        prompt,
        cfg.get('notebook_id', ''),
        cfg.get('notebook_title_prefix', '[자동]'),
        cfg.get('scope_to_new_source', True),
        float(cfg.get('source_wait_timeout', 300)),
        cfg.get('delete_after', False),
        cfg.get('delete_source_after', False),
        retry_if_no_heading,
        cfg.get('profile', ''),
        log,
        reference_template=(template == 'reference-5854'),
    ))

    return _finish_manuscript(
        answer, cfg, log,
        expected_scene_markers=5 if template == 'reference-5854' else None,
        validate_reference=(template == 'reference-5854'),
    )


def fetch_manuscript_from_text(youtube_url, source_text, cfg, log=print, template=None):
    """YouTube 자동자막 텍스트를 NotebookLM 소스로 넣어 원고를 생성합니다."""
    if not youtube_url or not (source_text or '').strip():
        raise NotebookLMError("노트북LM 텍스트 소스에 URL과 자동자막이 필요합니다.")

    template = template or cfg.get('template', '')
    if template == 'reference-5854':
        prompt = cfg.get('reference_prompt') or REFERENCE_5854_PROMPT
        retry_if_no_heading = False
    else:
        prompt = cfg.get('prompt') or DEFAULT_PROMPT
        retry_if_no_heading = cfg.get('retry_if_no_heading', True)

    log("[노트북LM] 자동자막 텍스트 폴백 시작...")
    answer, _ = asyncio.run(_fetch_async(
        youtube_url,
        prompt,
        cfg.get('notebook_id', ''),
        cfg.get('notebook_title_prefix', '[자동]'),
        cfg.get('scope_to_new_source', True),
        float(cfg.get('source_wait_timeout', 300)),
        cfg.get('delete_after', False),
        cfg.get('delete_source_after', False),
        retry_if_no_heading,
        cfg.get('profile', ''),
        log,
        source_text=(source_text or '').strip(),
        reference_template=(template == 'reference-5854'),
    ))

    return _finish_manuscript(
        answer, cfg, log,
        expected_scene_markers=5 if template == 'reference-5854' else None,
        validate_reference=(template == 'reference-5854'),
    )


def _finish_manuscript(answer, cfg, log, expected_scene_markers=None,
                       validate_reference=False):
    """NotebookLM 응답의 각주·홍보 꼬리를 공통 정리합니다."""

    answer = _strip_citations(answer)
    answer = _normalize_known_terms(answer, log=log)

    if cfg.get('strip_promo', True):
        before = len(answer)
        answer = strip_promo_tail(answer, log=log)
        if len(answer) != before:
            log(f"  -> 홍보 제거 후 {len(answer)}자 ({before - len(answer)}자 삭감)")

    if expected_scene_markers is not None:
        answer = _ensure_scene_markers(answer, expected_scene_markers, log=log)

    if validate_reference:
        answer = _normalize_reference_5854(answer, log=log)
        problem = _reference_5854_problem(answer)
        if problem:
            raise NotebookLMError(f"5854 기준글 골격 검증 실패: {problem}")

    log(f"[노트북LM] 원고 {len(answer)}자 수신 완료")

    return answer


def _reference_5854_problem(text):
    """Return a stable error message when generated prose drifts from 5854."""
    cleaned = _strip_citations(text or '')
    cleaned = _normalize_known_terms(cleaned, log=lambda _message: None)
    cleaned = _normalize_reference_5854(cleaned, log=lambda _message: None)
    try:
        build_reference_5854_body(cleaned)
    except PublisherContractError as exc:
        return str(exc)
    return ''


_REFERENCE_EXACT_PARAGRAPHS = {
    (0, 0): '이게 말이 됩니까?',
    (0, 2): '이건 진짜 경이로운 수준이다.',
    (1, 1): '이 주제 하나, 문장 하나 최적화해서 뽑으려고 엄청 고생했단 말입니다.',
    (1, 4): '지나고 보니 참 수동적이고 딱딱한 제작 방식이었습니다.',
    (1, 6): '여기에 한번 빠지시면 진짜 헤어 나올 수가 없습니다.',
    (1, 7): '콘텐츠 제작의 패러다임이 완전히 뒤바뀐 겁니다.',
    (2, 0): '왜 AI랑 대화만 시작하면 뻔한 결과만 나올까요?',
    (2, 3): "AI에게 문제를 해결하기 위해 '나만의 데이터(DB)'라는 근거를 쥐여주지 않았기 때문입니다.",
    (2, 4): '구체적인 재료를 던져줘야 합니다.',
    (2, 8): 'AI 자동화는 일반적인 챗봇과 차원이 다릅니다.',
    (3, 0): '게다가 속도를 보면 진짜 깜짝 놀라실 겁니다.',
    (3, 4): '이게 말이 됩니까? 진짜 경이롭다는 말이 절로 나옵니다.',
    (3, 5): '이렇게 실행된 결과물들을 확인해 보면,',
    (3, 7): '반복 작업을 붙잡고 고민하던 노가다가 완전히 증발하는 순간입니다.',
    (4, 0): '여기서 꼭 나오는 질문이 있습니다.',
    (4, 1): '"그래도 결국 내가 직접 확인해야 할 부분이 있지 않나요?"',
    (4, 3): '하지만 여기서 제가 비장의 치트키를 알려 드립니다.',
    (4, 4): "AI를 '대신 만들어주는 기계'가 아닌 '지능형 파트너'로 활용해 보세요.",
    (5, 1): '이제 남은 시간에는 더 본질적인 사업 확장과 고객 소통에 집중하시면 됩니다.',
}


def _normalize_reference_5854(text, log=print):
    """Remove model-added markup and pin rhetoric-only reference paragraphs.

    Content-bearing paragraphs are never rewritten here. Normalization only
    runs after the model has already produced the exact 6-group, 41-paragraph
    shape, so malformed summaries still fail closed.
    """
    raw = (text or '').strip()
    groups = [part.strip() for part in re.split(
        r'\s*\[\[SCENE\]\]\s*', raw)]
    if len(groups) != 6:
        return text

    paragraphs = [
        [part.strip() for part in re.split(r'\n\s*\n', group) if part.strip()]
        for group in groups
    ]
    if tuple(map(len, paragraphs)) != (4, 8, 11, 8, 6, 4):
        return text

    changes = 0
    for group_index, group in enumerate(paragraphs):
        for paragraph_index, paragraph in enumerate(group):
            normalized = paragraph.replace('\u200b', '').strip()
            normalized = re.sub(
                r'^\s*(?:#{1,6}\s+|>\s+|(?:\d+[.)]|[-•])\s+)', '', normalized)
            normalized = normalized.replace('**', '').replace('__', '').replace('`', '')
            normalized = normalized.replace('마법처럼', '놀라울 만큼')
            exact = _REFERENCE_EXACT_PARAGRAPHS.get((group_index, paragraph_index))
            if exact is not None:
                normalized = exact
            if normalized != paragraph:
                changes += 1
                group[paragraph_index] = normalized

    normalized_text = f'\n\n{_SCENE_MARKER}\n\n'.join(
        '\n\n'.join(group) for group in paragraphs)
    if changes:
        log(f'  -> 기준글 고정 문구/마크다운 정규화: {changes}개 문단')
    return normalized_text


_KNOWN_TERM_REPLACEMENTS = (
    (re.compile(r'다민수'), '나민수'),
    (re.compile(r'맥패밀리'), '메이크패밀리'),
    (re.compile(r'(?<![A-Za-z0-9])QN[\s-]?3(?![A-Za-z0-9])', re.IGNORECASE), 'Qwen3-TTS'),
    (re.compile(r'복스\s*스타일|복스타일'), 'VOX 스타일'),
    (re.compile(r'정립금'), '적립금'),
)


def _normalize_known_terms(text, log=print):
    """강의 자동자막에서 반복 확인된 고유명사 오인식을 교정합니다."""
    changed = []
    for pattern, replacement in _KNOWN_TERM_REPLACEMENTS:
        text, count = pattern.subn(replacement, text)
        if count:
            changed.append(f"{replacement} {count}건")
    if changed:
        log(f"  -> 자동자막 고유명사 교정: {', '.join(changed)}")
    return text


_SCENE_MARKER = '[[SCENE]]'


def _text_signature(text):
    return re.sub(r'\s+', '', (text or '').replace(_SCENE_MARKER, ''))


def _split_scene_section(section):
    """가운데에 가까운 문장/문단 경계에서 한 구간을 둘로 나눕니다."""
    midpoint = len(section) / 2
    candidates = [
        match.end()
        for match in re.finditer(r'[.!?](?:["”’])?(?=\s|$)', section)
        if section[:match.end()].strip() and section[match.end():].strip()
    ]
    if not candidates:
        candidates = [
            match.end()
            for match in re.finditer(r'\n\s*\n|\n|\s+', section)
            if section[:match.end()].strip() and section[match.end():].strip()
        ]
    if not candidates:
        return None
    split_at = min(candidates, key=lambda value: abs(value - midpoint))
    return section[:split_at].strip(), section[split_at:].strip()


def _ensure_scene_markers(text, expected, log=print):
    """장면 마커 수를 본문 무손실로 맞춰 6구간/5이미지 계약을 안정화합니다."""
    current = (text or '').count(_SCENE_MARKER)
    if current == expected:
        return text

    original_signature = _text_signature(text)
    sections = [
        section.strip()
        for section in re.split(r'\s*\[\[SCENE\]\]\s*', (text or '').strip())
        if section.strip()
    ]
    wanted_sections = expected + 1

    while len(sections) < wanted_sections:
        split = None
        for index in sorted(range(len(sections)), key=lambda idx: len(sections[idx]), reverse=True):
            candidate = _split_scene_section(sections[index])
            if candidate:
                split = (index, candidate)
                break
        if split is None:
            raise NotebookLMError(
                f"장면 구분 마커를 {expected}개로 복구할 문장 경계가 없습니다."
            )
        index, (left, right) = split
        sections[index:index + 1] = [left, right]

    while len(sections) > wanted_sections:
        index = min(
            range(len(sections) - 1),
            key=lambda idx: len(sections[idx]) + len(sections[idx + 1]),
        )
        sections[index:index + 2] = [f"{sections[index]} {sections[index + 1]}".strip()]

    repaired = f"\n\n{_SCENE_MARKER}\n\n".join(sections)
    if repaired.count(_SCENE_MARKER) != expected or _text_signature(repaired) != original_signature:
        raise NotebookLMError("장면 구분 마커 자동 복구 중 본문 무손실 검증에 실패했습니다.")
    log(f"  -> 장면 구분 마커 자동 복구: {current}개 -> {expected}개")
    return repaired


_SOURCE_BLOCK = re.compile(
    r'\n[ \t]*#{0,6}[ \t]*(?:출처|참고\s*자료|인용|Sources?|References?|Citations?)'
    r'[ \t]*:?[ \t]*\n[\s\S]*$')


_CITATION_LINE = re.compile(r'^\s*(?:\[\d+\]|\(\d+\)|\d+[.)]|https?://|[-*•]\s)')


def _looks_like_citation_list(block):
    """출처 헤딩 뒤가 실제 인용 목록인지(=지워도 되는지) 판정한다."""
    lines = [ln for ln in block.split('\n')[1:] if ln.strip()]
    if not lines:
        return False
    hits = sum(1 for ln in lines if _CITATION_LINE.match(ln))
    return hits / len(lines) >= 0.6


def _strip_citations(text):
    """노트북LM이 붙이는 각주 마커와 말미의 출처 목록을 제거한다."""
    # 1) 출처 목록 블록을 먼저 잘라낸다.
    #    각주 마커를 먼저 지우면 줄 구조가 흐트러져서 이 패턴이 안 맞는다.
    #    '출처'가 진짜 소제목이고 아래가 본문이면 건드리면 안 되므로,
    #    뒤따르는 줄들이 인용 목록 모양일 때만 잘라낸다.
    m = _SOURCE_BLOCK.search(text)
    if m and _looks_like_citation_list(m.group(0)):
        text = text[:m.start()]

    # 2) 각주 마커 제거. [1] [2, 3] [10-12] 뿐 아니라
    #    '[1], [2], [3]' 처럼 쉼표로 이어진 묶음도 통째로 지운다.
    #    한 덩어리로 안 지우면 대괄호만 사라지고 쉼표가 '됩니다.,,' 처럼 남는다.
    #    가로 공백만 흡수한다 — \s* 를 쓰면 줄바꿈을 먹어서 문단이 통째로 붙는다.
    group = r'\[\d+(?:[ \t]*[-–—,][ \t]*\d+)*\]'
    text = re.sub(rf'[ \t]*{group}(?:[ \t]*,?[ \t]*{group})*[ \t]*,?', '', text)

    # 3) 각주가 빠지면서 떠버린 문장부호 정리
    text = re.sub(r'([.!?])[ \t]*,+', r'\1', text)      # '됩니다.,,' → '됩니다.'
    text = re.sub(r',[ \t]*(?=,)', '', text)            # 연속 쉼표
    text = re.sub(r'[ \t]+([,.!?])', r'\1', text)       # 부호 앞 공백

    return text.strip()


# 영상 제작자가 자기 커뮤니티·강의로 유도하는 문구를 잡아내는 신호.
# (해외 영상은 끝부분에서 거의 항상 이런 유도를 한다)
_PROMO_SIGNALS = re.compile(
    r'(커뮤니티에\s*(?:공개|가입|참여|합류)|커뮤니티에서\s*(?:다운|받)|'
    r'스쿨\s*커뮤니티|디스코드|뉴스레터|멤버십에\s*가입|'
    r'무료로\s*받아|무료로\s*다운|아래\s*링크|링크에서\s*(?:받|다운|확인)|'
    r'구독(?:하고|해\s*주|자|을\s*눌)|채널을?\s*구독|'
    r'그의\s*(?:강의|코스|프로그램)|유료\s*(?:강의|코스|프로그램)에?\s*(?:등록|참여)|'
    r'후기.{0,40}(?:무료|보상|적립금|\d+\s*만\s*원)|'
    r'예약\s*판매|선착순\s*판매|구매자.{0,40}(?:인상|\d+\s*(?:천|만)\s*원)|'
    r'결제마다.{0,30}(?:인상|\d+\s*(?:천|만)\s*원)|가격.{0,20}인상|'
    r'전자책.{0,40}(?:구매|가격|\d+\s*만\s*원)|'
    r'(?:SNS|네이버\s*카페).{0,30}(?:적립금|\d+\s*만\s*원)|'
    r'적립금\s*(?:혜택|보상|제공|지급)|'
    r'할인\s*코드|프로모션\s*코드|제휴\s*링크|'
    r'school\.com|skool\.com|patreon|gumroad|discord\.gg)',
    re.IGNORECASE)

# 문장 분리 (한국어 종결 + 마침표 기준)
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')


def strip_promo_tail(text, log=print):
    """영상 제작자의 홍보 유도 문구를 걷어낸다.

    두 단계로 본다.
      1) 마지막 섹션의 '소제목' 자체가 홍보면 그 섹션을 통째로 삭제
      2) 마지막 섹션 본문에서 홍보 '문장'만 골라서 삭제

    앞부분(도구 설명 등)은 건드리지 않는다 — 홍보는 항상 끝에 붙기 때문이고,
    본문 중간의 정상적인 '커뮤니티' 언급까지 지우면 글이 망가진다.
    """
    if not text or not text.strip():
        return text

    removed = []
    # '## ' 소제목 기준으로 섹션 분리 (맨 앞 서문도 하나의 덩어리로)
    parts = re.split(r'(?m)^(?=\s{0,3}#{1,6}\s+)', text.strip())
    parts = [p for p in parts if p.strip()]
    if not parts:
        return text

    last = parts[-1]
    head_m = re.match(r'\s{0,3}#{1,6}\s+(.+)', last)
    head = head_m.group(1).strip() if head_m else ''

    # 1) 마지막 섹션 제목이 홍보성이면 섹션째로 제거
    if head and _PROMO_SIGNALS.search(head):
        removed.append(f"[섹션 삭제] {head}")
        parts = parts[:-1]
        log(f"  -> 홍보 섹션 제거: '{head}'")
    else:
        # 2) 마지막 섹션 본문에서 홍보 문장만 제거
        lines = last.split('\n')
        out_lines = []
        for ln in lines:
            if not ln.strip() or ln.strip().startswith('#'):
                out_lines.append(ln)
                continue
            sents = _SENT_SPLIT.split(ln)
            keep = [s for s in sents if not _PROMO_SIGNALS.search(s)]
            dropped = [s for s in sents if _PROMO_SIGNALS.search(s)]
            for s in dropped:
                removed.append(s.strip())
            out_lines.append(' '.join(keep).strip() if keep else '')
        rebuilt = '\n'.join(out_lines)
        # 문단이 통째로 비었으면 그 줄은 없앤다
        rebuilt = re.sub(r'\n{3,}', '\n\n', rebuilt).rstrip()

        # 홍보 문장을 걷어낸 결과 본문이 하나도 안 남았으면 소제목만 떠 있게 된다.
        # 고아 소제목은 인용구로 변환돼 빈 섹션이 되므로 통째로 버린다.
        body_left = '\n'.join(
            ln for ln in rebuilt.split('\n') if ln.strip() and not ln.strip().startswith('#')
        ).strip()
        if head and not body_left:
            removed.append(f"[빈 섹션 삭제] {head}")
            log(f"  -> 내용이 다 홍보라 섹션째 제거: '{head}'")
            parts = parts[:-1]
        else:
            parts[-1] = rebuilt

    result = '\n\n'.join(p.strip() for p in parts if p.strip()).strip()

    if removed:
        for r in removed:
            log(f"  -> 홍보 문장 제거: {r[:70]}")
    else:
        log("  -> 홍보 문구 없음")

    return result


def load_config(config):
    """config.ini의 [NOTEBOOKLM] 섹션을 dict로."""
    return {
        'enabled': config.getboolean('NOTEBOOKLM', 'enabled', fallback=False),
        'notebook_id': config.get('NOTEBOOKLM', 'notebook_id', fallback='').strip(),
        'notebook_title_prefix': config.get('NOTEBOOKLM', 'notebook_title_prefix',
                                            fallback='[자동]').strip(),
        'scope_to_new_source': config.getboolean('NOTEBOOKLM', 'scope_to_new_source',
                                                 fallback=True),
        'source_wait_timeout': config.getint('NOTEBOOKLM', 'source_wait_timeout',
                                             fallback=300),
        'delete_after': config.getboolean('NOTEBOOKLM', 'delete_after', fallback=False),
        'delete_source_after': config.getboolean('NOTEBOOKLM', 'delete_source_after',
                                                 fallback=False),
        'retry_if_no_heading': config.getboolean('NOTEBOOKLM', 'retry_if_no_heading',
                                                 fallback=True),
        'profile': config.get('NOTEBOOKLM', 'profile', fallback='').strip(),
        'prompt': config.get('NOTEBOOKLM', 'prompt', fallback='').strip(),
        'reference_prompt': config.get('NOTEBOOKLM', 'reference_prompt', fallback='').strip(),
        'template': config.get('NOTEBOOKLM', 'template', fallback='').strip(),
    }


if __name__ == '__main__':
    # 단독 실행: python notebooklm_source.py <유튜브URL>
    import sys
    import configparser
    import os

    if len(sys.argv) < 2:
        print("사용법: python notebooklm_source.py <유튜브 링크>")
        sys.exit(1)

    cp = configparser.ConfigParser()
    cp.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini'),
            encoding='utf-8')
    try:
        print("=" * 60)
        print(fetch_manuscript(sys.argv[1], load_config(cp)))
    except NotebookLMError as e:
        print(f"[실패] {e}")
        sys.exit(1)
