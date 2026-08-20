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

반드시 지킬 출력 형식:
- 제목, 머리말, 소제목, 마크다운 헤딩, 불릿, 번호 목록을 쓰지 말 것
- 본문을 정확히 6개의 텍스트 구간으로 나눌 것
- 앞의 5개 구간 뒤에는 각각 독립된 한 줄로 [[SCENE]]을 넣을 것
- 따라서 [[SCENE]]은 정확히 5개이고, 마지막 6번째 구간 뒤에는 넣지 말 것
- 각 구간은 1~3개의 자연스러운 문단으로 구성할 것
- 전체 분량은 한국어 1800~2600자

문체와 내용:
- 존댓말의 담백한 구어체로 짧고 명확하게 쓸 것
- 영상에 나온 구체적인 숫자, 도구명, 회사명, 사례를 살릴 것
- 영상에 없는 내용은 추측하거나 지어내지 말 것
- 구독, 외부 커뮤니티 가입, 제휴 링크 등 영상 제작자의 홍보 문구는 제외할 것
- 이모지와 해시태그를 쓰지 말 것

설명이나 코드 블록 없이 본문과 [[SCENE]] 마커만 출력해줘."""


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
                       source_text=None):
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
    ))

    return _finish_manuscript(answer, cfg, log)


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
    ))

    return _finish_manuscript(answer, cfg, log)


def _finish_manuscript(answer, cfg, log):
    """NotebookLM 응답의 각주·홍보 꼬리를 공통 정리합니다."""

    answer = _strip_citations(answer)
    log(f"[노트북LM] 원고 {len(answer)}자 수신 완료")

    if cfg.get('strip_promo', True):
        before = len(answer)
        answer = strip_promo_tail(answer, log=log)
        if len(answer) != before:
            log(f"  -> 홍보 제거 후 {len(answer)}자 ({before - len(answer)}자 삭감)")

    return answer


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
    text = re.sub(rf'[ \t]*{group}(?:[ \t]*,?[ \t]*{group})*', '', text)

    # 3) 각주가 빠지면서 떠버린 문장부호 정리
    text = re.sub(r'([.!?])[ \t]*,+', r'\1', text)      # '됩니다.,,' → '됩니다.'
    text = re.sub(r',[ \t]*(?=,)', '', text)            # 연속 쉼표
    text = re.sub(r'[ \t]+([,.!?])', r'\1', text)       # 부호 앞 공백
    text = re.sub(r'(?m)[ \t]*,[ \t]*$', '', text)      # 줄 끝에 남은 쉼표

    return text.strip()


# 영상 제작자가 자기 커뮤니티·강의로 유도하는 문구를 잡아내는 신호.
# (해외 영상은 끝부분에서 거의 항상 이런 유도를 한다)
_PROMO_SIGNALS = re.compile(
    r'(커뮤니티에\s*(?:공개|가입|참여|합류)|커뮤니티에서\s*(?:다운|받)|'
    r'스쿨\s*커뮤니티|디스코드|뉴스레터|멤버십에\s*가입|'
    r'무료로\s*받아|무료로\s*다운|아래\s*링크|링크에서\s*(?:받|다운|확인)|'
    r'구독(?:하고|해\s*주|자|을\s*눌)|채널을?\s*구독|'
    r'그의\s*(?:강의|코스|프로그램)|유료\s*(?:강의|코스|프로그램)에?\s*(?:등록|참여)|'
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
