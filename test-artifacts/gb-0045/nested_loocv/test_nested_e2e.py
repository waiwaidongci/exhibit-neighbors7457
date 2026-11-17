import os,sys,time,threading,random,tempfile,pathlib
BACKEND=pathlib.Path(__file__).resolve().parents[3]/'backend'
sys.path.insert(0,str(BACKEND))
_TMP=tempfile.mkdtemp(prefix='nested-test-')
os.environ['DATABASE_URL']='sqlite:///'+os.path.join(_TMP,'e2e.db')
os.environ['REDIS_URL']='redis://localhost:6399/0'
os.environ['ARTIFACT_DIR']=os.path.join(_TMP,'art')
os.environ['SEED_DEMO']='false'

import fakeredis
import src.config.database as database
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
engine=create_engine(os.environ['DATABASE_URL'].replace('sqlite:///','sqlite:///'),connect_args={'check_same_thread':False})
database.engine=engine
database.Session=sessionmaker(bind=engine,expire_on_commit=False)
import src.services.runs as runs_svc
import src.services.nested_studies as ns
fake=fakeredis.FakeStrictRedis()
runs_svc.queue=fake
ns.queue=fake
import src.worker as worker
worker.queue=fake
from src.config.database import Base,Session
import src.models
from src.models.source import Source
from src.models.record import Record
from src.models.user import User
from src.models.nested_study import NestedStudy
from src.models.nested_fold import NestedFold
from src.utils.security import hash_password
from src.utils.serialization import now
from sqlalchemy import select
Base.metadata.create_all(engine)

# ---- 造数据 ----
rng=random.Random(11)
with Session() as db:
    db.add(User(email='a@x.demo',password_hash=hash_password('pw'),role='admin'))
    s=Source(key='S1',name='来源1',filename='s1.csv',description='t');db.add(s);db.flush()
    rid=0
    for u in range(12):
        n=rng.randrange(3,7)
        for e in rng.sample(range(8),n):
            rid+=1
            db.add(Record(source_id=s.id,reference=f'R{rid:04d}',
                          values={'visitor':f'V{u:02d}','exhibit':f'E{e:02d}',
                                  'rating':max(1,min(5,round(3+rng.gauss(0,.8),1)))}))
    db.commit();sid=s.id

class U:
    def __init__(self,email):self.email=email
admin=U('a@x.demo')

class Grid:
    def __init__(self,source_id,grid,name='嵌套研究'):
        self.source_id=source_id;self.grid=grid;self.name=name

def consume_target(target_sid,timeout=60):
    """排空队列直到目标研究完成；非目标消息放回队尾。"""
    deadline=time.time()+timeout
    while time.time()<deadline:
        with Session() as db:
            if db.get(NestedStudy,target_sid).state=='completed':return
        val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
        if not val:continue
        msg=val[1] if isinstance(val,tuple) else val
        s,f=msg.decode().split(':')
        if int(s)==target_sid:
            worker.execute_nested(int(s),int(f))
        else:
            fake.rpush(ns.NESTED_QUEUE,msg)
            time.sleep(0.02)
    raise AssertionError('study %s not completed in time'%target_sid)
def pop_msg(queue=ns.NESTED_QUEUE):
    val=fake.blpop(queue,timeout=1)
    if not val:return None
    return (val[1] if isinstance(val,tuple) else val).decode()

with Session() as db:
    study=ns.create_study(db,admin,Grid(sid,{'neighbors':[2,4],'min_common':[2,3]}))
    study_id=study['id']
    nfolds=len(study['folds'])
print('1) study created, folds =',nfolds,
      '| queued messages =',fake.llen(ns.NESTED_QUEUE))

# ---- 多执行者并发消费（3 个 worker 线程）----
results=[]
def run_worker(wid,stop_after):
    worker.WORKER_ID='w'+wid
    consumed=0
    while consumed<stop_after:
        val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
        if not val:break
        s,f=val[1].decode().split(':')
        worker.execute_nested(int(s),int(f));consumed+=1
        results.append((wid,int(f)))
threads=[threading.Thread(target=run_worker,args=(str(i),99)) for i in range(3)]
for t in threads:t.start()
deadline=time.time()+60
while time.time()<deadline:
    with Session() as db:
        st=db.get(NestedStudy,study_id)
        folds=db.scalars(select(NestedFold).where(NestedFold.study_id==study_id)).all()
        completed=sum(1 for f in folds if f.state=='completed')
    if st.state=='completed':break
    time.sleep(0.1)
