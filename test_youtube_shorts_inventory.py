import copy
import json
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


def run_scan(monkeypatch, tmp_path, snapshots):
    results=iter(snapshots)
    monkeypatch.setattr(inventory,'capture',lambda root:next(results))
    manifest=SimpleNamespace(expected_channel=CHANNEL_NAME,video=tmp_path/'final.mp4')
    return inventory.scan(manifest,phase='test')


def changed_snapshot(value):
    changed=copy.deepcopy(value)
    changed['rows'][0]['description']='changed during scan'
    return changed


def test_stable_first_two_complete_snapshots_pass_immediately(monkeypatch,tmp_path):
    first=snapshot()
    result=run_scan(monkeypatch,tmp_path,[first,copy.deepcopy(first)])
    assert result['metadata_capture_method']=='two_matching_provider_row_model_snapshots'


def test_one_drift_then_two_consecutive_matching_snapshots_pass_on_third(monkeypatch,tmp_path):
    first=snapshot();second=changed_snapshot(first)
    result=run_scan(monkeypatch,tmp_path,[first,second,copy.deepcopy(second)])
    assert result['rows'][0]['description']=='changed during scan'


def test_nonconsecutive_a_b_a_b_never_stabilizes(monkeypatch,tmp_path):
    first=snapshot();second=changed_snapshot(first)
    with pytest.raises(InventoryError,match='did not stabilize in 4 complete snapshots'):
        run_scan(monkeypatch,tmp_path,[first,second,copy.deepcopy(first),copy.deepcopy(second)])


def test_invalid_capture_fails_immediately_without_retry(monkeypatch,tmp_path):
    calls=[]
    invalid=snapshot();invalid['status']='blocked';invalid['error']='provider row invalid'
    def capture(_root):
        calls.append(True)
        return invalid
    monkeypatch.setattr(inventory,'capture',capture)
    manifest=SimpleNamespace(expected_channel=CHANNEL_NAME,video=tmp_path/'final.mp4')
    with pytest.raises(InventoryError,match='provider row invalid'):
        inventory.scan(manifest,phase='test')
    assert len(calls)==1


def test_pagination_state_shares_the_immutable_row_snapshot_and_keeps_enabled_gate():
    script=inventory.CAPTURE_JS
    assert "return {values,next_count:nextControls.length" in script
    assert "if(pageSnapshot.next_count!==1)" in script
    assert "pageSnapshot.next_aria_disabled==='true'" in script
    assert "pageSnapshot.next_disabled_attribute!==null" in script
    assert "!await next.isEnabled()" in script
    assert "await next.count()" not in script
    assert "await next.getAttribute('aria-disabled')" not in script
    assert "await next.getAttribute('disabled')" not in script
    assert script.index('const pageSnapshot=await p.evaluate') < script.index('const values=pageSnapshot.values')
    assert script.index('const values=pageSnapshot.values') < script.index('await next.click()')


def test_channel_recovery_is_sealed_behind_explicit_permission_denial():
    script=inventory.CAPTURE_JS
    denial=script.index("if(!denied)return")
    switcher=script.index("p.goto(payload.channel_switcher_url)")
    click=script.index("channelSelectionClicks=1")
    inventory_gate=script.index("v.channelId!==channelId")
    assert denial < switcher < click < inventory_gate
    assert "querySelectorAll('ytd-account-item-renderer')" in script
    assert "title===name&&lines.includes(handle)" in script
    assert "if(count!==1)" in script
    assert script.count("channelSelectionClicks=1") == 1


def test_channel_recovery_payload_names_only_the_sealed_existing_target(monkeypatch,tmp_path):
    captured={}
    def fake_run(_script,**kwargs):
        captured['script']=_script
        return {'status':'blocked','error':'expected read-only test stop'}
    monkeypatch.setitem(__import__('sys').modules,'aside_browser',SimpleNamespace(
        JS_COMMON='',_payload_expression=lambda value:json.dumps(value,ensure_ascii=False),run_repl=fake_run))
    assert inventory.capture(tmp_path)['status']=='blocked'
    script=captured['script']
    assert 'https://www.youtube.com/channel_switcher' in script
    assert '나민수 AI' in script and '@naminsoo_aimax' in script
    assert 'UCWyi-m_CdIbRpcwZN6MgBfg' in script
