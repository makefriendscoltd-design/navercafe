# AIMAX Shorts style handoff

This is a small, isolated style export from `makefriendscoltd-design/navercafe`.
`PROVENANCE.json` pins the source commit and file hashes. The live production entry
point is `shorts_v7_builder.py` in the canonical repository; it is not replaced or
modified by this export. This branch has no parent history and must not be merged
into production main as an application replacement.

## Get this branch

```sh
git clone --single-branch --depth 1 --branch share/aimax-shorts-style-20261001 https://github.com/makefriendscoltd-design/navercafe.git aimax-shorts-style
cd aimax-shorts-style
python3 render_style.py --help
```

Use Python 3.10+ and FFmpeg/ffprobe with libass, libx264, AAC, loudnorm and subtitle
filters. No Python packages are needed for the included render and audio scripts.
Windows users may use `python` instead of `python3`; native Windows rendering is
not yet verified. Keep the working path simple and supply absolute input paths.

## What is included

- `SCRIPT_PROMPT.md`: the exact v25.0 production prompt extracted from policy.
  Apply it to one source's transcript; its historical filename does not require
  NotebookLM. Its fixed currency conversion is a pinned editorial instruction,
  not a statement of the current exchange rate.
- `style_policy.json`: selected current production constants; the numeric authority
  for this export. `style_policy.py` loads it for the tempo helper.
- `render_config.template.json`: sanitized visual template. Source-specific text,
  presenter paths, source hashes and old effect timestamps have been removed.
- `aimax_video_pipeline.py`: byte-identical renderer used by the production builder.
  Its legacy CLI has optional fallbacks; use `render_style.py` for this handoff.
- `build_tailbite_audio.py`: byte-identical continuous narration silence editor.
  Run `python3 build_tailbite_audio.py --help` for alignment/script input arguments.
- `shorts_narration_tempo.py`: production retiming helper, with only its policy
  import redirected. This is a library, not an end-to-end TTS command. Its `retime`
  function requires aligned captions, seven logical sections, a reference SRT,
  and parse/duration/token-cleaning callbacks from the caller.
- `render_style.py`: portable candidate rendering entry point with mandatory
  local inputs; no download, voice API, account access, upload or scheduling.

## Reproduction sequence

1. Use the supplied prompt with a verified source transcript. Preserve its six
   body paragraphs, select headline candidate 1, then append the fixed CTA:
   `OO분 짜리 영상 내용을 모두 정리했습니다.` followed by a blank line and
   `이 자료 궁금하신 분들은 댓글에 OO 남겨주세요.` The first OO is the original
   video's duration in minutes; the second is a relevant two-character Korean
   keyword. Keep source evidence and avoid adding unverified claims.
2. Generate one continuous take with the owner's separately authorized
   ElevenLabs voice. Model and voice settings are in policy. The private voice
   identifier and credentials are not included. Tailbite silence editing and
   tempo correction follow policy. Align subtitles to the edited audio, preserve
   whole tokens, remove edge punctuation and retain internal product-name dots.
3. Privately supply authorized source footage, approved presenter recording,
   aligned SRT, processed narration, licensed BGM/SFX, and the exact fonts
   BM HANNA 11yrs old and Cafe24 Ohsquare. Put both font files in the same local
   font directory so libass can resolve them. Nothing in `assets/` or `inputs/`
   is to be committed. Do not replace the presenter with another person's face.
4. Run the command below first with `--check-only`, then without it and with a
   fresh output directory. Replace every placeholder and effect timestamp.

```sh
python3 render_style.py --presenter inputs/presenter.mp4 --screen inputs/source.mp4 --voice inputs/narration.wav --srt inputs/aligned.srt --title-font assets/fonts/BMHANNA_11yrs_ttf.ttf --body-font assets/fonts/Cafe24Ohsquare.ttf --bgm assets/bgm/approved.mp3 --sfx assets/sfx/approved.mp3 --headline '첫째 줄\n둘째 줄' --credit '출처: 확인한 제작자' --sfx-at 10 20 30 40 50 --out work/candidate-001 --check-only
```

The shared renderer is a composition component. Narration preparation, production
source/identity checks and exact audio calibration performed by the full V7
builder are NOT replaced by this command. A generated `candidate.mp4` is not an
approved or publishable production artifact just because rendering succeeded.

## Acceptance before use

- Compare geometry and typography with `style_policy.json` and the template:
  central source, bottom circular muted presenter, cyan headline and @aimax.
  The source and presenter audio must not be in the output mix.
- Measure every headline line at the exact font/size: maximum width comes from
  policy. The wrapper's character-count check alone does not prove it fits.
- Check the full narration, final CTA and subtitle synchronization; no truncated
  ending. Check initial/middle/final pace and the allowed final/initial ratio.
- Measure narration, BGM, SFX and final audio against every `AUDIO_GATES` field.
  The template's volume multipliers alone cannot guarantee these levels for
  different inputs. Calibrate the local inputs and rerender as needed.
- Inspect representative frames and listen to the entire candidate. Run the
  canonical production acceptance pipeline separately if it will be published.

## Explicit exclusions

No secrets, API keys, cookies, auth/session files, customer data, source transcripts,
private presenter recordings or asset fingerprints, media, font binaries, rendered
outputs, source-specific manifests, queues, channel/provider URLs or schedules.
The full production builder and its account/asset-dependent automation are not
exported. LTX2.5, long-form style 1 and Team Automation Hub files are out of scope
and were not modified. Public sharing does not grant licenses to separately
obtained media or fonts.

## Validation scope

See `VALIDATION.md`. Synthetic smoke inputs/results are deliberately outside Git.
Private-asset parity and native Windows execution remain unverified.
