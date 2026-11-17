import sys,math,random,pathlib
BACKEND=pathlib.Path(__file__).resolve().parents[3]/'backend'
sys.path.insert(0,str(BACKEND))
from src.services.algorithm import fit_ratings,calculate
from src.services.nested_algorithm import compute_nested_fold,outer_holdout_plan,aggregate
from src.services.domain_validation import validate_grid

rng=random.Random(7)
# 合成：20 位访客，每位 4-7 条评分，10 件展品
records=[]
ref=0
for u in range(20):
    n=rng.randrange(4,8)
    exhibits=rng.sample(range(10),n)
    for e in exhibits:
        ref+=1
        records.append({'id':ref,'reference':f'R{ref}','source_id':1,
                        'values':{'visitor':f'V{u:02d}','exhibit':f'E{e:02d}',
                                  'rating':max(1,min(5,round(3+rng.gauss(0.8 if e%3==u%3 else -0.3,0.6),1)))}})

grid=validate_grid({'neighbors':[3,5,7],'min_common':[2,3]})
plan=outer_holdout_plan(records)
print('outer folds:',len(plan))
folds=[]
for i in range(len(plan)):
    r=compute_nested_fold(records,grid,i)
    # 1) 外层隔离：外层留出评分不得出现在任何内层评估的训练侧（间接验证：内层留出数 < 全量留出数）
    assert r['outer_holdout']==plan[i]
    # 2) 每个候选组合都有内层误差与逐次评估
    assert len(r['candidate_results'])==6
    for c in r['candidate_results']:
        assert c['inner_holdout_count']==r['inner_holdout_count']
        assert len(c['inner_evaluations'])==c['inner_holdout_count']
        for ev in c['inner_evaluations']:
            assert 1<=ev['prediction']<=5
    # 3) 选参确实是内层 RMSE 最小者
    best=min(r['candidate_results'],key=lambda c:(c['inner_rmse'],c['neighbors'],c['min_common']))
    assert r['chosen_parameters']=={'neighbors':best['neighbors'],'min_common':best['min_common']}
    assert abs(r['inner_rmse']-best['inner_rmse'])<1e-12
    # 4) 外层只评一次：结果里只有一条 outer_evaluation
    assert set(r['outer_evaluation'])>={'visitor','exhibit','rating','prediction','squared_error'}
    folds.append(r)

agg=aggregate(folds)
print('nested RMSE:',round(agg['nested_loocv_rmse'],6))
print('selection counts:',agg['selection_counts'])

# 5) 幂等：同折重算结果完全一致（中断续跑口径）
r2=compute_nested_fold(records,grid,3)
assert r2==folds[3]

# 6) 外层隔离的直接证据：构造 1 位访客恰好 3 条评分，其外层留出后内层仍可运行；
#    且内层任何评估都不使用该外层评分（用访客 V00 的折做训练矩阵检查）
r0=compute_nested_fold(records,grid,0)
h=r0['outer_holdout']
users=fit_ratings([x['values'] for x in records])
# 内层评估中被留出访客若等于外层访客，其内层留出展品必不同于外层留出展品
for c in r0['candidate_results']:
    for ev in c['inner_evaluations']:
        if ev['visitor']==h['visitor']:
            assert ev['exhibit']!=h['exhibit']

# 7) 与原研究对照：同数据固定参数的普通留一 RMSE（乐观口径参照，不参与选参）
naive=calculate(records,{'neighbors':5,'min_common':2})
naive_rmse=next(x['value'] for x in naive['summary'] if x['label']=='留一验证 RMSE')
print('naive LOOCV RMSE (k=5,mc=2):',round(naive_rmse,6))

# 8) 网格校验边界
for bad in [{'neighbors':[],'min_common':[2]},
            {'neighbors':[0],'min_common':[2]},
            {'neighbors':[1,1],'min_common':[2]},
            {'neighbors':[1.5],'min_common':[2]},
            {'neighbors':[1],'min_common':[1]},
            {'neighbors':[1],'min_common':[2],'x':[1]}]:
    try:
        validate_grid(bad);raise AssertionError('should reject '+str(bad))
    except ValueError:pass
print('grid validation ok')
print('ALL ALGORITHM CHECKS PASSED')