for t in threads:t.join(timeout=5)
with Session() as db:
    st=db.get(NestedStudy,study_id)
    folds=db.scalars(select(NestedFold).where(NestedFold.study_id==study_id).order_by(NestedFold.fold_index)).all()
    assert st.state=='completed',st.state
    assert all(f.state=='completed' for f in folds)
    assert all(f.attempts==1 for f in folds),[f.attempts for f in folds]
    assert st.result['outer_fold_count']==nfolds
    assert abs(st.result['nested_loocv_rmse']-__import__('math').sqrt(
        sum(f.result['outer_evaluation']['squared_error'] for f in folds)/nfolds))<1e-12
    # 逐折依据保留
    f0=folds[0].result
    assert len(f0['candidate_results'])==4
    assert f0['chosen_parameters'] in [{'neighbors':k,'min_common':m} for k in (2,4) for m in (2,3)]
print('2) 3 workers concurrent -> completed, nested RMSE =',round(st.result['nested_loocv_rmse'],6))

# ---- 中断续跑：已落盘折不重算 ----
with Session() as db:
    study2=ns.create_study(db,admin,Grid(sid,{'neighbors':[3],'min_common':[2]},'续跑研究'))
    sid2=study2['id'];n2=len(study2['folds'])
# 先只消费一半消息
half=n2//2
for _ in range(half):
    val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
    s,f=val[1].decode().split(':')
    worker.execute_nested(int(s),int(f))
with Session() as db:
    folds=db.scalars(select(NestedFold).where(NestedFold.study_id==sid2).order_by(NestedFold.fold_index)).all()
    done_ids={f.id for f in folds if f.state=='completed'}
    assert len(done_ids)==half
# 把剩余队列消息清空，模拟中断后重启；已落盘折保持 completed
fake.delete(ns.NESTED_QUEUE)
# 重新排队剩余折（模拟服务重启后的恢复入口）
with Session() as db:
    for f in db.scalars(select(NestedFold).where(NestedFold.study_id==sid2,NestedFold.state!='completed')):
        fake.rpush(ns.NESTED_QUEUE,f'{sid2}:{f.id}')
while fake.llen(ns.NESTED_QUEUE):
    val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
    s,f=val[1].decode().split(':')
    worker.execute_nested(int(s),int(f))
with Session() as db:
    st=db.get(NestedStudy,sid2)
    folds=db.scalars(select(NestedFold).where(NestedFold.study_id==sid2)).all()
    assert st.state=='completed'
    assert all(f.attempts==1 for f in folds),'已落盘折未被重算'
print('3) 中断续跑：已落盘折 attempts 保持 1，研究最终 completed')

# ---- 失联接管 + 旧执行者迟归不覆盖 ----
with Session() as db:
    study3=ns.create_study(db,admin,Grid(sid,{'neighbors':[2],'min_common':[2]},'接管研究'))
    sid3=study3['id']
# 只保留本研究的消息
kept=[v for v in fake.lrange(ns.NESTED_QUEUE,0,-1) if v.decode().startswith(str(sid3)+':')]
fake.delete(ns.NESTED_QUEUE)
for v in kept:fake.rpush(ns.NESTED_QUEUE,v)
val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
msg=val[1] if isinstance(val,tuple) else val
s,fid=msg.decode().split(':');fid=int(fid)
import uuid
token_old=uuid.uuid4().hex
with Session() as db:
    fold,acq=ns.claim_fold(db,'old-worker',fid,token_old)
    assert acq and fold.state=='running' and fold.worker_id=='old-worker'
    attempts_before=fold.attempts
# 旧执行者心跳停在 31 秒前
from datetime import datetime,timedelta,timezone
with Session() as db:
    f=db.get(NestedFold,fid)
    f.heartbeat_at=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat();db.commit()
# 新执行者接管
with Session() as db:
    n_reclaimed=ns.reclaim_orphans(db,'new-worker')
    assert n_reclaimed>=1
    fold2,acq=ns.claim_fold(db,'new-worker',fid,'token-new')
    assert acq and fold2.state=='running' and fold2.worker_id=='new-worker'
    assert fold2.attempts==attempts_before+1,'接管计一次'
# 旧执行者迟归：带旧 token 完成，必须被丢弃
with Session() as db:
    stale=ns.complete_fold(db,'old-worker',fid,token_old,{'stale':True})
    f=db.get(NestedFold,fid)
    assert f.state=='running' and f.result is None,'迟归不得覆盖接管结果'
    assert f.lease_token=='token-new'
# 新执行者正常完成
from src.services.nested_algorithm import compute_nested_fold
with Session() as db:
    st3=db.get(NestedStudy,sid3);grid=st3.grid;snap=st3.snapshot
res=compute_nested_fold(snap['records'],grid,fold2.fold_index)
with Session() as db:
    ns.complete_fold(db,'new-worker',fid,'token-new',res)
# 旧 token 的失败回报同样被忽略
with Session() as db:
    ns.fail_fold(db,'old-worker',fid,token_old,'late failure')
    assert db.get(NestedFold,fid).state=='completed'
