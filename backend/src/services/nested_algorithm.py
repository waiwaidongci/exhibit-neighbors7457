"""嵌套留一选参（nested LOOCV）。

外层留一评分全程隔离：外层留出评分只用于最终一次评估，绝不进入内层。
内层在“去掉该外层评分”的训练集上再做留一，对候选参数网格逐一计算内层 RMSE，
以内层误差选定参数，然后只用选定参数在外层留出评分上评一次。
每一内层折都重新拟合（均值、相似度、邻居、回退值全部基于内层训练评分重算）。
"""
import math
from collections import defaultdict
from .algorithm import fit_ratings, predict

def _loocv_holdouts(users):
    """与原研究同口径：按展品代码排序，每位至少 3 条评分的访客留最后一件。"""
    holdouts=[]
    for uid,items in sorted(users.items()):
        ordered=sorted(items.items())
        if len(ordered)>=3:
            exhibit,rating=ordered[-1]
            holdouts.append({'visitor':uid,'exhibit':exhibit,'rating':rating})
    return holdouts

def _users_without(users,visitor,exhibit):
    out={}
    for uid,items in users.items():
        if uid==visitor:
            reduced={e:v for e,v in items.items() if e!=exhibit}
            if reduced:out[uid]=reduced
        else:
            out[uid]=dict(items)
    return out

def _inner_rmse(users,inner_holdouts,k,min_common):
    """内层留一：每个内层留出评分都从训练矩阵剔除后重新拟合再预测。"""
    evaluations=[]
    for h in inner_holdouts:
        inner_users=_users_without(users,h['visitor'],h['exhibit'])
        pred,neighbors,method=predict(inner_users,h['visitor'],h['exhibit'],k,min_common)
        evaluations.append({**h,'prediction':pred,'method':method,
                            'neighbor_count':len(neighbors),
                            'squared_error':(pred-h['rating'])**2})
    sse=sum(x['squared_error'] for x in evaluations)
    return math.sqrt(sse/len(evaluations)),evaluations

def _candidate_grid(grid):
    pairs=[]
    for k in grid['neighbors']:
        for mc in grid['min_common']:
            pairs.append((k,mc))
    # 确定性并列口径：先按邻居数、再按共同评分门槛升序
    return sorted(pairs)

def compute_nested_fold(records,grid,fold_index,heartbeat=None):
    """计算单个外层折，返回该折完整逐折依据（含全部候选参数的内层误差）。

    heartbeat 为可选回调，每完成一个候选参数组合调用一次，供执行者续租，
    保证长计算期间租约不会被判失联。
    """
    data=[r['values'] for r in records]
    users=fit_ratings(data)
    outer_holdouts=_loocv_holdouts(users)
    if not outer_holdouts:
        raise ValueError('至少一位访客有3条评分才可留一验证')
    if fold_index<0 or fold_index>=len(outer_holdouts):
        raise ValueError('外层折编号越界')
    holdout=outer_holdouts[fold_index]
    # 外层隔离：外层留出评分从训练矩阵剔除，内层全程看不到它
    outer_train_users=_users_without(users,holdout['visitor'],holdout['exhibit'])
    inner_holdouts=_loocv_holdouts(outer_train_users)
    if not inner_holdouts:
        raise ValueError('外层训练集内没有可做内层留一的访客（需有访客至少3条评分）')
    candidates=[]
    for tick,(k,min_common) in enumerate(_candidate_grid(grid),start=1):
        inner_rmse,inner_evals=_inner_rmse(outer_train_users,inner_holdouts,k,min_common)
        candidates.append({'neighbors':k,'min_common':min_common,
                           'inner_rmse':inner_rmse,
                           'inner_holdout_count':len(inner_evals),
                           'inner_evaluations':inner_evals})
        if heartbeat:heartbeat(tick)
    # 以内层误差选参；并列时取邻居数更少、再取门槛更低的确定性组合
    chosen=min(candidates,key=lambda c:(c['inner_rmse'],c['neighbors'],c['min_common']))
    # 选参后仅评一次外层
    pred,neighbors,method=predict(outer_train_users,holdout['visitor'],
                                  holdout['exhibit'],chosen['neighbors'],chosen['min_common'])
    outer_eval={**holdout,'prediction':pred,'method':method,
                'neighbor_count':len(neighbors),'neighbors':neighbors,
                'squared_error':(pred-holdout['rating'])**2,
                'selected_neighbors':chosen['neighbors'],
                'selected_min_common':chosen['min_common']}
    return {'fold_index':fold_index,
            'outer_holdout':holdout,
            'inner_holdout_count':len(inner_holdouts),
            'candidate_results':candidates,
            'chosen_parameters':{'neighbors':chosen['neighbors'],
                                 'min_common':chosen['min_common']},
            'inner_rmse':chosen['inner_rmse'],
            'outer_evaluation':outer_eval}

def outer_holdout_plan(records):
    users=fit_ratings([r['values'] for r in records])
    return _loocv_holdouts(users)

def aggregate(fold_results):
    """全部外层折完成后统一发布：汇总嵌套 RMSE 与逐折选参依据。"""
    folds=sorted(fold_results,key=lambda x:x['fold_index'])
    n=len(folds)
    outer_rmse=math.sqrt(sum(f['outer_evaluation']['squared_error'] for f in folds)/n)
    # 对照：用同一批评分固定各候选参数做普通留一的乐观误差（仅作口径对照，不参与选参）
    selection_counts=defaultdict(int)
    for f in folds:
        key=(f['chosen_parameters']['neighbors'],f['chosen_parameters']['min_common'])
        selection_counts[key]+=1
    return {'nested_loocv_rmse':outer_rmse,
            'outer_fold_count':n,
            'selection_counts':[{'neighbors':k[0],'min_common':k[1],'folds':v}
                                for k,v in sorted(selection_counts.items())],
            'folds':folds}
