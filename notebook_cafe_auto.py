# -*- coding: utf-8 -*-
"""
노트북LM 원고 + 유튜브 링크 → 네이버 카페 자동 발행
====================================================

수동으로 하던 작업 중 아래를 자동화한다:

  (수동) 유튜브 영상 찾기 → 노트북LM에 넣고 원고 뽑기
  ────────────────────────────────────────────────
  (자동) 원고를 문단/소제목 단위로 분해
  (자동) 유튜브 영상에서 장면 N장 캡처 → 문단 사이사이 삽입
  (자동) 소제목 → 인용구 블록, 핵심 키워드 → 볼드/음영
  (자동) 글씨체 일괄 적용 (기본: 나눔스퀘어 네오 15)
  (자동) 맨 하단 CTA 문구 + 링크 카드
  (자동) 제일 하단 원본 영상 링크
  (자동) 카페 등록

★ 원문 보존 원칙 ★
  원고를 Gemini에 다시 태우지 않는다. 마커 삽입은 전부 파이썬이 한다.
  Gemini는 (1) 볼드/음영 키워드 고르기 (2) 제목 짓기 두 가지만 담당하고,
  둘 다 실패해도 글은 정상 발행된다.
  조립이 끝나면 `verify_manuscript_intact()`가 마커를 걷어낸 결과와 원문을
  비교해서, 한 글자라도 달라졌으면 마커 없는 안전 버전으로 되돌린다.

사용법:
  python notebook_cafe_auto.py
  (또는 run_notebook_cafe.bat 더블클릭)
"""

import os
import re
import sys
import json
import shutil
import subprocess
import threading

