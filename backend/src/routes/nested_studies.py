from fastapi import APIRouter,Depends
from ..config.database import db_session
from ..schemas.nested_study import NestedStudyInput
from ..services.auth import current_user,writer
from ..services.export import nested_csv,nested_candidates_csv
from ..controllers.nested_studies import NestedStudiesController as C
router=APIRouter()
@router.get('/nested-studies')
def studies(db=Depends(db_session),u=Depends(current_user)):return C.list(db)
@router.post('/nested-studies')
def create(data:NestedStudyInput,db=Depends(db_session),u=Depends(writer)):return C.create(db,u,data)
@router.get('/nested-studies/{study_id}')
def detail(study_id:int,db=Depends(db_session),u=Depends(current_user)):return C.detail(db,study_id)
@router.post('/nested-studies/{study_id}/folds/{fold_id}/retry')
def retry(study_id:int,fold_id:int,db=Depends(db_session),u=Depends(writer)):return C.retry(db,u,study_id,fold_id)
@router.get('/nested-studies/{study_id}/export')
def export(study_id:int,db=Depends(db_session),u=Depends(current_user)):return nested_csv(db,study_id)
@router.get('/nested-studies/{study_id}/export-candidates')
def export_candidates(study_id:int,db=Depends(db_session),u=Depends(current_user)):return nested_candidates_csv(db,study_id)
