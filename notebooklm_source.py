# -*- coding: utf-8 -*-
"""Generate NotebookLM manuscripts through the pinned Aside ``u0`` session."""

import re

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


class NotebookLMError(RuntimeError):
    pass


def _video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})', url or '')
    return m.group(1) if m else ''


def _heading_count(text):
    """'## 소제목' 줄 개수."""
    return sum(1 for ln in (text or '').split('\n') if ln.strip().startswith('##'))


def fetch_manuscript(youtube_url, cfg, log=print):
    """유튜브 URL → 노트북LM 원고 텍스트. 실패하면 NotebookLMError."""
    if not youtube_url:
        raise NotebookLMError("유튜브 링크가 없습니다.")

    from content_production_policy import CAFE_NOTEBOOK, SHORTS_NOTEBOOK
    from notebooklm_aside import NotebookLMAsideError, ask_existing_notebook

    notebook_id = str(cfg.get('notebook_id') or '').strip()
    notebook_title = str(cfg.get('notebook_title') or '').strip()
    kind = 'shorts' if notebook_id == SHORTS_NOTEBOOK['id'] else 'cafe'
    expected = SHORTS_NOTEBOOK if kind == 'shorts' else CAFE_NOTEBOOK
    notebook_id = notebook_id or expected['id']
    notebook_title = notebook_title or expected['title']
    log(f"[노트북LM] Aside u0 / {notebook_title} 원고 생성 시작...")
    try:
        result = ask_existing_notebook(
            youtube_url,
            cfg.get('prompt') or DEFAULT_PROMPT,
            kind=kind,
            notebook_id=notebook_id,
            notebook_title=notebook_title,
            timeout=int(cfg.get('source_wait_timeout', 300)),
            account=str(cfg.get('aside_account') or 'u0'),
            evidence_dir=cfg.get('evidence_dir'),
        )
    except (NotebookLMAsideError, RuntimeError) as exc:
        raise NotebookLMError(str(exc)) from exc
    answer = str(result.get('answer') or '').strip()

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
    from content_production_policy import ASIDE_ACCOUNT, CAFE_NOTEBOOK

    return {
        'enabled': config.getboolean('NOTEBOOKLM', 'enabled', fallback=False),
        'notebook_id': config.get(
            'NOTEBOOKLM', 'notebook_id', fallback=CAFE_NOTEBOOK['id']
        ).strip(),
        'notebook_title': config.get('NOTEBOOKLM', 'notebook_title',
                                     fallback=CAFE_NOTEBOOK['title']).strip(),
        'notebook_title_prefix': config.get('NOTEBOOKLM', 'notebook_title_prefix',
                                            fallback='[자동]').strip(),
        'scope_to_new_source': config.getboolean('NOTEBOOKLM', 'scope_to_new_source',
                                                 fallback=True),
        'source_wait_timeout': config.getint('NOTEBOOKLM', 'source_wait_timeout',
                                             fallback=300),
        'delete_after': config.getboolean('NOTEBOOKLM', 'delete_after', fallback=False),
        'delete_source_after': True,
        'retry_if_no_heading': config.getboolean('NOTEBOOKLM', 'retry_if_no_heading',
                                                 fallback=True),
        'profile': config.get('NOTEBOOKLM', 'profile', fallback='').strip(),
        'aside_account': ASIDE_ACCOUNT,
        'prompt': config.get('NOTEBOOKLM', 'prompt', fallback='').strip(),
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
