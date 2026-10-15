"""Whole-round policy only; neither reading nor card scheduling lives here."""
import uuid

def identity(item):
    return [item['path'], str(item['chunk_key'])]

def create(readings, cards, reading_round, card_round, legacy=False):
    s,c=len(readings),len(cards)
    return dict(id=uuid.uuid4().hex,policy='even-floor-v1',reading_slots=s,card_count=c,
                blocks=[i*c//s-(i-1)*c//s for i in range(1,s+1)] if s else [],
                slots=[[identity(r)] for r in readings], card_ids=list(cards),
                reading_round=reading_round,card_round=card_round,legacy=legacy,
                status='active',phase='reading' if s else 'review')

def replace_slot(flow, parent, children):
    key=identity(parent)
    for slot in flow['slots']:
        if key in slot:
            at=slot.index(key);slot[at:at+1]=[key]+[identity(c) for c in children]
            # Keep the parent as an alias: if a crash precedes the reading
            # transaction, that original pending item still owns the slot.
            break

def answer_count(round_state):
    """Only explicit answer statistics establish recalls; handled can include drift.

    Old rounds without this counter still recover their queues and undo token,
    but their ambiguous historical handled count must not invent answers.
    """
    return max(0, (round_state or {}).get('answered_count', 0))


def reconcile(flow, readings, cards, *, answered_count=0):
    pending={tuple(identity(r)) for r in readings}
    consumed=sum(not any(tuple(k) in pending for k in slot) for slot in flow['slots'])
    # Removed/drifted cards consume their original position, but are not answers.
    handled=len(set(flow['card_ids'])-set(cards))
    # A successful round answer consumes one position, even Again (the native
    # learning schedule does not change that business-queue fact). Withdrawals
    # consume none. Include restored/prior-round pending debt in the denominator.
    answered=max(0, answered_count)
    s,c=flow['reading_slots'],answered+len(set(cards))
    target=consumed*c//s if s else c
    # Undo can reinsert a prior-round card; drain that debt as well.
    debt=any(cid not in flow['card_ids'] for cid in cards)
    flow.update(reading_consumed=consumed,card_handled=handled,block_target=target,
                card_answered=answered,effective_card_count=c,
                blocks=[i*c//s-(i-1)*c//s for i in range(1,s+1)] if s else [])
    flow['phase']=('review' if cards and (answered<target or not readings or debt)
                   else 'reading' if readings else 'review' if cards else 'complete')
    flow['status']='complete' if flow['phase']=='complete' else 'active'
    return flow