import youtube_cafe_auto as auto
import notebooklm_source as nlm
import publisher_notify
from publisher_contract import (
    REFERENCE_IMAGE_COUNT,
    REFERENCE_TEMPLATE,
    REFERENCE_TEXT_GROUP_COUNT,
    SCENE_MARKER,
    PublisherContractError,
    assert_cafe_target,
    build_reference_5854_body,
    reference_body_without_markers,
    validate_reference_5854_body,
    write_result,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PREVIEW_PATH = os.path.join(SCRIPT_DIR, 'last_body_preview.txt')


# ===================================================================
# 1. 원문 보존 검증
# ===================================================================

_MARKER_RE = re.compile(r'\[(?:/?BOLD|/?HIGHLIGHT|/?BLOCKQUOTE|IMAGE_HERE)\]|\[\[SCENE\]\]')


def _canon(text):
    """마커·마크다운 기호·공백을 걷어낸 '순수 글자'만 남긴다 (비교용)."""
    text = _MARKER_RE.sub('', text)
    text = re.sub(r'^\s{0,3}#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = text.replace('**', '')
    return re.sub(r'\s+', '', text)


def verify_manuscript_intact(original, assembled):
    """마커를 제거한 결과가 원문과 동일한지 확인한다."""
    a, b = _canon(original), _canon(assembled)
    if a == b:
        return True, ''
    # 어디서 갈렸는지 알려준다
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return False, f"{i}번째 글자부터 불일치\n  원문: ...{a[max(0,i-20):i+20]}...\n  결과: ...{b[max(0,i-20):i+20]}..."
    return False, f"길이 불일치 (원문 {len(a)}자 / 결과 {len(b)}자)"


# ===================================================================
# 2. 원고 → 블록 분해 + 소제목 감지
# ===================================================================

_MD_HEADING = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$')
_BOLD_ONLY = re.compile(r'^\s*\*\*(.{2,40}?)\*\*\s*[:：]?\s*$')
_NUM_HEADING = re.compile(r'^\s*(\d{1,2})\s*[.)]\s*(.{2,40})$')


def _as_heading(block):
    """블록이 소제목이면 소제목 텍스트를, 아니면 None을 반환한다."""
    if '\n' in block:
        return None

    m = _MD_HEADING.match(block)
    if m:
        return m.group(1).replace('**', '').strip()

    m = _BOLD_ONLY.match(block)
    if m:
        return m.group(1).strip()

    m = _NUM_HEADING.match(block)
    if m and not re.search(r'[.!?…]$', block):
        return f"{m.group(1)}. {m.group(2).replace('**', '').strip()}"

    return None


def _as_heading_loose(block):
    """패턴형 소제목이 하나도 없을 때만 쓰는 완화 규칙 (짧은 독립 줄)."""
    if '\n' in block:
        return None
    t = block.strip()
    if not (2 <= len(t) <= 30):
        return None
    if re.search(r'[.!?…,]$', t):
        return None
    if t[0] in '-•·▪※"‘“(':
        return None
    return t.replace('**', '').strip()


def _isolate_heading_lines(text):
    """소제목 줄을 독립 블록으로 떼어낸다.

    노트북LM이 '## 소제목' 바로 다음 줄에 빈 줄 없이 본문을 붙여 쓸 때가 있다.
    그러면 소제목+본문이 한 덩어리가 되어 소제목으로 인식되지 않는다.
    빈 줄만 넣는 것이므로 글자는 하나도 안 바뀐다.
    """
    text = re.sub(r'(?m)^([ \t]{0,3}#{1,6}[ \t]+\S.*?)[ \t]*$', r'\n\1\n', text)
    text = re.sub(r'(?m)^[ \t]*(\*\*[^*\n]{2,40}\*\*)[ \t]*[:：]?[ \t]*$', r'\n\1\n', text)
    return text


def split_blocks(manuscript):
    """원고를 빈 줄 기준으로 나누고 각 블록을 heading/body로 분류한다."""
    manuscript = _isolate_heading_lines(manuscript)
    raw = [b.strip() for b in re.split(r'\n\s*\n', manuscript.strip()) if b.strip()]

    blocks = [{'kind': 'heading' if _as_heading(b) else 'body',
               'text': _as_heading(b) or b} for b in raw]

    # 패턴형 소제목이 전혀 없으면 완화 규칙으로 한 번 더 시도
    if not any(b['kind'] == 'heading' for b in blocks):
        blocks = []
        for b in raw:
            h = _as_heading_loose(b)
            blocks.append({'kind': 'heading', 'text': h} if h
                          else {'kind': 'body', 'text': b})

    return blocks


# ===================================================================
# 3. 이미지 자리 배치
# ===================================================================

def section_image_slots(blocks):
    """소제목 섹션마다 1장. 각 섹션의 마지막 본문 문단 뒤에 넣는다.

    (상철 방식: 큰 의미 단위로 문단을 나누고 그 사이사이에 이미지)
    """
    if not blocks:
        return set()

    head_idx = [i for i, b in enumerate(blocks) if b['kind'] == 'heading']
    if not head_idx:
        return set()

    slots = set()
    for n, start in enumerate(head_idx):
        end = head_idx[n + 1] if n + 1 < len(head_idx) else len(blocks)
        # 이 섹션의 마지막 본문 블록
        last_body = None
        for j in range(start + 1, end):
            if blocks[j]['kind'] == 'body':
                last_body = j
        if last_body is not None:
            slots.add(last_body)
    return slots


def count_sections(manuscript):
    """원고의 소제목 섹션 개수 (= 넣을 이미지 장수)."""
    return sum(1 for b in split_blocks(manuscript) if b['kind'] == 'heading')


def choose_image_slots(blocks, image_count):
    """[IMAGE_HERE]를 꽂을 블록 인덱스를 균등하게 고른다.

    - 맨 앞(첫 섹션 이전)에는 넣지 않는다
    - 마지막 블록 뒤에는 넣지 않는다 (CTA가 붙을 자리)
    - 소제목 바로 뒤에는 넣지 않는다 (소제목-이미지-본문은 어색)
    """
    if image_count <= 0:
        return set()

    slots = [i for i, b in enumerate(blocks)
             if b['kind'] == 'body' and i >= 1 and i < len(blocks) - 1]

    if not slots:
        return set()

    if len(slots) <= image_count:
        if len(slots) < image_count:
            print(f"  -> [주의] 문단이 부족해 이미지를 {len(slots)}장만 배치합니다 "
                  f"(요청 {image_count}장). 나머지는 사용하지 않습니다.")
        return set(slots)

    step = len(slots) / float(image_count)
    return {slots[min(len(slots) - 1, int(step * (i + 0.5)))] for i in range(image_count)}


# ===================================================================
# 4. 볼드 / 음영 키워드
# ===================================================================

def _marker_spans(text):
    """이미 마커로 감싸인 구간의 (start, end) 목록."""
    return [(m.start(), m.end()) for m in
            re.finditer(r'\[(?:BOLD|HIGHLIGHT)\].*?\[/(?:BOLD|HIGHLIGHT)\]', text, re.DOTALL)]


def _wrap_first(text, keyword, tag):
    """마커 밖에 있는 첫 등장 1회만 감싼다. 못 찾으면 원문 그대로 반환."""
    spans = _marker_spans(text)
    for m in re.finditer(re.escape(keyword), text):
        if any(s <= m.start() and m.end() <= e for s, e in spans):
            continue
        return (text[:m.start()] + f"[{tag}]{keyword}[/{tag}]" + text[m.end():]), True
    return text, False


def convert_markdown_bold(text, enabled=True):
    """원고의 **강조** 처리. 어느 쪽이든 ** 기호 자체는 반드시 없앤다.

    enabled=True  → [BOLD]로 변환
    enabled=False → 기호만 제거하고 평문으로 (볼드를 꺼도 ** 가 본문에 찍히면 안 된다)
    """
    if enabled:
        return re.sub(r'\*\*(.+?)\*\*', r'[BOLD]\1[/BOLD]', text, flags=re.DOTALL)
    return re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)


def pick_keywords(manuscript, bold_n=4, hl_n=2):
    """Gemini에게 볼드/음영 대상 키워드만 고르게 한다. 실패하면 빈 목록."""
    try:
        from google import genai
        client = genai.Client(api_key=auto.GEMINI_API_KEY)
        prompt = f"""아래는 네이버 카페에 올릴 글의 본문이다.

독자의 시선을 잡을 핵심 키워드를 고르는 것이 네 임무다. 글을 고치거나 요약하지 마라.

규칙:
- bold: {bold_n}개, highlight: {hl_n}개
- highlight는 bold와 겹치지 않게 (서로 다른 단어)
- 반드시 본문에 **그대로 등장하는 연속된 문자열**만 골라라. 한 글자도 바꾸지 마라.
- 각 키워드는 2~15자 사이의 단어/짧은 구절
- 조사('~는', '~을')로 끝나지 않게 명사 단위로 잘라라

출력은 아래 JSON 형식만. 설명 금지.
{{"bold": ["...", "..."], "highlight": ["..."]}}

본문:
{manuscript[:6000]}
"""
        resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        raw = re.sub(r'^```(?:json)?|```$', '', (resp.text or '').strip(), flags=re.MULTILINE).strip()
        data = json.loads(raw)
        bold = [k.strip() for k in data.get('bold', []) if isinstance(k, str) and k.strip()]
        hl = [k.strip() for k in data.get('highlight', []) if isinstance(k, str) and k.strip()]
        # 본문에 실제로 있는 것만, 그리고 bold ∩ highlight = ∅
        bold = [k for k in bold if k in manuscript]
        hl = [k for k in hl if k in manuscript and k not in bold]
        print(f"  -> 볼드 키워드: {bold}")
        print(f"  -> 음영 키워드: {hl}")
        return bold[:bold_n], hl[:hl_n]
    except Exception as e:
        print(f"  -> [주의] 키워드 선별 실패(무시하고 계속): {e}")
        return [], []


# ===================================================================
# 5. 본문 조립
# ===================================================================

def build_body(manuscript, image_count, optional_config, use_ai_keywords=True):
    """원고 → 마커가 박힌 본문 문자열."""
    print("[본문 조립] 원고를 블록으로 분해하는 중...")

    blocks = split_blocks(manuscript)
    n_head = sum(1 for b in blocks if b['kind'] == 'heading')
    print(f"  -> 블록 {len(blocks)}개 (소제목 {n_head}개 / 본문 {len(blocks) - n_head}개)")

    # 소제목 섹션마다 1장이 기본. 섹션이 없으면 문단 균등 배치로 폴백.
    slots = section_image_slots(blocks)
    if slots:
        slots = set(sorted(slots)[:image_count]) if image_count else set()
        print(f"  -> 이미지 위치(섹션 끝마다): {sorted(slots)} (총 {len(slots)}장)")
    else:
        slots = choose_image_slots(blocks, image_count)
        print(f"  -> 이미지 위치(문단 균등): {sorted(slots)} (총 {len(slots)}장)")

    bold_kws, hl_kws = [], []
    if use_ai_keywords and (optional_config.get('bold_enabled') or
                            optional_config.get('highlight_enabled')):
        print("  -> 볼드/음영 키워드 선별 중 (Gemini)...")
        bold_kws, hl_kws = pick_keywords(manuscript)
        if not optional_config.get('bold_enabled'):
            bold_kws = []
        if not optional_config.get('highlight_enabled'):
            hl_kws = []

    used = set()
    parts = []
    for i, blk in enumerate(blocks):
        if blk['kind'] == 'heading':
            parts.append(f"[BLOCKQUOTE]{blk['text']}[/BLOCKQUOTE]")
        else:
            t = convert_markdown_bold(
                blk['text'], enabled=bool(optional_config.get('bold_enabled')))
            for kw in bold_kws:
                if kw not in used:
                    t, hit = _wrap_first(t, kw, 'BOLD')
                    if hit:
                        used.add(kw)
            for kw in hl_kws:
                if kw not in used:
                    t, hit = _wrap_first(t, kw, 'HIGHLIGHT')
                    if hit:
                        used.add(kw)
            parts.append(t)

        if i in slots:
            parts.append('[IMAGE_HERE]')

    body = '\n\n'.join(parts)

    ok, detail = verify_manuscript_intact(manuscript, body)
    if ok:
        print("  -> [검증] 원문 무손실 확인 OK")
        return body

    print(f"  -> [경고] 원문이 변형되었습니다. 마커 없는 안전 버전으로 되돌립니다.\n{detail}")
    return build_body_safe(manuscript, image_count)


def build_body_safe(manuscript, image_count):
    """폴백: 소제목/키워드 없이 원고 + 이미지 자리만."""
    blocks = [{'kind': 'body', 'text': b.strip()}
              for b in re.split(r'\n\s*\n', manuscript.strip()) if b.strip()]
    slots = choose_image_slots(blocks, image_count)
    parts = []
    for i, blk in enumerate(blocks):
        parts.append(blk['text'])
        if i in slots:
            parts.append('[IMAGE_HERE]')
    return '\n\n'.join(parts)


def build_template_body(manuscript, image_count, optional_config,
                        template='', use_ai_keywords=True):
    if template != REFERENCE_TEMPLATE:
        return build_body(
            manuscript, image_count, optional_config,
            use_ai_keywords=use_ai_keywords)

    print(f"[본문 조립] {REFERENCE_TEMPLATE} 기준글 구조 적용 중...")
    body = build_reference_5854_body(manuscript, image_count)
    shape = validate_reference_5854_body(body, image_count)
    original = re.sub(r'\s+', '', manuscript.replace(SCENE_MARKER, ''))
    if reference_body_without_markers(body) != original:
        raise PublisherContractError("기준글 템플릿 조립 중 원고가 변형되었습니다.")
    print(f"  -> 텍스트 {shape['textGroupCount']}구간 / 이미지 {shape['imageMarkerCount']}장")
    print("  -> [검증] 소제목·인용구 없이 원문 무손실 확인 OK")
    return body


def apply_template_options(optional_config, template):
    result = dict(optional_config)
    if template == REFERENCE_TEMPLATE:
        # 기준글 5854: 본문/이미지 교차 + 맨 끝 원본 영상 OG 카드만 사용한다.
        result.update({
            'bold_enabled': False,
            'highlight_enabled': False,
            'cta_enabled': False,
            'board_name': '',
            'source_label': '',
            'source_link_card': True,
        })
    return result


# ===================================================================
# 6. 제목
# ===================================================================

TITLE_PROMPT = """아래 글에 붙일 네이버 카페 게시글 제목을 하나만 지어라.

규칙:
- 40자 이내, 한국어
- 이모지·따옴표·해시태그 금지
- 낚시성 과장 금지. 글에 실제로 있는 내용만
- 숫자나 구체적 대상이 들어가면 좋다
- 조사가 어색하게 끊기지 않는 자연스러운 한 문장
- 제목 텍스트만 출력. 설명·인사·따옴표 금지.

글:
{body}
"""


def _clean_title(raw):
    """모델 출력에서 제목 한 줄만 추려낸다."""
    if not raw:
        return ''
    lines = [ln.strip() for ln in raw.strip().split('\n') if ln.strip()]
    if not lines:
        return ''
    t = lines[-1] if len(lines) > 1 else lines[0]
    t = t.strip().strip('"“”\'')
    t = re.sub(r'^(제목|title)\s*[:：]\s*', '', t, flags=re.IGNORECASE).strip()
    return t if 4 <= len(t) <= 60 else ''


def _title_via_claude(manuscript, timeout=180):
    """Claude Code CLI로 제목을 뽑는다.

    이미 로그인된 구독(OAuth) 세션을 그대로 쓰므로 API 키가 필요 없다.
    anthropic SDK를 직접 쓰려면 `ant auth login`으로 별도 OAuth 프로필을
    만들어야 하는데, 그건 이 PC에 없다.
    """
    exe = shutil.which('claude')
    if not exe:
        print('  -> [주의] claude CLI를 찾을 수 없습니다. Gemini로 넘어갑니다.')
        return ''

    try:
        r = subprocess.run(
            [exe, '-p'],
            input=TITLE_PROMPT.format(body=manuscript[:6000]),
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f'  -> [주의] claude CLI 응답 없음({timeout}초). Gemini로 넘어갑니다.')
        return ''
    except Exception as e:
        print(f'  -> [주의] claude CLI 호출 실패: {e}')
        return ''

    if r.returncode != 0:
        print(f'  -> [주의] claude CLI 오류(rc={r.returncode}): {(r.stderr or "").strip()[:200]}')
        return ''

    return _clean_title(r.stdout)


def _title_via_gemini(manuscript):
    """폴백: Gemini로 제목을 뽑는다."""
    try:
        from google import genai
        client = genai.Client(api_key=auto.GEMINI_API_KEY)
        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=TITLE_PROMPT.format(body=manuscript[:4000]))
        return _clean_title(resp.text)
    except Exception as e:
        print(f'  -> [주의] Gemini 제목 생성 실패: {e}')
        return ''


