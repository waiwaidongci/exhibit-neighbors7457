from pydantic import BaseModel,Field
class NestedStudyInput(BaseModel):
    source_id:int
    name:str=Field(min_length=2,max_length=160)
    # 页面指定候选邻居数与共同评分门槛，例如 {"neighbors":[3,5],"min_common":[2,3]}
    grid:dict
