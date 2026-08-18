# Repository Instructions

This repository is a Windows-focused Naver Cafe automation tool. It turns YouTube videos, news articles, or direct text into Korean cafe/blog-style posts with Gemini, then publishes them through Naver Cafe using Selenium plus clipboard/desktop automation.

## Run

Use Python 3.12+.

```powershell
python -m pip install -r requirements.txt
python youtube_cafe_auto.py
```

GUI mode:

```powershell
python cafe_gui.py
```

Server mode:

```powershell
python server.py
```

Local secrets and browser sessions live outside version control. `config.ini` is created by the app and is intentionally ignored.

## Architecture

- `youtube_cafe_auto.py`: core engine plus CLI entry point.
- `cafe_gui.py`: Tkinter GUI wrapper around `youtube_cafe_auto`.
- `telegram_content_controller.py`: controller for Telegram-driven content jobs.
- `server.py`: FastAPI server wrapper.
- `comment_bot.py`: Naver Cafe comment automation.

Pipeline:

```text
source input -> detect source type -> extract content -> Gemini post generation -> Selenium Naver Cafe publishing
```

Gemini output may contain these custom markers:

- `[IMAGE_HERE]`: image insertion point.
- `[BLOCKQUOTE]...[/BLOCKQUOTE]`: quote block.
- `[BOLD]...[/BOLD]`: bold text.
- `[HIGHLIGHT]...[/HIGHLIGHT]`: highlighted text.

Naver Smart Editor automation depends on DOM selectors and clipboard paste behavior. Prefer small, tested changes around selectors and editor actions.

## Mandatory Fact Check Before Publishing

Before saving or publishing any generated article, fact-check current claims with live web search. This applies to direct instructions, Telegram controller jobs, and manual script runs.

- Treat model names, versions, release dates, prices, benchmark numbers, and company claims from source scripts as unverified.
- Verify each retained claim with current web search, preferably official vendor release pages for version numbers.
- If a claim is stale, fix or remove only that claim. Do not rewrite unrelated prose.
- Card news slides can repeat stale claims. If the body is corrected, update `04_cardnews_deck.json` and re-render the cards too.
- Report `factcheck=ok` when nothing changed or `factcheck=fixed` when corrections were made.
- If an already-approved body has a stale fact at publish time, stop instead of silently editing and report `status=blocked reason=stale_fact:<detail>`.

## Development Notes

- Keep UI strings in Korean.
- Avoid committing generated runtime state: `config.ini`, `telegram_content_controller_state.json`, `telegram_previews/`, Chrome profiles, temporary videos, and extracted frames.
- Use `python -m py_compile` for a quick syntax check after Python edits.
- Browser/desktop automation may require an interactive Windows session; headless CI is not a reliable validation environment for publish flows.
