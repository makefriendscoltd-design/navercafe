# Export validation — 2026-10-01

Passed on macOS with Python 3.12.13 and installed FFmpeg:

- All five Python modules compile; renderer wrapper and tailbite CLI help load.
- Exported renderer and tailbite files match the pinned Git source byte-for-byte.
  Tempo differs only in its policy import; prompt matches the pinned literal.
- Wrapper input preflight succeeded using generated test media and locally
  available production fonts. No private source footage or presenter was used.
- Real FFmpeg candidate render: 2.000 seconds, H.264, 1080 x 1920, 30 fps;
  AAC, 48 kHz, stereo. FFprobe confirmed streams and duration.
- A decoded frame was visually inspected: cyan two-line headline, central source
  test pattern, whole-token Korean subtitle, circular lower PIP and @aimax.
- Sharing files are UTF-8 text only, allowlisted, individually below 100 KB.
  No media, font binaries, credentials or absolute personal paths are included.
- The branch is an independent root commit: production history is not reachable
  from it. The canonical production worktree was not edited.

Scope limitations:

- System `python3` on this Mac is 3.9 and cannot import the original renderer.
  Use Python 3.10 or newer (3.12 was tested).
- This validates the portable rendering component and packaging, not full V7
  production acceptance. Private-asset visual/audio parity, paid voice generation,
  transcription quality and native Windows execution remain unverified.
- Silence editing and retiming helpers were source-compared/import-checked;
  they were not exercised end-to-end with a real voice/alignment in this audit.
- No upload, external message, scheduler or provider mutation was performed.
- Smoke media, frame captures, logs and local asset paths remain outside Git.