def make_title(manuscript):
    """제목 생성: Claude(구독 OAuth) → Gemini → 원고 첫 줄 순으로 시도."""
    first_line = manuscript.strip().split('\n')[0].strip()
    first_line = re.sub(r'^\s{0,3}#{1,6}\s+', '', first_line).replace('**', '').strip()

    print('  -> 제목 생성 중 (Claude)...')
    t = _title_via_claude(manuscript)
    if t:
        return t

    print('  -> Gemini로 재시도...')
    t = _title_via_gemini(manuscript)
    if t:
        return t

    print('  -> 제목 생성 실패. 원고 첫 줄을 사용합니다.')
    return first_line[:60] or '제목 없음'


# ===================================================================
# 7. 입력 창
# ===================================================================

def ask_inputs(default_image_count=5, nlm_cfg=None):
    """유튜브 링크 / 제목 / 이미지 수 / 원고를 한 창에서 받는다."""
    import tkinter as tk
    from tkinter import messagebox

    nlm_cfg = nlm_cfg or {}
    nlm_on = bool(nlm_cfg.get('enabled'))
    result = {}

    root = tk.Tk()
    root.title('카페 자동 발행 — 노트북LM 원고 + 유튜브 장면')
    root.attributes('-topmost', True)
    root.geometry('820x720')

    pad = {'padx': 12, 'pady': 4}

    tk.Label(root, text='유튜브 영상 링크 (원고 생성 + 장면 캡처 + 맨 하단 원본 링크)',
             anchor='w', font=('맑은 고딕', 9, 'bold')).pack(fill='x', **pad)

    urow = tk.Frame(root)
    urow.pack(fill='x', padx=12)
    e_url = tk.Entry(urow, font=('맑은 고딕', 10))
    e_url.grid(row=0, column=0, sticky='we')
    urow.columnconfigure(0, weight=1)

    status_var = tk.StringVar(
        value='링크 넣고 [노트북LM에서 가져오기]. 비워두고 발행하면 자동으로 가져옵니다.'
        if nlm_on else '노트북LM 연동이 꺼져 있습니다 (config.ini의 [NOTEBOOKLM] enabled).')
    btn_fetch = tk.Button(urow, text='노트북LM에서 가져오기', width=20,
                          font=('맑은 고딕', 9),
                          state=('normal' if nlm_on else 'disabled'))
    btn_fetch.grid(row=0, column=1, padx=(8, 0))

    tk.Label(root, textvariable=status_var, anchor='w', fg='#0a6',
             font=('맑은 고딕', 8)).pack(fill='x', padx=12)

    row = tk.Frame(root)
    row.pack(fill='x', padx=12, pady=(10, 0))

    tk.Label(row, text='제목 (비우면 AI가 자동 생성)',
             anchor='w', font=('맑은 고딕', 9, 'bold')).grid(row=0, column=0, sticky='w')
    tk.Label(row, text='이미지 수', anchor='w',
             font=('맑은 고딕', 9, 'bold')).grid(row=0, column=1, sticky='w', padx=(12, 0))

    e_title = tk.Entry(row, font=('맑은 고딕', 10), width=70)
    e_title.grid(row=1, column=0, sticky='we')
    s_count = tk.Spinbox(row, from_=0, to=10, width=5, font=('맑은 고딕', 10))
    s_count.delete(0, 'end')
    s_count.insert(0, str(default_image_count))
    s_count.grid(row=1, column=1, sticky='w', padx=(12, 0))
    row.columnconfigure(0, weight=1)

    tk.Label(root, text='노트북LM 원고 (붙여넣기 — 문장은 하나도 안 고칩니다)',
             anchor='w', font=('맑은 고딕', 9, 'bold')).pack(fill='x', **pad)

    frame = tk.Frame(root)
    frame.pack(fill='both', expand=True, padx=12)
    scroll = tk.Scrollbar(frame)
    scroll.pack(side='right', fill='y')
    t_body = tk.Text(frame, wrap='word', font=('맑은 고딕', 10), yscrollcommand=scroll.set)
    t_body.pack(side='left', fill='both', expand=True)
    scroll.config(command=t_body.yview)

    var_dry = tk.BooleanVar(value=False)
    var_kw = tk.BooleanVar(value=True)
    var_pub = tk.BooleanVar(value=False)
    opts = tk.Frame(root)
    opts.pack(fill='x', padx=12, pady=(8, 0))
    tk.Checkbutton(opts, text='드라이런 (카페 안 열고 본문만 확인)',
                   variable=var_dry, font=('맑은 고딕', 9)).pack(side='left')
    tk.Checkbutton(opts, text='AI 키워드 볼드/음영',
                   variable=var_kw, font=('맑은 고딕', 9)).pack(side='left', padx=(16, 0))
    tk.Checkbutton(opts, text='바로 발행 (체크 안 하면 임시저장)',
                   variable=var_pub, font=('맑은 고딕', 9, 'bold'),
                   fg='#c33').pack(side='left', padx=(16, 0))

    # ── 노트북LM에서 원고 가져오기 (백그라운드 스레드) ──
    def _fetch_done(text):
        t_body.delete('1.0', 'end')
        t_body.insert('1.0', text)
        status_var.set(f'노트북LM 원고 {len(text)}자 수신 완료. 확인하고 발행하세요.')
        btn_fetch.config(state='normal', text='노트북LM에서 가져오기')

    def _fetch_fail(msg):
        status_var.set('노트북LM 가져오기 실패')
        btn_fetch.config(state='normal', text='노트북LM에서 가져오기')
        messagebox.showerror('노트북LM 실패', msg)

    def on_fetch():
        url = e_url.get().strip()
        if not url:
            messagebox.showwarning('입력 필요', '유튜브 링크를 먼저 넣어주세요.')
            return
        btn_fetch.config(state='disabled', text='가져오는 중...')
        status_var.set('노트북LM 호출 중... 영상 길이에 따라 1~5분 걸립니다.')

        def work():
            try:
                text = nlm.fetch_manuscript(
                    url, nlm_cfg,
                    log=lambda m: root.after(0, status_var.set, m.strip()))
                root.after(0, _fetch_done, text)
            except Exception as e:
                root.after(0, _fetch_fail, str(e))

        threading.Thread(target=work, daemon=True).start()

    btn_fetch.config(command=on_fetch)

    def on_submit():
        url = e_url.get().strip()
        body = t_body.get('1.0', 'end').strip()
        if not body and not (nlm_on and url):
            messagebox.showwarning(
                '입력 필요',
                '원고를 붙여넣거나, 유튜브 링크를 넣고 노트북LM에서 가져오세요.')
            return
        try:
            cnt = int(s_count.get())
        except ValueError:
            cnt = default_image_count
        if cnt > 0 and not url:
            messagebox.showwarning(
                '입력 필요',
                '이미지를 캡처하려면 유튜브 링크가 필요합니다.\n'
                '링크 없이 글만 올리려면 이미지 수를 0으로 바꿔주세요.')
            return
        result.update({'url': url, 'title': e_title.get().strip(), 'image_count': cnt,
                       'manuscript': body, 'dry_run': var_dry.get(),
                       'use_ai_keywords': var_kw.get(),
                       'draft': not var_pub.get()})
        root.destroy()

    btns = tk.Frame(root)
    btns.pack(fill='x', padx=12, pady=10)
    tk.Button(btns, text='발행 시작', command=on_submit, width=14,
              font=('맑은 고딕', 10, 'bold')).pack(side='right')
    tk.Button(btns, text='취소', command=root.destroy, width=10,
              font=('맑은 고딕', 10)).pack(side='right', padx=(0, 8))

    e_url.focus_set()
    root.mainloop()
    return result or None


