# Handoff: Telegram cafe job KUeW3zzF49A-78422

## Request

- Source: `https://youtu.be/KUeW3zzF49A?si=wuD9jK9UP0xRdExa`
- Goal: create and publish a Naver cafe article using the existing cafe pipeline.
- Hard guards:
  - Do not publish if cafe images are `0`.
  - Do not publish if the same source URL was already published.
  - If login is required, keep the browser visible and report `LOGIN_REQUIRED`.
  - Final marker must be one of:
    - `TELEGRAM_RESULT cafe status=published url=<cafe_article_url> images=<count>`
    - `TELEGRAM_RESULT cafe status=blocked reason=<reason>`

## Environment Notes

- Repo: `D:\coding\ccidacafe`
- Branch: `feat/notebooklm-cafe-publisher`
- Python was not on PATH in the sandboxed shell.
- User-provided interpreter:
  `C:\Users\hey_m\AppData\Local\Programs\Python\Python312\python.exe`
- Required command used:
  `C:\Users\hey_m\AppData\Local\Programs\Python\Python312\python.exe notebook_cafe_auto.py "https://youtu.be/KUeW3zzF49A?si=wuD9jK9UP0xRdExa" --publish`

## What Happened

1. Duplicate guard was checked through the existing pipeline state.
   `published_posts.json` only showed a prior source key for `youtube:CB5bG4mvnS0`, not `youtube:KUeW3zzF49A`.
2. NotebookLM generated a manuscript for the source.
3. The pipeline requested 5 section images.
4. `yt-dlp` download failed with YouTube HTTP 403 and browser cookie issues, but the browser fallback captured 5 images successfully.
5. Zero-image guard did not block because captured image count was `5`.
6. The publish attempt reached Naver cafe automation, but the browser landed on Naver login URLs when opening the editor:
   - `https://nid.naver.com/nidlogin.login?...articles/write...`
   - `https://nid.naver.com/nidlogin.login?mode=form...ArticleWrite...`
7. The editor title input `.textarea_input` was not found because login was required.
8. The final reported marker for that run was:
   `TELEGRAM_RESULT cafe status=blocked reason=LOGIN_REQUIRED`

## Code State

The branch contains fixes for the recurring login failure mode:

- `youtube_cafe_auto.py`
  - Adds `naver_session.json` cookie persistence.
  - Restores saved Naver cookies before requiring a manual login.
  - Stops navigating away from the login page during the wait loop.
  - Saves Naver cookies immediately after successful human login.
- `.gitignore`
  - Ignores `naver_session.json` because it contains authentication cookies.

## Continue From Here

1. Open a visible login helper or run the publish pipeline in a visible browser.
2. Complete Naver login manually if prompted.
3. Re-run the publish command above.
4. Confirm the final marker uses real values only.

Do not commit local run logs, browser profiles, cookies, `config.ini`, `published_posts.json`, or `naver_session.json`.
