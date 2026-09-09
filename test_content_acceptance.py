import json

import content_acceptance as acceptance
import cafe_manifest_publisher as cafe


def test_longform_source_is_independent_of_cafe_draft(tmp_path, monkeypatch):
    root = tmp_path / 'YAsxyoTWFDA-20260909'
    calls = []
    def measure(key):
        calls.append(key)
        return {'seconds': 1350, 'width': 640, 'height': 360}
    monkeypatch.setattr(cafe, 'measure_source_video', measure)
    assert acceptance.check_source(root) == []
    assert calls == ['YAsxyoTWFDA']


def test_short_source_remains_rejected_without_cafe_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(cafe, 'measure_source_video', lambda key:
                        {'seconds': 54, 'width': 360, 'height': 640})
    assert acceptance.check_source(tmp_path / 'YAsxyoTWFDA-20260909')


def test_corrupt_cafe_json_does_not_abort_other_channel_checks(tmp_path, monkeypatch):
    root = tmp_path / 'YAsxyoTWFDA-20260909'
    (root / 'cafe').mkdir(parents=True)
    (root / 'cafe/06_cafe_manifest.json').write_text('{broken')
    (root / 'cafe/11_local_validation.json').write_text('{"status":"pass"}')
    monkeypatch.setattr(cafe, 'measure_source_video', lambda key:
                        {'seconds': 1350, 'width': 640, 'height': 360})
    monkeypatch.setattr(acceptance, 'check_shorts', lambda root: [])
    result = acceptance.audit(root)
    assert result['channels']['source']['status'] == 'pass'
    assert result['channels']['cafe']['status'] == 'fail'
    assert result['channels']['shorts']['status'] == 'pass'


def test_mismatched_source_id_is_not_silently_remeasured(tmp_path, monkeypatch):
    root = tmp_path / 'YAsxyoTWFDA-20260909'
    (root / 'cafe').mkdir(parents=True)
    (root / 'cafe/06_cafe_manifest.json').write_text(json.dumps({'source_key':'abcdefghijk'}))
    assert acceptance.check_source(root)
