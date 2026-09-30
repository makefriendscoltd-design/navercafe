import pytest

import shorts_new_prepare as prepare


def test_canonical_prepare_refuses_published_source(monkeypatch):
    monkeypatch.setattr("reference_selection.delivered_source_keys", lambda project: set())
    monkeypatch.setattr("reference_selection.shorts_done", lambda key: True)
    monkeypatch.setattr("reference_selection.local_shorts_done", lambda key: False)
    with pytest.raises(RuntimeError, match="이미 발행 완료"):
        prepare._refuse_duplicate_canonical("abcdefghijk", "shorts")


def test_canonical_prepare_refuses_validated_review_variant(monkeypatch):
    monkeypatch.setattr("reference_selection.delivered_source_keys", lambda project: set())
    monkeypatch.setattr("reference_selection.shorts_done", lambda key: False)
    monkeypatch.setattr("reference_selection.local_shorts_done", lambda key: True)
    with pytest.raises(RuntimeError, match="검증된 canonical/review"):
        prepare._refuse_duplicate_canonical("abcdefghijk", "shorts")


def test_explicit_review_candidate_remains_allowed(monkeypatch):
    monkeypatch.setattr("reference_selection.shorts_done", lambda key: True)
    monkeypatch.setattr("reference_selection.local_shorts_done", lambda key: True)
    prepare._refuse_duplicate_canonical("abcdefghijk", "shorts-review-20261001")


def test_canonical_prepare_refuses_previous_delivery(monkeypatch):
    monkeypatch.setattr("reference_selection.delivered_source_keys",
                        lambda project: {"abcdefghijk"})
    monkeypatch.setattr("reference_selection.shorts_done", lambda key: False)
    monkeypatch.setattr("reference_selection.local_shorts_done", lambda key: False)
    with pytest.raises(RuntimeError, match="이전 쇼츠 납품"):
        prepare._refuse_duplicate_canonical("abcdefghijk", "shorts")
