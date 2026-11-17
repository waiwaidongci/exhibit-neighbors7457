from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey
from ..config.database import Base
from ..config.settings import CONFIG
class NestedStudy(Base):
    __tablename__=CONFIG['nested_study_table']
    id=Column(Integer,primary_key=True)
    source_id=Column(Integer,ForeignKey(CONFIG['source_table']+'.id'),nullable=False)
    name=Column(String(160),nullable=False)
    # 页面指定的候选参数网格，例如 {"neighbors":[3,5],"min_common":[2,3]}
    grid=Column(JSON,nullable=False)
    snapshot=Column(JSON,nullable=False)
    state=Column(String(20),default='queued')
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
    completed_at=Column(String(40),nullable=True)
