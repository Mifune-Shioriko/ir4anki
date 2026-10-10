"""Coordinator contract; runs without any collection or network."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from flow import create, reconcile, replace_slot

def test_even_actual_sequence():
    for s,c in [(0,0),(0,5),(5,0),(1,7),(7,1),(3,8),(8,3),(4,4)]:
        readings=[{'path':'a','chunk_key':str(i)} for i in range(s)]
        cards=list(range(c))
        f=create(readings,cards,'r','c')
        seq=[]
        while True:
            reconcile(f,readings,cards)
            if f['phase']=='complete':break
            if f['phase']=='reading':
                seq.append('R');readings.pop(0)
            else:
                seq.append('C');cards.pop(0)
        expected=[]
        if s:
            for i in range(1,s+1):
                expected+=['R']+['C']*((i*c)//s-((i-1)*c)//s)
        else:expected=['C']*c
        assert seq==expected
        assert f['reading_slots']==s and f['card_count']==c

def test_split_drift_undo():
    a={'path':'a','chunk_key':'1'};b={'path':'a','chunk_key':'2'}
    f=create([a,b],[10,11,12,13],'r','c')
    children=[{'path':'a','chunk_key':'3'},{'path':'a','chunk_key':'4'}]
    replace_slot(f,a,children)
    reconcile(f,children+[b],[10,11,12,13]);assert f['phase']=='reading'
    reconcile(f,children[1:]+[b],[10,11,12,13]);assert f['phase']=='reading'
    reconcile(f,[b],[10,11,12,13]);assert f['phase']=='review'
    reconcile(f,[b],[12,13]);assert f['phase']=='reading'
    reconcile(f,[b],[11,12,13]);assert f['phase']=='review' # cross-phase undo
    reconcile(f,[],[]);assert f['phase']=='complete'

if __name__=='__main__':
    test_even_actual_sequence();test_split_drift_undo();print('2 policy tests passed')
