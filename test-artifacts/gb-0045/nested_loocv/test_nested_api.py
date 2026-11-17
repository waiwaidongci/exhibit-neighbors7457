import os,sys,tempfile,pathlib
BACKEND=pathlib.Path(__file__).resolve().parents[3]/'backend'
sys.path.insert(0,str(BACKEND))
_TMP=tempfile.mkdtemp(prefix='nested-test-')
os.environ['DATABASE_URL']='sqlite:///'+os.path.join(_TMP,'api.db')
os.environ['REDIS_URL']='redis://localhost:6399/0'
os.environ['ARTIFACT_DIR']=os.path.join(_TMP,'art')
os.environ['SEED_DEMO']='false'
os.environ['JWT_SECRET']='test-secret'

import fakeredis
import src.services.runs as r1
import src.services.nested_studies as ns
fake=fakeredis.FakeStrictRedis();r1.queue=fake;ns.queue=fake
from fastapi.testclient import TestClient
from src.main import app
from src.config.database import Session,Base,engine
import src.worker as worker
worker.queue=fake
Base.metadata.create_all(engine)
from src.models.user import User
from src.models.source import Source
from src.models.record import Record
from src.models.nested_study import NestedStudy
from src.models.nested_fold import NestedFold
from src.utils.security import hash_password
from sqlalchemy import select
import random
rng=random.Random(3)
with Session() as db:
    db.add(User(email='admin@x.demo',password_hash=hash_password('pw'),role='admin'))
    db.add(User(email='viewer@x.demo',password_hash=hash_password('pw'),role='viewer'))
    s=Source(key='S1',name='来源',filename='s.csv',description='t');db.add(s);db.flush()
    rid=0
    for u in range(10):
        for e in rng.sample(range(8),rng.randrange(3,7)):
            rid+=1
            db.add(Record(source_id=s.id,reference=f'R{rid:04d}',
                values={'visitor':f'V{u:02d}','exhibit':f'E{e:02d}','rating':max(1,min(5,round(3+rng.gauss(0,.7),1)))}))
    db.commit();sid=s.id
c=TestClient(app)
def login(email):
    r=c.post('/auth/login',json={'email':email,'password':'pw'})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}
H=login('admin@x.demo');HV=login('viewer@x.demo')

# 鉴权
assert c.get('/nested-studies').status_code==401
assert c.post('/nested-studies',headers=HV,json={'source_id':sid,'name':'嵌套','grid':{'neighbors':[3],'min_common':[2]}}).status_code==403

# 非法网格 422
for bad in [{'neighbors':[],'min_common':[2]},{'neighbors':[0],'min_common':[2]},
            {'neighbors':[1.5],'min_common':[2]},{'neighbors':[1],'min_common':[1]},
            {'neighbors':[1,1],'min_common':[2]},{'neighbors':[1],'min_common':[2],'x':[1]},
            {'neighbors':['a'],'min_common':[2]}]:
    r=c.post('/nested-studies',headers=H,json={'source_id':sid,'name':'坏网格','grid':bad})
    assert r.status_code==422,(bad,r.status_code,r.text)
print('1) 鉴权与网格 422 校验通过')

# 建研究
r=c.post('/nested-studies',headers=H,json={'source_id':sid,'name':'嵌套留一研究',
    'grid':{'neighbors':[2,4],'min_common':[2,3]}})
assert r.status_code==200,r.text
study=r.json();stid=study['id'];nf=len(study['folds'])
assert study['state']=='queued' and all(f['state']=='queued' for f in study['folds'])
assert fake.llen(ns.NESTED_QUEUE)==nf

# 列表（不含大字段 snapshot/result/folds）
r=c.get('/nested-studies',headers=H);assert r.status_code==200 and len(r.json())>=1
# 详情含逐折
r=c.get(f'/nested-studies/{stid}',headers=H);assert r.status_code==200 and len(r.json()['folds'])==nf

# worker 消费完成
while True:
    v=fake.blpop(ns.NESTED_QUEUE,timeout=1)
    if not v:break
    msg=v[1] if isinstance(v,tuple) else v
    a,b=msg.decode().split(':');worker.execute_nested(int(a),int(b))
r=c.get(f'/nested-studies/{stid}',headers=H);study=r.json()
assert study['state']=='completed',study['state']
res=study['result']
assert res['outer_fold_count']==nf and len(res['folds'])==nf
# 每折保留全部候选组合依据
for f in res['folds']:
    assert len(f['candidate_results'])==4
    assert f['outer_evaluation']['prediction']
print('2) 研究完成，嵌套 RMSE =',round(res['nested_loocv_rmse'],6))

# 导出两份 CSV
r=c.get(f'/nested-studies/{stid}/export',headers=H)
assert r.status_code==200 and 'text/csv' in r.headers['content-type']
outer_csv=r.text
assert 'fold_index' in outer_csv and outer_csv.count('\n')==nf+1
r2=c.get(f'/nested-studies/{stid}/export-candidates',headers=H)
assert r2.status_code==200
assert r2.text.count('\n')==nf*4+1
# 未完成研究不能导出
r=c.post('/nested-studies',headers=H,json={'source_id':sid,'name':'进行中','grid':{'neighbors':[2],'min_common':[2]}})
stid2=r.json()['id']
assert c.get(f'/nested-studies/{stid2}/export',headers=H).status_code==409
print('3) CSV 导出与未完成 409 通过')

# 失败折重试路由：人为制造失败
fid=r.json()['folds'][0]['id']
import uuid
with Session() as db:
    f=db.get(NestedFold,fid)
    f.state='running';f.lease_token='t';f.worker_id='w';f.attempts=1
    from src.utils.serialization import now
    f.heartbeat_at=now();f.updated_at=now();db.commit()
    ns.fail_fold(db,'w',fid,'t','boom')
r=c.post(f'/nested-studies/{stid2}/folds/{fid}/retry',headers=H)
assert r.status_code==200 and r.json()['state']=='queued',r.text
# 非失败折重试 409
r=c.post(f'/nested-studies/{stid2}/folds/{r.json()["id"]}/retry',headers=H)
# 现在它已 queued，再试应 409
assert r.status_code==409,r.text
# viewer 不能重试
assert c.post(f'/nested-studies/{stid2}/folds/{fid}/retry',headers=HV).status_code==403
# 不存在研究 404
assert c.get('/nested-studies/99999',headers=H).status_code==404
print('4) 重试路由状态码与权限通过')

# 完成第二个研究
while True:
    v=fake.blpop(ns.NESTED_QUEUE,timeout=1)
    if not v:break
    msg=v[1] if isinstance(v,tuple) else v
    a,b=msg.decode().split(':');worker.execute_nested(int(a),int(b))
r=c.get(f'/nested-studies/{stid2}',headers=H)
assert r.json()['state']=='completed'

# 原研究路由不受影响
r=c.post('/runs',headers=H,json={'source_id':sid,'name':'原研究','parameters':{'neighbors':5,'min_common':2}})
assert r.status_code==200,r.text
v=fake.blpop('analysis:queue',timeout=1);worker.execute(int(v[1]))
r=c.get('/runs/'+str(r.json()['id']),headers=H)
assert r.json()['state']=='completed'
print('5) 原研究 /runs 全流程保持可用')
print('ALL API CHECKS PASSED')