# ===================================================================
# 8. 커맨드라인
# ===================================================================

USAGE = """사용법:
  python notebook_cafe_auto.py                          창 띄우기 (기존 방식)
  python notebook_cafe_auto.py "<유튜브링크>"            임시저장까지 (기본값)
  python notebook_cafe_auto.py "<링크>" --publish       바로 발행
  python notebook_cafe_auto.py "<링크>" --dry           카페 안 열고 본문만 확인
  python notebook_cafe_auto.py "<링크>" --title "제목"   제목 직접 지정
  python notebook_cafe_auto.py "<링크>" --images 3      이미지 장수 지정
  python notebook_cafe_auto.py "<링크>" --file 원고.txt  원고를 파일로 주기(노트북LM 건너뜀)
  python notebook_cafe_auto.py "<링크>" --no-keywords   AI 볼드/음영 끄기
  python notebook_cafe_auto.py "<링크>" --template reference-5854 --images 5
  python notebook_cafe_auto.py "<링크>" --video-file C:\\path\\live.mp4 --result result.json
  python notebook_cafe_auto.py "<링크>" --notebook-url "<원본라이브링크>"
  python notebook_cafe_auto.py "<링크>" --publish --notify   검증 성공 뒤 텔레그램 알림
"""


def parse_args(argv, default_image_count):
    """인자를 파싱한다. URL이 없으면 None (→ 창 띄우기)."""
    # draft=True 가 기본. 임시저장해두고 눈으로 확인한 뒤 카페에서 발행한다.
    args = {'url': '', 'title': '', 'image_count': default_image_count,
            'manuscript': '', 'dry_run': False, 'use_ai_keywords': True,
            'draft': True, 'headless': False, 'template': '',
            'video_file': '', 'result_path': '', 'notify': False,
            'notebook_url': '', 'source_date': '', 'kind': '',
            'unattended': False, 'expected_club_id': '', 'expected_menu_id': ''}

    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('-h', '--help'):
            print(USAGE)
            sys.exit(0)
        elif a == '--dry':
            args['dry_run'] = True
        elif a == '--headless':
            args['headless'] = True
        elif a == '--publish':
            args['draft'] = False
        elif a == '--draft':
            args['draft'] = True
        elif a == '--no-keywords':
            args['use_ai_keywords'] = False
        elif a == '--notify':
            args['notify'] = True
        elif a == '--unattended':
            args['unattended'] = True
        elif a == '--title' and i + 1 < len(argv):
            i += 1
            args['title'] = argv[i]
        elif a == '--images' and i + 1 < len(argv):
            i += 1
            args['image_count'] = int(argv[i])
            args['image_count_explicit'] = True   # 자동 산출을 끄고 이 값을 쓴다
        elif a == '--file' and i + 1 < len(argv):
            i += 1
            with open(argv[i], 'r', encoding='utf-8') as f:
                args['manuscript'] = f.read().strip()
        elif a == '--template' and i + 1 < len(argv):
            i += 1
            args['template'] = argv[i]
        elif a == '--video-file' and i + 1 < len(argv):
            i += 1
            args['video_file'] = argv[i]
        elif a == '--result' and i + 1 < len(argv):
            i += 1
            args['result_path'] = argv[i]
        elif a == '--notebook-url' and i + 1 < len(argv):
            i += 1
            args['notebook_url'] = argv[i]
        elif a == '--source-date' and i + 1 < len(argv):
            i += 1
            args['source_date'] = argv[i]
        elif a == '--kind' and i + 1 < len(argv):
            i += 1
            args['kind'] = argv[i]
        elif a == '--expected-club-id' and i + 1 < len(argv):
            i += 1
            args['expected_club_id'] = argv[i]
        elif a == '--expected-menu-id' and i + 1 < len(argv):
            i += 1
            args['expected_menu_id'] = argv[i]
        elif a.startswith('-'):
            print(f"알 수 없는 옵션: {a}\n\n{USAGE}")
            sys.exit(1)
        else:
            rest.append(a)
        i += 1

    if rest:
        args['url'] = rest[0]

    if args['template'] and args['template'] != REFERENCE_TEMPLATE:
        print(f"지원하지 않는 템플릿: {args['template']}")
        sys.exit(1)
    if args['kind'] and args['kind'] not in ('ai', 'business'):
        print("--kind 는 ai 또는 business 여야 합니다.")
        sys.exit(1)

    if not args['url'] and not args['manuscript']:
        return None
    return args


