"""嵌套留一研究编排：多执行者并发、中断续跑、失联接管、统一发布。"""
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select
from redis import Redis
from ..config.settings import REDIS_URL
from ..models.nested_study import NestedStudy
from ..models.nested_fold import NestedFold
from ..utils.serialization import row,now
from .sources import source_or_404,list_records
from .domain_validation import validate_grid
from .nested_algorithm import outer_holdout_plan,aggregate
from .audit import log
queue=Redis.from_url(REDIS_URL)
NESTED_QUEUE='nested:queue'
# 租约心跳超过该秒数未续租即视为执行者失联，其他执行者可接管
LEASE_SECONDS=30
def _parse_ts(value):
    return datetime.fromisoformat(value) if value else None
def _lease_expired(fold,at):
    last=_parse_ts(fold.heartbeat_at)
    return last is None or (at-last).total_seconds()>LEASE_SECONDS
def get_study(db,study_id):
    s=db.get(NestedStudy,study_id)
    if not s:raise HTTPException(404,'未找到嵌套留一研究')
    return s
def get_fold(db,study_id,fold_id):
    s=get_study(db,study_id)
    f=db.get(NestedFold,fold_id)
    if not f or f.study_id!=s.id:raise HTTPException(404,'未找到候选折')
    return s,f
def list_studies(db):
    # 列表仅用于账册与状态轮询，剔除冻结快照与统一发布的大结果
    return [{k:v for k,v in row(s).items() if k not in ('snapshot','result')}
            for s in db.scalars(select(NestedStudy).order_by(NestedStudy.id.desc()))]
def study_detail(db,study_id):
    s=get_study(db,study_id)
    folds=db.scalars(select(NestedFold).where(NestedFold.study_id==s.id).order_by(NestedFold.fold_index)).all()
    payload=row(s)
    payload['folds']=[row(f) for f in folds]
    return payload
def create_study(db,user,data):
    source=source_or_404(db,data.source_id)
    records=list_records(db,source.id)
    if len(records)<3:raise HTTPException(422,'至少录入3条样本后才能执行')
    try:grid=validate_grid(data.grid)
    except ValueError as e:raise HTTPException(422,str(e))
    try:plan=outer_holdout_plan(records)
    except ValueError as e:raise HTTPException(422,str(e))
    if not plan:raise HTTPException(422,'至少一位访客有3条评分才可留一验证')
    snapshot={'source':row(source),'records':records,
              'outer_holdouts':plan}
    study=NestedStudy(source_id=source.id,name=data.name,grid=grid,
                      snapshot=snapshot,state='queued',error='',
                      created_at=now())
    db.add(study);db.flush()
    ts=now()
    for index,_ in enumerate(plan):
        db.add(NestedFold(study_id=study.id,fold_index=index,state='queued',
                          attempts=0,error='',created_at=ts,updated_at=ts))
    log(db,user,'nested.queued',study.id,data.name)
    db.commit()
    db.refresh(study)
    fold_ids=list(db.scalars(select(NestedFold.id).where(NestedFold.study_id==study.id).order_by(NestedFold.fold_index)))
    try:
        for fid in fold_ids:queue.rpush(NESTED_QUEUE,str(study.id)+':'+str(fid))
    except Exception:
        study.state='failed';study.error='队列暂不可用，请重新创建研究';db.commit()
        raise HTTPException(503,study.error)
    return study_detail(db,study.id)
def claim_fold(db,worker_id,fold_id,token):
    """抢占候选折。已完成折不重算；失联折可接管；同次重试不重复计入。

    返回 (fold, acquired)：acquired 表示本次消息真正拿到了执行权。
    健康租约下的重复投递直接确认丢弃，避免重复消息在队列中自我繁殖。
    """
    fold=db.get(NestedFold,fold_id)
    if not fold:return None,False
    if fold.state=='completed':
        # 已落盘折不重算，消息直接确认
        return fold,False
    at=datetime.now(timezone.utc)
    if fold.state=='queued' or fold.state=='failed' or (
        fold.state=='running' and _lease_expired(fold,at)):
        taken_over=fold.state=='running'
        fold.state='running'
        fold.worker_id=worker_id
        fold.lease_token=token
        fold.heartbeat_at=at.isoformat()
        # 每次真正接手（初次领取、失败重试、失联接管）计一次；
        # 已持有租约的重复投递不会走到这里，故同次重试不重复计入
        fold.attempts=(fold.attempts or 0)+1
        fold.updated_at=at.isoformat()
        study=db.get(NestedStudy,fold.study_id)
        if study.state in ('queued','failed'):
            study.state='running'
        if taken_over:
            log(db,worker_id,'nested.takeover',fold.id,
                '接管失联折 study=%s fold=%s'%(fold.study_id,fold.fold_index))
        db.commit();db.refresh(fold)
        return fold,True
    # 健康 running 租约：其他执行者正在处理，本消息为重复投递，确认丢弃
    return fold,False