# 消费剩余折完成研究（只处理本研究消息，有界退出）
deadline=time.time()+30
while time.time()<deadline:
    with Session() as db:
        if db.get(NestedStudy,sid3).state=='completed':break
    val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
    if not val:continue
    s,f=val[1].decode().split(':')
    if int(s)==sid3:
        worker.execute_nested(int(s),int(f))
    else:
        fake.rpush(ns.NESTED_QUEUE,val[1])
        time.sleep(0.02)
with Session() as db:
    st3=db.get(NestedStudy,sid3)
    assert st3.state=='completed'
    assert st3.result['folds'][fold2.fold_index]==res,'接管者结果生效'
print('4) 失联接管成功；旧执行者迟归完成/失败均被丢弃，接管结果保留')

# ---- 失败折重试，同次重试不重复计入 ----
with Session() as db:
    study4=ns.create_study(db,admin,Grid(sid,{'neighbors':[2],'min_common':[2]},'重试研究'))
    sid4=study4['id']
fake.delete(ns.NESTED_QUEUE)
fid=study4['folds'][0]['id']
fake.rpush(ns.NESTED_QUEUE,f'{sid4}:{fid}')
val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
s,fid=val[1].decode().split(':');fid=int(fid)
tok=uuid.uuid4().hex
with Session() as db:
    ns.claim_fold(db,'w-x',fid,tok)
    ns.fail_fold(db,'w-x',fid,tok,'boom')
    f=db.get(NestedFold,fid);assert f.state=='failed' and f.attempts==1
# 人工重试
with Session() as db:
    row=ns.retry_fold(db,admin,sid4,fid)
    assert row['state']=='queued'
# 队列里只有一条重试消息；模拟同条消息被重复投递两次
fake.rpush(ns.NESTED_QUEUE,f'{sid4}:{fid}')
val1=fake.blpop(ns.NESTED_QUEUE,timeout=1)
s,f=val1[1].decode().split(':')
worker.execute_nested(int(s),int(f))
with Session() as db:
    f=db.get(NestedFold,fid)
    assert f.state=='completed' and f.attempts==2,'重试接手只计一次'
# 第二条重复消息到达时折已完成，不重算不计数
fake.rpush(ns.NESTED_QUEUE,f'{sid4}:{fid}')
val2=fake.blpop(ns.NESTED_QUEUE,timeout=1)
s,f=val2[1].decode().split(':')
worker.execute_nested(int(s),int(f))
with Session() as db:
    f=db.get(NestedFold,fid)
    assert f.state=='completed' and f.attempts==2
# 重新投递本研究其余待算折（此前为隔离测试清空了队列）
with Session() as db:
    for f in db.scalars(select(NestedFold).where(
        NestedFold.study_id==sid4,NestedFold.state!='completed')):
        fake.rpush(ns.NESTED_QUEUE,f'{sid4}:{f.id}')
consume_target(sid4)
with Session() as db:
    assert db.get(NestedStudy,sid4).state=='completed'
print('5) 失败折可重试；同次重试只计一次，重复投递不重算')

# ---- 统一发布：全部完成前 result 始终为空 ----
with Session() as db:
    study5=ns.create_study(db,admin,Grid(sid,{'neighbors':[2],'min_common':[2]},'发布研究'))
    sid5=study5['id'];n5=len(study5['folds'])
fake.delete(ns.NESTED_QUEUE)
for f in study5['folds']:fake.rpush(ns.NESTED_QUEUE,f'{sid5}:{f["id"]}')
consumed=0
while consumed<n5-1:
    val=fake.blpop(ns.NESTED_QUEUE,timeout=1)
    s,f=val[1].decode().split(':')
    if int(s)==sid5:
        worker.execute_nested(int(s),int(f));consumed+=1
    else:
        fake.rpush(ns.NESTED_QUEUE,val[1])
with Session() as db:
    st=db.get(NestedStudy,sid5)
    assert st.result is None and st.state!='completed','未全部完成不得发布'
consume_target(sid5)
with Session() as db:
    st=db.get(NestedStudy,sid5)
    assert st.state=='completed' and st.result is not None
print('6) 全部外层折完成后才统一发布结果')

# ---- 原研究（recommendation_runs）不受影响，仍可走原队列 ----
from src.models.run import Run
from src.services import runs as runs_mod
class RunInput:
    def __init__(self):self.source_id=sid;self.name='原研究';self.parameters={'neighbors':5,'min_common':2}
with Session() as db:
    rr=runs_mod.create_run(db,admin,RunInput());rid2=rr['id']
val=fake.blpop('analysis:queue',timeout=1)
worker.execute(int(val[1]))
with Session() as db:
    r=db.get(Run,rid2)
    assert r.state=='completed' and r.result['checksum']
print('7) 原研究保留：普通留一任务仍正常完成')
print('ALL E2E CHECKS PASSED')
