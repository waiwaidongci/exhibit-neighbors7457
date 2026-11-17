import json,time,uuid,logging,os,socket
from redis import Redis
from sqlalchemy import select
from .config.database import Session
from .config.settings import REDIS_URL,ARTIFACT_DIR
from .models.run import Run
from .models.nested_fold import NestedFold
from .models.nested_study import NestedStudy
from .models import source,record,user,note,audit
from .services.algorithm import calculate
from .services.nested_algorithm import compute_nested_fold
from .services import nested_studies
from .services.audit import log
from .services.export import csv_response
queue=Redis.from_url(REDIS_URL)
ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
WORKER_ID='%s-%s-%s'%(socket.gethostname(),os.getpid(),uuid.uuid4().hex[:8])
HEARTBEAT_EVERY=8
ORPHAN_SWEEP_EVERY=15
def execute(id):
    with Session() as db:
        r=db.get(Run,id)
        if not r or r.state!='queued':return
        r.state='running';db.commit()
        try:
            result=calculate(r.snapshot['records'],r.parameters)
            json.dumps(result,allow_nan=False)
            r.result=result;r.state='completed'
            response=csv_response(result,'run.csv')
            (ARTIFACT_DIR/f'run-{r.id}.csv').write_bytes(response.body)
            log(db,'worker','run.completed',r.id)
        except Exception as e:
            logging.exception('analysis failed');r.state='failed';r.error=str(e)[:2000]
            log(db,'worker','run.failed',r.id,r.error)
        db.commit()
def execute_nested(study_id,fold_id):
    token=uuid.uuid4().hex
    with Session() as db:
        fold,acquired=nested_studies.claim_fold(db,WORKER_ID,fold_id,token)
        if not acquired:return
        study=db.get(NestedStudy,study_id)
        if not study:return
        snapshot=study.snapshot;grid=study.grid
    # 已落盘折（completed）在 claim 阶段即返回，不会进入这里重算
    last_beat=time.time()
    def beat(tick):
        nonlocal last_beat
        now=time.time()
        if now-last_beat>=HEARTBEAT_EVERY:
            with Session() as hb:nested_studies.heartbeat(hb,fold_id,token)
            last_beat=now
    try:
        result=compute_nested_fold(snapshot['records'],grid,fold.fold_index,heartbeat=beat)
        json.dumps(result,allow_nan=False)
        with Session() as db:nested_studies.complete_fold(db,WORKER_ID,fold_id,token,result)
    except Exception as e:
        logging.exception('nested fold failed')
        with Session() as db:nested_studies.fail_fold(db,WORKER_ID,fold_id,token,str(e))
def main():
    tick=0
    while True:
        tick+=1
        (ARTIFACT_DIR/'heartbeat').write_text(str(time.time()))
        (ARTIFACT_DIR/('nested-worker-'+WORKER_ID)).write_text(str(time.time()))
        # 两个队列轮流阻塞拉取，多个 worker 进程天然并发消费候选折
        value=queue.blpop('analysis:queue',timeout=1)
        if value:execute(int(value[1]));continue
        value=queue.blpop(nested_studies.NESTED_QUEUE,timeout=1)
        if value:
            try:
                sid,fid=value[1].decode().split(':')
                execute_nested(int(sid),int(fid))
            except ValueError:logging.exception('bad nested queue message %r',value)
            continue
        if tick%ORPHAN_SWEEP_EVERY==0:
            try:
                with Session() as db:nested_studies.reclaim_orphans(db,WORKER_ID)
            except Exception:logging.exception('orphan sweep failed')
if __name__=='__main__':main()
