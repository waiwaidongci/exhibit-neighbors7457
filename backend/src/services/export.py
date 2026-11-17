import csv,io
from fastapi import HTTPException
from fastapi.responses import Response
from ..models.note import Note
from ..models.nested_study import NestedStudy
from .runs import get_run
def csv_response(result,filename):
    buf=io.StringIO();fields=result['columns'];w=csv.DictWriter(buf,fieldnames=fields);w.writeheader()
    for r in result['rows']:w.writerow({k:r.get(k,'') for k in fields})
    return Response('﻿'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="'+filename+'"'})
def run_csv(db,id):
    r=get_run(db,id)
    if r.state!='completed':raise HTTPException(409,'结果尚未完成')
    return csv_response(r.result,'run-'+str(id)+'.csv')
def note_csv(db,id):
    n=db.get(Note,id)
    if not n:raise HTTPException(404,'未找到说明')
    if n.state!='approved':raise HTTPException(403,'仅批准后的业务说明可导出')
    return csv_response(n.frozen_result,'approved-'+str(id)+'.csv')
def nested_csv(db,study_id):
    s=db.get(NestedStudy,study_id)
    if not s:raise HTTPException(404,'未找到嵌套留一研究')
    if s.state!='completed' or not s.result:raise HTTPException(409,'研究尚未完成，统一发布后才可导出')
    folds=s.result['folds']
    outer_cols=['fold_index','visitor','exhibit','rating','prediction','squared_error',
                'selected_neighbors','selected_min_common','inner_rmse','method','neighbor_count']
    outer_rows=[]
    for f in folds:
        ev=f['outer_evaluation']
        outer_rows.append({'fold_index':f['fold_index'],'visitor':ev['visitor'],
            'exhibit':ev['exhibit'],'rating':ev['rating'],'prediction':ev['prediction'],
            'squared_error':ev['squared_error'],'selected_neighbors':f['chosen_parameters']['neighbors'],
            'selected_min_common':f['chosen_parameters']['min_common'],'inner_rmse':f['inner_rmse'],
            'method':ev['method'],'neighbor_count':ev['neighbor_count']})
    return csv_response({'columns':outer_cols,'rows':outer_rows},'nested-study-'+str(study_id)+'.csv')
def nested_candidates_csv(db,study_id):
    s=db.get(NestedStudy,study_id)
    if not s:raise HTTPException(404,'未找到嵌套留一研究')
    if s.state!='completed' or not s.result:raise HTTPException(409,'研究尚未完成，统一发布后才可导出')
    cols=['fold_index','neighbors','min_common','inner_rmse','inner_holdout_count','chosen']
    rows=[]
    for f in s.result['folds']:
        chosen=(f['chosen_parameters']['neighbors'],f['chosen_parameters']['min_common'])
        for c in f['candidate_results']:
            rows.append({'fold_index':f['fold_index'],'neighbors':c['neighbors'],
                'min_common':c['min_common'],'inner_rmse':c['inner_rmse'],
                'inner_holdout_count':c['inner_holdout_count'],
                'chosen':'是' if (c['neighbors'],c['min_common'])==chosen else ''})
    return csv_response({'columns':cols,'rows':rows},'nested-candidates-'+str(study_id)+'.csv')
