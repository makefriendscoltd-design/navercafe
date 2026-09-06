"""Explicit one-for-one replacement while preserving complete provider snapshots."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path

import content_production_policy as policy
import youtube_shorts_publisher as pub


RETIRE_JS = r'''
let p=null;let saved=0;
const one=async(selector,label)=>{const x=p.locator(selector);if(await x.count()!==1)throw new Error(label+' cardinality');return x;};
try{
 p=await openTab(`https://studio.youtube.com/video/${payload.old_id}/edit`);
 const end=Date.now()+30000;
 while(Date.now()<end){if(await p.locator('#title-textarea #textbox').count()===1&&await p.locator('#title-textarea #textbox').isVisible())break;await sleep(300);}
 const state=await p.evaluate(()=>({host:location.hostname,path:location.pathname,ids:[...new Set([...document.querySelectorAll('a[href*="/channel/"]')].map(a=>((a.getAttribute('href')||'').match(/\/channel\/(UC[A-Za-z0-9_-]{22})/)||[])[1]).filter(Boolean))]}));
 if(state.host!=='studio.youtube.com'||state.path!==`/video/${payload.old_id}/edit`||state.ids.length!==1||state.ids[0]!==payload.channel_id)throw new Error('old provider identity mismatch');
 const title=await one('#title-textarea #textbox','title'),desc=await one('#description-textarea #textbox','description');
 if((await title.innerText()).trim()!==payload.old_title||(await desc.innerText()).trim()!==payload.old_description.trim())throw new Error('old metadata drift');
 await (await one('ytcp-video-metadata-visibility #container','visibility')).click();
 await p.getByText('저장 또는 게시',{exact:true}).click();
 await sleep(350);
 const popup=await one('ytcp-video-visibility-edit-popup','visibility popup');
 const privateChoice=p.locator('ytcp-video-visibility-edit-popup #private-radio-button[name="PRIVATE"]');
 if(await privateChoice.count()!==1)throw new Error('private option cardinality');
 if((await privateChoice.getAttribute('aria-checked'))!=='true')await privateChoice.click();
 if((await privateChoice.getAttribute('aria-checked'))!=='true')throw new Error('private choice not selected');
 if(payload.prepare_only){emit({status:'prepared',old_id:payload.old_id,save_clicks:0,preview:(await popup.innerText()).slice(0,900)});}
 else{
  await popup.locator('#save-button').click();await sleep(300);
  const save=await one('ytcp-button#save','editor save');
  if(!await save.isEnabled())throw new Error('save unavailable');
  saved=1;await save.click();
  const deadline=Date.now()+30000;while(Date.now()<deadline&&await save.isEnabled())await sleep(250);
  if(await save.isEnabled())throw new Error('save acknowledgement missing');
  emit({status:'save_acknowledged',old_id:payload.old_id,save_clicks:saved});
 }
}catch(e){emit({status:'blocked',old_id:payload.old_id,save_clicks:saved,error:String(e?.message||e)});}
finally{try{if(p)await p.close();}catch(_){}}
'''


def text_hash(value):
    return hashlib.sha256(value.strip().encode()).hexdigest()


def validate_contract(manifest):
    c = manifest.replacement
    if not isinstance(c, dict) or c.get('source_key') != manifest.source_key:
        raise pub.ManifestError('Replacement must bind the same source')
    if not pub.VIDEO_ID_RE.fullmatch(str(c.get('provider_id', ''))):
        raise pub.ManifestError('Replacement provider ID missing')
    if not c.get('title') or not pub.SHA256_RE.fullmatch(str(c.get('description_sha256', ''))):
        raise pub.ManifestError('Replacement old metadata binding missing')
    return c, pub._parse_datetime(c.get('scheduled_at'), label='replacement slot')


def bind_old(inventory, manifest, *, allow_private=False):
    c, slot = validate_contract(manifest)
    matches = [r for r in inventory.rows if r.provider_id == c['provider_id']]
    if len(matches) != 1:
        raise pub.AmbiguousProviderState('Replacement old ID is not a single provider row')
    old = matches[0]
    if (old.title != c['title'] or text_hash(old.description) != c['description_sha256']
            or not any(pub._exact_url_present(old.description, u) for u in manifest.canonical_urls)):
        raise pub.AmbiguousProviderState('Replacement source or old metadata changed')
    if old.status == 'scheduled' and old.scheduled_at == slot:
        return old
    if allow_private and old.status == 'private' and not old.scheduled_at:
        return old
    raise pub.AmbiguousProviderState('Replacement old visibility or schedule changed')


class ReplacementPublisher(pub.YouTubeShortsPublisher):
    supports_replacement = True
    def _scan(self, manifest, phase, fixed_now):
        full = super()._scan(manifest, phase, fixed_now)
        old = bind_old(full, manifest)
        # The complete provider snapshot remains in the inventory evidence files.
        # Only this explicitly bound predecessor is excluded from candidate matching.
        return replace(full, rows=tuple(r for r in full.rows if r.provider_id != old.provider_id))

    def _plan(self, inventory, now):
        _, slot = validate_contract(self.replacement_manifest)
        if slot <= now:
            raise pub.ManualRemediationRequired('Original replacement slot elapsed')
        policy.validate_schedule([*inventory.occupancy_slots(now), slot])
        return slot

    def _finish_crm(self, manifest, store, journal):
        if not journal.get('verified'):
            raise pub.AmbiguousProviderState('New provider verification required before retiring old video')
        from youtube_shorts_aside_adapter import CHANNEL_ID
        full = super()._scan(manifest, 'replacement_before_retirement', None)
        old = bind_old(full, manifest, allow_private=True)
        new_id = journal['verified']['provider_id']
        current = [r for r in full.rows if r.provider_id == new_id]
        _, slot = validate_contract(manifest)
        if (len(current) != 1 or current[0].status != 'scheduled' or current[0].scheduled_at != slot
                or current[0].title != manifest.title or current[0].description.strip() != manifest.description.strip()):
            raise pub.AmbiguousProviderState('Replacement new row is not the exact scheduled candidate')
        retirement = journal.setdefault('replacement_retirement', {'save_reservation_count': 0})
        if old.status == 'scheduled':
            if retirement['save_reservation_count'] != 0:
                raise pub.AmbiguousProviderState('Old retirement was already attempted; reconcile only')
            retirement['save_reservation_count'] = 1
            store.event(journal, 'old_retirement_reserved')
            result = self.provider._run(RETIRE_JS, {
                'old_id': old.provider_id, 'old_title': old.title, 'old_description': old.description,
                'channel_id': CHANNEL_ID, 'prepare_only': False,
            }, cwd=manifest.video.parent, timeout=120)
            retirement['receipt'] = dict(result)
            store.event(journal, 'old_retirement_returned')
            # An uncertain UI acknowledgement is reconciled by provider state, never a second click.
            full = super()._scan(manifest, 'replacement_after_retirement', None)
            old = bind_old(full, manifest, allow_private=True)
        if old.status != 'private':
            raise pub.AmbiguousProviderState('Old schedule is still active')
        final_new = [r for r in full.rows if r.provider_id == new_id]
        if (len(final_new) != 1 or final_new[0].status != 'scheduled' or final_new[0].scheduled_at != slot
                or final_new[0].title != manifest.title
                or final_new[0].description.strip() != manifest.description.strip()):
            raise pub.AmbiguousProviderState('New schedule changed during predecessor retirement')
        retirement['verified_status'] = 'private'
        retirement['old_provider_id'] = old.provider_id
        retirement['new_provider_id'] = new_id
        store.event(journal, 'replacement_pair_verified')
        return super()._finish_crm(manifest, store, journal)

    def run(self, manifest_path, **kwargs):
        self.replacement_manifest = pub.load_manifest(manifest_path)
        validate_contract(self.replacement_manifest)
        return super().run(manifest_path, **kwargs)
