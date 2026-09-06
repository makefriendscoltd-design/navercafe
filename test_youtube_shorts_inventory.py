import copy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import youtube_shorts_inventory as inventory
from youtube_shorts_aside_adapter import CHANNEL_ID, CHANNEL_NAME, KST
from youtube_shorts_publisher import InventoryError, ProviderInventory


def snapshot():
    return {'status':'pass','channel_id':CHANNEL_ID,'scan_id':'snapshot',
            'captured_at':datetime.now(timezone.utc).isoformat(),
            'pages':[{'page':1,'row_count':1,'next_disabled':True}],
            'rows':[{'identity':'sampleKey01','provider_id':'sampleKey01','status':'scheduled',
                     'title':'검증 원고','description':'원본 설명','urls':[],'page':1,
                     'metadata_origin':'studio_provider_row_model','model_channel_id':CHANNEL_ID,
                     'privacy':'VIDEO_PRIVACY_PRIVATE','draft_status':'DRAFT_STATUS_NONE',
                     'scheduled_raw':{'scheduledPublishings':[{'status':'SCHEDULED_PUBLISHING_STATUS_SCHEDULED',
                         'action':'SCHEDULED_PUBLISHING_ACTION_SET_PUBLIC','scheduledTimeSeconds':'1789556400'}]},
                     'published_seconds':'0','direct_metadata_inspected':True,'visibility_control_present':None}]}


def test_provider_epoch_is_converted_to_explicit_kst_without_ui_time_guess():
    manifest=SimpleNamespace(expected_channel=CHANNEL_NAME)
    value=inventory.normalize(snapshot(),manifest,'test')
    assert value['rows'][0]['scheduled_at']=='2026-09-16T20:00:00+09:00'
    ProviderInventory.from_mapping(value,manifest=manifest,now=datetime.now(KST),phase='test')


@pytest.mark.parametrize('change',[{'privacy':'VIDEO_PRIVACY_PUBLIC'}, {'model_channel_id':'other'}, {'draft_status':'DRAFT_STATUS_PUBLIC'}])
def test_provider_row_model_disagreement_is_rejected(change):
    raw=snapshot();raw['rows'][0].update(change)
    with pytest.raises(InventoryError):
        inventory.normalize(raw,SimpleNamespace(expected_channel=CHANNEL_NAME),'test')


def test_two_complete_snapshots_must_preserve_description(monkeypatch,tmp_path):
    first=snapshot();second=copy.deepcopy(first);second['rows'][0]['description']='changed during scan'
    results=iter([first,second]);monkeypatch.setattr(inventory,'capture',lambda root:next(results))
    manifest=SimpleNamespace(expected_channel=CHANNEL_NAME,video=tmp_path/'final.mp4')
    with pytest.raises(InventoryError,match='changed between'):
        inventory.scan(manifest,phase='test')
