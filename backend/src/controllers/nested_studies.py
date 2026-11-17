from ..services import nested_studies
class NestedStudiesController:
    list=staticmethod(nested_studies.list_studies)
    create=staticmethod(nested_studies.create_study)
    detail=staticmethod(nested_studies.study_detail)
    retry=staticmethod(nested_studies.retry_fold)