# ===================================================================
# 9. 메인
# ===================================================================

def _finish(inputs, payload):
    result = write_result((inputs or {}).get('result_path'), payload)
    print("PUBLISHER_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def _notify_verified_result(config, source_url, title, post_result, result):
    record = auto.get_published_record(source_url) or {}
    prior_notification = record.get('notification') or {}
    if prior_notification.get('ok'):
        result['notification'] = {
            "ok": True,
            "status": "already-sent",
            "messageId": prior_notification.get('messageId'),
        }
        print('[텔레그램] 이미 전송된 글이라 중복 알림을 건너뜁니다.')
        return result
    if prior_notification.get('status') == 'sending':
        result.update({
            "ok": False,
            "status": "published-verified-notification-unknown",
            "stage": "telegram-notify",
            "error": "이전 텔레그램 전송의 성공 여부를 확인할 수 없어 중복 방지를 위해 자동 재전송하지 않습니다.",
            "notification": {"ok": False, "status": "unknown"},
        })
        print('[텔레그램] 이전 전송 결과가 불명확해 자동 재전송을 중단합니다.')
        return result

    try:
        publisher_notify.validate_notification_config(config)
        if not auto.mark_publish_notification(
                source_url, {"ok": False, "status": "sending"}):
            raise RuntimeError("텔레그램 전송 전 중복 방지 상태를 저장하지 못했습니다.")
        notification = publisher_notify.send_verified_article(
            config, title, post_result['articleUrl'])
        auto.mark_publish_notification(source_url, notification)
        result['notification'] = notification
        result['naverOrTelegramWrites'] = True
        print('[텔레그램] 카페 글 링크 알림 전송 완료')
    except Exception as e:
        result.update({
            "ok": False,
            "status": "published-verified-notification-error",
            "stage": "telegram-notify",
            "error": str(e),
            "notification": {"ok": False, "status": "error"},
        })
    return result


def main(argv=None):
    raw_argv = argv if argv is not None else sys.argv[1:]
    # 결과 파일 경로를 설정 로드보다 먼저 알아야 설정/인증 오류도 구조화할 수 있다.
    inputs = parse_args(raw_argv, REFERENCE_IMAGE_COUNT)
    config = auto.load_or_create_config()
    auto.NAVER_ID = config['NAVER']['id']
    auto.NAVER_PW = config['NAVER']['pw']
    auto.CAFE_URL = config['NAVER']['cafe_url']
    auto.GEMINI_API_KEY = config.get('GEMINI', 'api_key', fallback='')
    optional_config = auto.load_optional_config(config)
    nlm_cfg = nlm.load_config(config)

    if inputs is None:
        inputs = ask_inputs(optional_config.get('image_count', 5), nlm_cfg)
    if not inputs:
        print('입력이 취소되었습니다.')
        return {"ok": False, "status": "cancelled", "stage": "input"}

    # GUI 입력과 구버전 호출에도 새 필드의 안전한 기본값을 보장한다.
    defaults = {
        'template': '', 'video_file': '', 'result_path': '', 'notify': False,
        'notebook_url': '', 'source_date': '', 'kind': '', 'unattended': False,
        'headless': False, 'expected_club_id': '', 'expected_menu_id': '',
    }
    for key, value in defaults.items():
        inputs.setdefault(key, value)

    url = inputs['url']
    template = inputs['template']
    base = {
        "sourceDate": inputs.get('source_date') or None,
        "kind": inputs.get('kind') or None,
        "template": template or "default",
        "sourceUrl": url or None,
        "naverOrTelegramWrites": False,
    }

    try:
        target_board = assert_cafe_target(
            auto.CAFE_URL,
            inputs.get('expected_club_id'),
            inputs.get('expected_menu_id'))
        if inputs.get('expected_club_id') or inputs.get('expected_menu_id'):
            print(f"[게시판 확인] cafe={target_board['clubId']} menu={target_board['menuId']}")
        if inputs.get('notify') and (inputs['dry_run'] or inputs['draft']):
            raise PublisherContractError("--notify는 --publish 모드에서만 사용할 수 있습니다.")
        existing_record = (auto.get_published_record(url)
                           if url and not inputs['dry_run'] and not inputs['draft'] else None)
        if inputs.get('unattended') and not (inputs.get('title') or existing_record):
            raise PublisherContractError("무인 실행에는 유료/대화형 제목 생성을 막기 위해 --title이 필요합니다.")

        # 발행 뒤 검증/알림 단계에서 실패한 재시도는 원고 생성과 이미지 추출도
        # 반복하지 않는다. 저장된 URL의 기존 글만 읽고 필요한 다음 단계만 수행한다.
        if existing_record:
            title = inputs.get('title') or existing_record.get('title') or '제목 없음'
            post_result = auto.post_to_naver_cafe(
                title, '', [], apply_template_options(optional_config, template),
                source_url=url,
                draft=False,
                unattended=inputs.get('unattended', False),
                keep_browser_open=False,
                verify_images=(REFERENCE_IMAGE_COUNT if template == REFERENCE_TEMPLATE else 1),
                verify_text_groups=(REFERENCE_TEXT_GROUP_COUNT
                                    if template == REFERENCE_TEMPLATE else None),
                verify_og_links=(1 if template == REFERENCE_TEMPLATE else None),
                require_all_images=True,
                verify_exact_images=(template == REFERENCE_TEMPLATE),
            )
            result = {
                **base,
                **post_result,
                "title": title,
                "naverOrTelegramWrites": False,
                "resumedFromPublishedRecord": True,
            }
            if inputs.get('notify') and post_result.get('status') == 'published-verified':
                result = _notify_verified_result(config, url, title, post_result, result)
            return _finish(inputs, result)

        manuscript = inputs['manuscript']
        notebook_url = inputs.get('notebook_url') or url

        # ── 0. 원고가 비었으면 NotebookLM에서 가져온다 ──
        if not manuscript:
            manuscript = nlm.fetch_manuscript(
                notebook_url, nlm_cfg, template=template)
            with open(os.path.join(SCRIPT_DIR, 'last_manuscript.txt'), 'w',
                      encoding='utf-8') as f:
                f.write(manuscript)

        # ── 1. 이미지 수와 장면 캡처 ──
        if template == REFERENCE_TEMPLATE:
            inputs['image_count'] = REFERENCE_IMAGE_COUNT
            inputs['image_count_explicit'] = True
            print(f"[이미지] 기준글 포맷 고정값 {REFERENCE_IMAGE_COUNT}장")
        elif not inputs.get('image_count_explicit'):
            n_sec = count_sections(manuscript)
            inputs['image_count'] = n_sec or optional_config.get('image_count', 5)
            if n_sec:
                print(f"[이미지] 소제목 섹션 {n_sec}개 → {n_sec}장 캡처")
            else:
                print(f"[이미지] 소제목이 없어 기본값 {inputs['image_count']}장 사용")

        image_paths = []
        if inputs['image_count'] > 0 and not (url or inputs.get('video_file')):
            print('[주의] 영상 링크/파일이 없어 장면 캡처를 건너뜁니다.')
        elif inputs['image_count'] > 0:
            image_paths = auto.extract_frames(
                notebook_url, inputs['image_count'], video_file=inputs.get('video_file'),
                allow_thumbnails=(template != REFERENCE_TEMPLATE))
        print(f"  -> 캡처된 이미지: {len(image_paths)}장")

        if template == REFERENCE_TEMPLATE and len(image_paths) != REFERENCE_IMAGE_COUNT:
            raise PublisherContractError(
                f"기준글 포맷은 이미지 {REFERENCE_IMAGE_COUNT}장이 모두 준비돼야 합니다 "
                f"(현재 {len(image_paths)}장).")

        # ── 2. 본문 조립 ──
        optional_config = apply_template_options(optional_config, template)
        body = build_template_body(
            manuscript, len(image_paths), optional_config, template,
            use_ai_keywords=inputs['use_ai_keywords'])

        # ── 3. 제목 ──
        title = inputs['title'] or make_title(manuscript)
        print(f"[제목] {title}")

        # ── 4. 미리보기 저장 ──
        with open(PREVIEW_PATH, 'w', encoding='utf-8') as f:
            f.write(f"제목: {title}\n"
                    f"템플릿: {template or 'default'}\n"
                    f"이미지: {len(image_paths)}장\n"
                    f"글씨체: {optional_config.get('font_family')} "
                    f"{optional_config.get('font_size')}\n"
                    f"원본 링크: {url}\n"
                    f"{'=' * 60}\n{body}\n")
        print(f"[미리보기] {PREVIEW_PATH}")

        if inputs['dry_run']:
            print('\n' + '=' * 60)
            print(body)
            print('=' * 60)
            print('\n드라이런 모드입니다. 카페와 텔레그램에는 쓰지 않았습니다.')
            return _finish(inputs, {
                **base,
                "ok": True,
                "status": "dry-run-complete",
                "stage": "preview",
                "title": title,
                "imageCount": len(image_paths),
                "previewPath": os.path.abspath(PREVIEW_PATH),
                "framePaths": [os.path.abspath(path) for path in image_paths],
            })

        # ── 5. 카페 임시저장/발행 ──
        if inputs.get('headless'):
            raise PublisherContractError(
                "헤드리스 발행은 본문 누락이 재현되어 안전상 금지되어 있습니다.")
        if not image_paths:
            raise PublisherContractError("카페 이미지가 0장이라 저장/발행을 중단합니다.")

        print('[모드] ' + ('임시저장' if inputs['draft'] else '바로 발행'))
        post_result = auto.post_to_naver_cafe(
            title, body, image_paths, optional_config,
            source_url=url,
            draft=inputs['draft'],
            unattended=inputs.get('unattended', False),
            keep_browser_open=not inputs.get('unattended', False) and inputs['draft'],
            verify_images=(REFERENCE_IMAGE_COUNT if template == REFERENCE_TEMPLATE
                           else len(image_paths)),
            verify_text_groups=(REFERENCE_TEXT_GROUP_COUNT
                                if template == REFERENCE_TEMPLATE else None),
            verify_og_links=(1 if template == REFERENCE_TEMPLATE else None),
            require_all_images=True,
            verify_exact_images=(template == REFERENCE_TEMPLATE),
        )
        result = {
            **base,
            **post_result,
            "title": title,
            "imageCount": len(image_paths),
            "previewPath": os.path.abspath(PREVIEW_PATH),
            "naverOrTelegramWrites": bool(post_result.get('status') in (
                'draft-saved', 'published-unverified', 'published-verified')),
        }

        # ── 6. 검증된 발행 글만 Telegram으로 한 번 알림 ──
        if (not inputs['draft'] and inputs.get('notify') and
                post_result.get('status') == 'published-verified'):
            result = _notify_verified_result(config, url, title, post_result, result)

        if result.get('ok') or result.get('articleUrl'):
            auto.cleanup_temp_files()
            print('[정리] 카페 발행용 임시 이미지가 삭제되었습니다.')
        return _finish(inputs, result)

    except Exception as e:
        print(f"\n[중단] {e}")
        return _finish(inputs, {
            **base,
            "ok": False,
            "status": "error",
            "stage": "prepare",
            "error": str(e),
        })


if __name__ == '__main__':
    try:
        outcome = main()
        if outcome and not outcome.get('ok', False):
            sys.exit(1)
    except KeyboardInterrupt:
        print('\n사용자가 중단했습니다.')
        sys.exit(1)