def heartbeat(db,fold_id,token):
    fold=db.get(NestedFold,fold_id)
    if fold and fold.state=='running' and fold.lease_token==token:
        fold.heartbeat_at=datetime.now(timezone.utc).isoformat()
        fold.updated_at=fold.heartbeat_at
        db.commit();return True
    return False
def complete_fold(db,worker_id,fold_id,token,result):
    """落盘逐折依据。旧执行者迟归不得覆盖接管结果。"""
    fold=db.get(NestedFold,fold_id)
    if not fold:return
    # 已被接管（租约令牌变更）或已完成：迟归结果直接丢弃
    if fold.lease_token!=token or fold.state!='running':
        log(db,worker_id,'nested.stale_drop',fold_id,
            '迟到结果已丢弃 study=%s fold=%s'%(fold.study_id,fold.fold_index))
        return
    fold.state='completed';fold.result=result;fold.error=''
    fold.heartbeat_at=datetime.now(timezone.utc).isoformat()
    fold.updated_at=fold.heartbeat_at
    study=db.get(NestedStudy,fold.study_id)
    log(db,worker_id,'nested.fold_completed',fold.id,
        'study=%s fold=%s'%(study.id,fold.fold_index))
    # 全部外层折完成后统一发布（一次事务，中途任何时刻页面都看不到半成品汇总）
    pending=db.scalars(select(NestedFold).where(
        NestedFold.study_id==study.id,NestedFold.state!='completed')).all()
    if not pending:
        folds=[f.result for f in db.scalars(select(NestedFold).where(
            NestedFold.study_id==study.id).order_by(NestedFold.fold_index))]
        study.result=aggregate(folds)
        study.state='completed';study.completed_at=now()
        log(db,worker_id,'nested.completed',study.id,
            '嵌套 RMSE=%.6f'%study.result['nested_loocv_rmse'])
    db.commit()
def fail_fold(db,worker_id,fold_id,token,message):
    fold=db.get(NestedFold,fold_id)
    if not fold or fold.lease_token!=token or fold.state!='running':return
    fold.state='failed';fold.error=message[:2000]
    fold.heartbeat_at=datetime.now(timezone.utc).isoformat()
    fold.updated_at=fold.heartbeat_at
    study=db.get(NestedStudy,fold.study_id)
    log(db,worker_id,'nested.fold_failed',fold.id,message[:500])
    others=db.scalars(select(NestedFold).where(
        NestedFold.study_id==study.id,NestedFold.state!='completed')).all()
    if others and all(f.state=='failed' for f in others):
        study.state='failed';study.error='全部候选折执行失败，可在页面逐折重试'
        log(db,worker_id,'nested.failed',study.id,study.error)
    db.commit()
def retry_fold(db,user,study_id,fold_id):
    """人工重试失败折；同次重试不重复计入，实际接手时才增加 attempts。"""
    study,fold=get_fold(db,study_id,fold_id)
    if fold.state!='failed':
        raise HTTPException(409,'仅失败折可重试，当前状态：'+fold.state)
    fold.state='queued';fold.worker_id=None;fold.lease_token=None
    fold.heartbeat_at=None;fold.error=''
    fold.updated_at=now()
    if study.state=='failed':study.state='running'
    log(db,user,'nested.retry',fold.id,
        'study=%s fold=%s'%(study.id,fold.fold_index))
    db.commit()
    try:queue.rpush(NESTED_QUEUE,str(study.id)+':'+str(fold.id))
    except Exception:
        fold.state='failed';fold.error='队列暂不可用，请稍后重试';db.commit()
        raise HTTPException(503,fold.error)
    db.refresh(fold)
    return row(fold)
def reclaim_orphans(db,worker_id):
    """扫描失联折并重新投递，使任意在线执行者都能接管。"""
    at=datetime.now(timezone.utc)
    candidates=db.scalars(select(NestedFold).where(NestedFold.state=='running')).all()
    reclaimed=[]
    for fold in candidates:
        if _lease_expired(fold,at):
            fold.state='queued';fold.worker_id=None;fold.lease_token=None
            fold.heartbeat_at=None;fold.updated_at=at.isoformat()
            log(db,worker_id,'nested.orphan_requeue',fold.id,
                'study=%s fold=%s'%(fold.study_id,fold.fold_index))
            reclaimed.append(fold)
    if reclaimed:db.commit()
    # 只重投本次确认失联的折，避免对正常租约产生重复消息
    for fold in reclaimed:
        queue.rpush(NESTED_QUEUE,str(fold.study_id)+':'+str(fold.id))
    return len(reclaimed)
