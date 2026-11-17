from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey,Index
from ..config.database import Base
from ..config.settings import CONFIG
class NestedFold(Base):
    __tablename__=CONFIG['nested_fold_table']
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey(CONFIG['nested_study_table']+'.id'),nullable=False)
    fold_index=Column(Integer,nullable=False)
    # queued / running / completed / failed / taken_over（接管后由接管执行者继续 running）
    state=Column(String(20),default='queued')
    # 完成一次后逐折依据固定落盘，中断续跑不重算
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    attempts=Column(Integer,default=0)
    # 租约：当前执行者标识与心跳续租时间，失联后可被接管
    worker_id=Column(String(80),nullable=True)
    lease_token=Column(String(80),nullable=True)
    heartbeat_at=Column(String(40),nullable=True)
    created_at=Column(String(40),nullable=False)
    updated_at=Column(String(40),nullable=False)
    __table_args__=(Index('ix_nested_fold_study_index','study_id','fold_index',unique=True),)
