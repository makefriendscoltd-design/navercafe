"""
네이버 카페 자동 포스팅 — GUI 프론트엔드
========================================
youtube_cafe_auto.py의 모든 기능을 한 화면에서 조작합니다.

실행: python cafe_gui.py
"""

import os
import sys
import re
import threading
import configparser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.ini")

# youtube_cafe_auto에서 기본 메타프롬프트 가져오기
try:
    from youtube_cafe_auto import DEFAULT_WRITING_STYLE
except ImportError:
    DEFAULT_WRITING_STYLE = ""

# ===================================================================
# GUI 앱
# ===================================================================

class CafeAutoApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("네이버 카페 자동 포스팅")
        self.root.geometry("1000x750")
        self.root.minsize(900, 650)
        self.root.attributes('-topmost', False)

        self.image_paths = []
        self.running = False

        self._build_ui()
        self._load_config()

    # ──────────────────────────────────────────────
    # UI 구성
    # ──────────────────────────────────────────────
    def _build_ui(self):
        # 상단: 탭 노트북 (설정 / 실행)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=(8, 0))

        # ── 탭 1: 실행 ──
        self.tab_run = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_run, text="  실행  ")
        self._build_run_tab()

        # ── 탭 2: 설정 ──
        self.tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_settings, text="  설정  ")
        self._build_settings_tab()

        # 하단: 상태 표시줄
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill='x', padx=8, pady=4)
        self.status_label = ttk.Label(status_frame, text="대기 중", foreground="gray")
        self.status_label.pack(side='left')

    def _build_run_tab(self):
        """실행 탭: 소스 입력 + 이미지 + 실행 버튼 + 로그"""
        # 상단: 소스 입력
        input_frame = ttk.LabelFrame(self.tab_run, text="소스 입력 (유튜브 링크 / 뉴스 URL / 직접 텍스트)")
        input_frame.pack(fill='x', padx=8, pady=(8, 4))

        self.source_text = scrolledtext.ScrolledText(input_frame, height=5, wrap='word',
                                                      font=('맑은 고딕', 10))
        self.source_text.pack(fill='x', padx=8, pady=8)

        # 중단: 이미지 + 버튼 영역
        action_frame = ttk.Frame(self.tab_run)
        action_frame.pack(fill='x', padx=8, pady=4)

        # 이미지 선택
        img_frame = ttk.LabelFrame(action_frame, text="이미지")
        img_frame.pack(side='left', fill='x', expand=True)

        self.img_label = ttk.Label(img_frame, text="유튜브: 자동 캡처 / 기타: 아래 버튼으로 선택",
                                    foreground="gray")
        self.img_label.pack(side='left', padx=8, pady=6)

        ttk.Button(img_frame, text="이미지 선택", command=self._select_images).pack(side='right', padx=8, pady=6)

        # 실행 버튼
        self.run_btn = ttk.Button(action_frame, text="▶ 실행", command=self._on_run)
        self.run_btn.pack(side='right', padx=(12, 0), pady=6, ipadx=20, ipady=4)

        # 하단: 로그
        log_frame = ttk.LabelFrame(self.tab_run, text="실행 로그")
        log_frame.pack(fill='both', expand=True, padx=8, pady=(4, 8))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, wrap='word',
                                                   font=('Consolas', 9), state='disabled',
                                                   background='#1e1e1e', foreground='#d4d4d4')
        self.log_text.pack(fill='both', expand=True, padx=4, pady=4)

    def _build_settings_tab(self):
        """설정 탭: 계정, CTA, 메타프롬프트, 콘텐츠 설정"""
        # 스크롤 가능한 프레임
        canvas = tk.Canvas(self.tab_settings)
        scrollbar = ttk.Scrollbar(self.tab_settings, orient='vertical', command=canvas.yview)
        self.settings_inner = ttk.Frame(canvas)

        self.settings_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.settings_inner, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side='left', fill='both', expand=True, padx=(8, 0), pady=8)
        scrollbar.pack(side='right', fill='y', pady=8, padx=(0, 8))

        # 마우스 휠 바인딩
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        parent = self.settings_inner

        # ── 계정 정보 ──
        acc_frame = ttk.LabelFrame(parent, text="네이버 계정 / API 키")
        acc_frame.pack(fill='x', padx=4, pady=(0, 8))

        self.var_naver_id = tk.StringVar()
        self.var_naver_pw = tk.StringVar()
        self.var_cafe_url = tk.StringVar()
        self.var_gemini_key = tk.StringVar()

        for i, (label, var, show) in enumerate([
            ("네이버 아이디", self.var_naver_id, None),
            ("네이버 비밀번호", self.var_naver_pw, '●'),
            ("카페 게시판 URL", self.var_cafe_url, None),
            ("Gemini API 키", self.var_gemini_key, '●'),
        ]):
            ttk.Label(acc_frame, text=label).grid(row=i, column=0, sticky='w', padx=8, pady=3)
            entry = ttk.Entry(acc_frame, textvariable=var, width=60)
            if show:
                entry.config(show=show)
            entry.grid(row=i, column=1, sticky='ew', padx=8, pady=3)
        acc_frame.columnconfigure(1, weight=1)

        # ── CTA 설정 ──
        cta_frame = ttk.LabelFrame(parent, text="CTA (Call to Action) — 글 마지막에 유입 링크 삽입")
        cta_frame.pack(fill='x', padx=4, pady=(0, 8))

        self.var_cta_enabled = tk.BooleanVar()
        ttk.Checkbutton(cta_frame, text="CTA 활성화", variable=self.var_cta_enabled).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=8, pady=4)

        self.var_cta_text = tk.StringVar()
        self.var_cta_link_url = tk.StringVar()
        self.var_cta_link_text = tk.StringVar()

        for i, (label, var, placeholder) in enumerate([
            ("안내 문구", self.var_cta_text, "예: 더 많은 정보가 궁금하다면 아래 링크를 확인하세요!"),
            ("링크 URL", self.var_cta_link_url, "예: https://open.kakao.com/o/xxxxx"),
            ("링크 텍스트", self.var_cta_link_text, "예: 카카오톡 오픈채팅 참여하기"),
        ], start=1):
            ttk.Label(cta_frame, text=label).grid(row=i, column=0, sticky='w', padx=8, pady=3)
            entry = ttk.Entry(cta_frame, textvariable=var, width=60)
            entry.grid(row=i, column=1, sticky='ew', padx=8, pady=3)
            # 플레이스홀더 힌트
            if not var.get():
                entry.insert(0, '')
        cta_frame.columnconfigure(1, weight=1)

        # ── 서식 설정 ──
        fmt_frame = ttk.LabelFrame(parent, text="서식 (볼드 / 하이라이트)")
        fmt_frame.pack(fill='x', padx=4, pady=(0, 8))

        self.var_bold = tk.BooleanVar(value=True)
        self.var_highlight = tk.BooleanVar(value=True)
        self.var_highlight_color = tk.StringVar(value='#FFFF00')

        ttk.Checkbutton(fmt_frame, text="AI 자동 볼드 처리", variable=self.var_bold).pack(
            anchor='w', padx=8, pady=2)
        ttk.Checkbutton(fmt_frame, text="AI 자동 하이라이트(음영) 처리", variable=self.var_highlight).pack(
            anchor='w', padx=8, pady=2)

        hl_row = ttk.Frame(fmt_frame)
        hl_row.pack(fill='x', padx=8, pady=4)
        ttk.Label(hl_row, text="음영 색상:").pack(side='left')
        ttk.Entry(hl_row, textvariable=self.var_highlight_color, width=10).pack(side='left', padx=4)
        ttk.Label(hl_row, text="(예: #FFFF00=노랑, #90EE90=연두)", foreground='gray').pack(side='left')

        # ── 메타프롬프트 / 커스텀 지시사항 ──
        prompt_frame = ttk.LabelFrame(parent,
                                       text="글 작성 스타일 메타프롬프트")
        prompt_frame.pack(fill='x', padx=4, pady=(0, 8))

        ttk.Label(prompt_frame,
                  text="AI가 글을 작성할 때 사용하는 메타프롬프트입니다. 기본값이 설정되어 있으며, 자유롭게 수정 가능합니다.",
                  foreground='gray', wraplength=800, justify='left').pack(
            anchor='w', padx=8, pady=(4, 0))

        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, height=10, wrap='word',
                                                      font=('맑은 고딕', 10))
        self.prompt_text.pack(fill='x', padx=8, pady=8)

        # ── 콘텐츠 길이 설정 ──
        content_frame = ttk.LabelFrame(parent, text="콘텐츠 길이 / 이미지 수")
        content_frame.pack(fill='x', padx=4, pady=(0, 8))

        self.var_min_length = tk.StringVar(value='1500')
        self.var_max_length = tk.StringVar(value='2500')
        self.var_max_paragraphs = tk.StringVar(value='6')
        self.var_image_count = tk.StringVar(value='6')

        grid_data = [
            ("최소 글자 수", self.var_min_length, "예: 1000, 1500, 2500"),
            ("최대 글자 수", self.var_max_length, "예: 1500, 2500, 4000"),
            ("최대 문단 수", self.var_max_paragraphs, "예: 4, 6, 8"),
            ("이미지 수 (유튜브 자동캡처)", self.var_image_count, "예: 4, 6, 8"),
        ]
        for i, (label, var, hint) in enumerate(grid_data):
            ttk.Label(content_frame, text=label).grid(row=i, column=0, sticky='w', padx=8, pady=3)
            ttk.Entry(content_frame, textvariable=var, width=10).grid(row=i, column=1, padx=8, pady=3)
            ttk.Label(content_frame, text=hint, foreground='gray').grid(row=i, column=2, sticky='w', padx=4, pady=3)

        # ── 저장 버튼 ──
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill='x', padx=4, pady=(4, 12))
        ttk.Button(btn_frame, text="설정 저장 (config.ini)", command=self._save_config).pack(
            side='right', padx=8, ipadx=16, ipady=4)

    # ──────────────────────────────────────────────
    # config.ini 읽기 / 쓰기
    # ──────────────────────────────────────────────
    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            self.notebook.select(self.tab_settings)
            self._log("⚙ config.ini가 없습니다. [설정] 탭에서 계정 정보를 입력한 뒤 저장하세요.")
            # 기본 메타프롬프트를 표시
            self.prompt_text.delete('1.0', 'end')
            self.prompt_text.insert('1.0', DEFAULT_WRITING_STYLE)
            return

        config = configparser.RawConfigParser()
        config.read(CONFIG_FILE, encoding='utf-8')

        self.var_naver_id.set(config.get('NAVER', 'id', fallback=''))
        self.var_naver_pw.set(config.get('NAVER', 'pw', fallback=''))
        self.var_cafe_url.set(config.get('NAVER', 'cafe_url', fallback=''))
        self.var_gemini_key.set(config.get('GEMINI', 'api_key', fallback=''))

        self.var_cta_enabled.set(config.getboolean('CTA', 'enabled', fallback=False))
        self.var_cta_text.set(config.get('CTA', 'text', fallback=''))
        self.var_cta_link_url.set(config.get('CTA', 'link_url', fallback=''))
        self.var_cta_link_text.set(config.get('CTA', 'link_text', fallback=''))

        self.var_bold.set(config.getboolean('FORMATTING', 'bold_enabled', fallback=True))
        self.var_highlight.set(config.getboolean('FORMATTING', 'highlight_enabled', fallback=True))
        self.var_highlight_color.set(config.get('FORMATTING', 'highlight_color', fallback='#FFFF00'))

        custom = config.get('PROMPT', 'custom_instructions', fallback='')
        self.prompt_text.delete('1.0', 'end')
        self.prompt_text.insert('1.0', custom if custom.strip() else DEFAULT_WRITING_STYLE)

        self.var_min_length.set(config.get('CONTENT', 'min_length', fallback='1500'))
        self.var_max_length.set(config.get('CONTENT', 'max_length', fallback='2500'))
        self.var_max_paragraphs.set(config.get('CONTENT', 'max_paragraphs', fallback='6'))
        self.var_image_count.set(config.get('CONTENT', 'image_count', fallback='6'))

        self._log("✓ config.ini 로드 완료")

    def _save_config(self):
        config = configparser.RawConfigParser()
        config['NAVER'] = {
            'id': self.var_naver_id.get(),
            'pw': self.var_naver_pw.get(),
            'cafe_url': self.var_cafe_url.get(),
        }
        config['GEMINI'] = {'api_key': self.var_gemini_key.get()}
        config['CTA'] = {
            'enabled': str(self.var_cta_enabled.get()).lower(),
            'text': self.var_cta_text.get(),
            'link_url': self.var_cta_link_url.get(),
            'link_text': self.var_cta_link_text.get(),
        }
        config['FORMATTING'] = {
            'bold_enabled': str(self.var_bold.get()).lower(),
            'highlight_enabled': str(self.var_highlight.get()).lower(),
            'highlight_color': self.var_highlight_color.get(),
        }
        config['PROMPT'] = {
            'custom_instructions': self.prompt_text.get('1.0', 'end-1c').strip(),
        }
        config['CONTENT'] = {
            'min_length': self.var_min_length.get(),
            'max_length': self.var_max_length.get(),
            'max_paragraphs': self.var_max_paragraphs.get(),
            'image_count': self.var_image_count.get(),
        }

        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)

        self._log(f"✓ 설정 저장 완료: {CONFIG_FILE}")
        messagebox.showinfo("저장 완료", "config.ini가 저장되었습니다.", parent=self.root)

    # ──────────────────────────────────────────────
    # 이미지 선택
    # ──────────────────────────────────────────────
    def _select_images(self):
        paths = filedialog.askopenfilenames(
            title="첨부할 이미지 선택 (여러 장 가능)",
            filetypes=[("이미지 파일", "*.jpg *.jpeg *.png *.bmp *.gif"), ("모든 파일", "*.*")],
            parent=self.root
        )
        self.image_paths = [os.path.abspath(p) for p in paths]
        if self.image_paths:
            self.img_label.config(text=f"선택된 이미지: {len(self.image_paths)}장", foreground="blue")
        else:
            self.img_label.config(text="유튜브: 자동 캡처 / 기타: 아래 버튼으로 선택", foreground="gray")

    # ──────────────────────────────────────────────
    # 로그 출력
    # ──────────────────────────────────────────────
    def _log(self, msg):
        def _append():
            self.log_text.config(state='normal')
            self.log_text.insert('end', msg + '\n')
            self.log_text.see('end')
            self.log_text.config(state='disabled')
        self.root.after(0, _append)

    # ──────────────────────────────────────────────
    # 실행
    # ──────────────────────────────────────────────
    def _on_run(self):
        if self.running:
            messagebox.showwarning("진행 중", "이미 실행 중입니다.", parent=self.root)
            return

        source = self.source_text.get('1.0', 'end-1c').strip()
        if not source:
            messagebox.showwarning("입력 필요", "소스 입력란에 유튜브 링크, URL, 또는 텍스트를 입력하세요.",
                                   parent=self.root)
            return

        # 필수 설정 확인
        if not self.var_naver_id.get() or not self.var_gemini_key.get():
            messagebox.showwarning("설정 필요",
                                   "[설정] 탭에서 네이버 계정과 Gemini API 키를 입력한 뒤 저장하세요.",
                                   parent=self.root)
            self.notebook.select(self.tab_settings)
            return

        # 설정 먼저 저장
        self._save_config_silent()

        self.running = True
        self.run_btn.config(state='disabled')
        self.status_label.config(text="실행 중...", foreground="green")

        # 별도 스레드에서 자동화 실행
        thread = threading.Thread(target=self._run_automation, args=(source,), daemon=True)
        thread.start()

    def _save_config_silent(self):
        """저장 알림 없이 config.ini 저장"""
        config = configparser.RawConfigParser()
        config['NAVER'] = {
            'id': self.var_naver_id.get(),
            'pw': self.var_naver_pw.get(),
            'cafe_url': self.var_cafe_url.get(),
        }
        config['GEMINI'] = {'api_key': self.var_gemini_key.get()}
        config['CTA'] = {
            'enabled': str(self.var_cta_enabled.get()).lower(),
            'text': self.var_cta_text.get(),
            'link_url': self.var_cta_link_url.get(),
            'link_text': self.var_cta_link_text.get(),
        }
        config['FORMATTING'] = {
            'bold_enabled': str(self.var_bold.get()).lower(),
            'highlight_enabled': str(self.var_highlight.get()).lower(),
            'highlight_color': self.var_highlight_color.get(),
        }
        config['PROMPT'] = {
            'custom_instructions': self.prompt_text.get('1.0', 'end-1c').strip(),
        }
        config['CONTENT'] = {
            'min_length': self.var_min_length.get(),
            'max_length': self.var_max_length.get(),
            'max_paragraphs': self.var_max_paragraphs.get(),
            'image_count': self.var_image_count.get(),
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)

    def _run_automation(self, source):
        """백그라운드 스레드에서 자동화 실행"""
        import io

        # stdout을 가로채서 로그창에 표시
        class LogWriter(io.TextIOBase):
            def __init__(self, log_func):
                self._log = log_func
            def write(self, msg):
                if msg and msg.strip():
                    self._log(msg.rstrip())
                return len(msg) if msg else 0
            def flush(self):
                pass

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        log_writer = LogWriter(self._log)
        sys.stdout = log_writer
        sys.stderr = log_writer

        try:
            # youtube_cafe_auto 모듈 함수들 사용
            import youtube_cafe_auto as auto

            # 전역 설정 주입
            auto.NAVER_ID = self.var_naver_id.get()
            auto.NAVER_PW = self.var_naver_pw.get()
            auto.CAFE_URL = self.var_cafe_url.get()
            auto.GEMINI_API_KEY = self.var_gemini_key.get()

            # optional_config 구성
            optional_config = {
                'cta_enabled': self.var_cta_enabled.get(),
                'cta_text': self.var_cta_text.get(),
                'cta_link_url': self.var_cta_link_url.get(),
                'cta_link_text': self.var_cta_link_text.get(),
                'bold_enabled': self.var_bold.get(),
                'highlight_enabled': self.var_highlight.get(),
                'highlight_color': self.var_highlight_color.get(),
                'custom_instructions': self.prompt_text.get('1.0', 'end-1c').strip(),
                'min_length': int(self.var_min_length.get() or 1500),
                'max_length': int(self.var_max_length.get() or 2500),
                'max_paragraphs': int(self.var_max_paragraphs.get() or 6),
                'image_count': int(self.var_image_count.get() or 6),
            }

            image_count = optional_config['image_count']

            # (1) 소스 유형 감지
            source_type, normalized_input = auto.detect_source_type(source)
            source_labels = {'youtube': '유튜브 영상', 'article': '웹 기사/페이지', 'text': '직접 텍스트'}
            self._log(f"[소스 유형] {source_labels.get(source_type, source_type)}")

            # (2) 소스별 콘텐츠 + 이미지 추출
            source_text = None
            image_paths = list(self.image_paths)  # GUI에서 선택한 이미지

            if source_type == 'youtube':
                v_id = auto.extract_video_id(normalized_input)
                if not v_id:
                    self._log("[오류] 유튜브 URL이 올바르지 않습니다.")
                    return
                source_text = auto.get_transcript(v_id)
                if not source_text:
                    self._log("[오류] 자막이 없어 진행할 수 없습니다.")
                    return
                # 유튜브: 사용자가 이미지를 선택하지 않았으면 자동 캡처
                if not image_paths:
                    image_paths = auto.extract_frames(normalized_input, image_count)

            elif source_type == 'article':
                source_text = auto.scrape_article(normalized_input)
                if not source_text:
                    self._log("[오류] 웹 페이지에서 텍스트를 추출할 수 없습니다.")
                    return

            elif source_type == 'text':
                source_text = normalized_input

            # (3) AI 글 생성
            has_images = len(image_paths) > 0
            actual_image_count = len(image_paths)
            title, body = auto.generate_blog_post(
                source_text, source_type, has_images, optional_config, actual_image_count
            )

            if has_images and "[IMAGE_HERE]" not in body:
                self._log("[경고] Gemini가 [IMAGE_HERE] 마커를 생성하지 않았습니다.")

            # (4) 네이버 카페 포스팅
            auto.post_to_naver_cafe(title, body, image_paths, optional_config)

            # (5) 정리
            auto.cleanup_temp_files()
            self._log("[정리] 임시 파일이 삭제되었습니다.")

        except Exception as e:
            import traceback
            self._log(f"\n[오류] {e}")
            self._log(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            self.running = False
            self.root.after(0, lambda: self.run_btn.config(state='normal'))
            self.root.after(0, lambda: self.status_label.config(text="완료", foreground="gray"))

    # ──────────────────────────────────────────────
    # 실행
    # ──────────────────────────────────────────────
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = CafeAutoApp()
    app.run()
