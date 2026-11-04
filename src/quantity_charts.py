"""Stage 7 quantity summary charts — NOT reinforcement or construction drawings."""
from collections import defaultdict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def bbs_weight_chart(bbs:dict):
    by_member=defaultdict(float)
    for line in bbs['lines']:
        by_member[line['member'] or 'Unlabelled']+=line['weight_kg']
    order=sorted(by_member,key=by_member.get)
    fig,ax=plt.subplots(figsize=(8.8,max(3.4,0.37*len(order)+1.5)))
    ax.barh(order,[by_member[x] for x in order])
    ax.set_xlabel('Net scheduled steel (kg) — before allowance')
    ax.set_title('BBS weight by reference member group')
    ax.grid(axis='x',alpha=0.18)
    fig.tight_layout()
    return fig


def boq_concrete_chart(boq:dict):
    rows=[r for r in boq['core_quantities'] if r['unit']=='m³' and not r['description'].startswith('TOTAL')]
    fig,ax=plt.subplots(figsize=(8,4))
    ax.barh([r['description'] for r in rows],[r['quantity'] for r in rows])
    ax.set_xlabel('Concrete volume (m³)')
    ax.set_title('Culvert-only concrete quantity breakdown')
    ax.grid(axis='x',alpha=0.18)
    fig.tight_layout()
    return fig
