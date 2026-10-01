"""Render a style candidate from separately supplied, reviewed local inputs.

This entry point does not synthesize, download, approve or publish content.
"""
import argparse
import copy
import json
from pathlib import Path
import aimax_video_pipeline as renderer

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('presenter', 'screen', 'voice', 'srt', 'title-font', 'body-font', 'bgm', 'sfx'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--headline', required=True, help=r'Two lines separated by \n')
    p.add_argument('--credit', required=True)
    p.add_argument('--sfx-at', required=True, type=float, nargs='+', help='Reviewed narration section boundaries in seconds')
    p.add_argument('--out', required=True, type=Path, help='New empty directory')
    p.add_argument('--check-only', action='store_true')
    args = p.parse_args()
    for name in ('presenter', 'screen', 'voice', 'srt', 'title_font', 'body_font', 'bgm', 'sfx'):
        path = getattr(args, name).resolve()
        if not path.is_file() or path.stat().st_size == 0:
            p.error(f'Missing local input: {name}')
        setattr(args, name, path)
    headline = args.headline.replace('\\n', '\n')
    lines = headline.splitlines()
    if len(lines) != 2 or any(not s.strip() or len(s) > 18 for s in lines):
        p.error('Headline must have two nonempty lines, each at most 18 characters')
    if any(t < 0 for t in args.sfx_at):
        p.error('Negative effect timestamp')
    renderer.require_tool('ffmpeg')
    renderer.require_tool('ffprobe')
    cfg = json.loads((ROOT / 'render_config.template.json').read_text(encoding='utf-8'))
    cfg['title'].update(text=headline, font_path=str(args.title_font))
    cfg['watermark']['font_path'] = str(args.title_font)
    cfg['subtitle']['font_path'] = str(args.body_font)
    cfg['source']['text'] = args.credit
    cfg['audio']['music_path'] = str(args.bgm)
    cfg['audio']['effects'] = [{'path': str(args.sfx), 'start': t, 'volume': 0.5} for t in args.sfx_at]
    if args.out.exists():
        p.error('Output directory already exists; choose a new directory')
    if args.check_only:
        print('Input presence/configuration PASS; media quality and publication acceptance NOT validated')
        return
    args.out.mkdir(parents=True)
    ass = args.out / 'captions.ass'
    # Alignment and token punctuation must already be reviewed by the operator.
    renderer.srt_to_ass(args.srt, ass, cfg)
    renderer.render_final(args.presenter, ass, args.out / 'candidate.mp4', copy.deepcopy(cfg), args.screen, args.voice)
    (args.out / 'render_config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Candidate rendered; production acceptance, source evidence and audio gates still required')


if __name__ == '__main__':
    main()
