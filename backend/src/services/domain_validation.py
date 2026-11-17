import math
from ..config.settings import CONFIG
def validate_record(values):
    out={}
    for key,label,kind in CONFIG['fields']:
        v=values.get(key)
        if kind=='number':
            if isinstance(v,bool):raise ValueError(label+'必须为数值')
            try:v=float(v)
            except (TypeError,ValueError):raise ValueError(label+'必须为数值')
            if not math.isfinite(v):raise ValueError(label+'必须是有限数值')
        else:
            if not isinstance(v,str) or not v.strip() or len(v)>600:raise ValueError(label+'不能为空且最多600字符')
            v=v.strip()
        out[key]=v
    kind=CONFIG['algorithm']
    if kind=='gaussian' and (out['rms']<0 or out['centroid']<0 or out['kurtosis']<0):raise ValueError('声学特征不可为负')
    if kind=='density' and (not 0<=out['humidity']<=100 or out['particles']<0):raise ValueError('湿度需在0–100且颗粒浓度非负')
    if kind=='transition':
        parts=[x.strip() for x in out['path'].split('>')]
        if any(not x for x in parts) or len(parts)>80:raise ValueError('章节路径需1–80个非空章节，用>分隔')
        out['path']='>'.join(parts)
    if kind=='forest' and (not 0<out['moisture']<=100 or min(out['thickness'],out['temperature'],out['airflow'],out['minutes'])<=0):raise ValueError('湿度需在(0,100]，其余工艺量为正')
    if kind=='neighbors' and not 1<=out['rating']<=5:raise ValueError('评分需在1–5之间')
    return out
def validate_parameters(params):
    defaults=CONFIG['default_parameters'];out={}
    if set(params)-set(defaults):raise ValueError('包含不支持的分析参数')
    for k,v in defaults.items():
        try:n=float(params.get(k,v))
        except (TypeError,ValueError):raise ValueError('参数必须为有限数值：'+k)
        if not math.isfinite(n):raise ValueError('参数必须为有限数值：'+k)
        out[k]=n
    ranges={'folds':(2,8),'smoothing':(1e-12,.01),'eps':(.01,5),'min_samples':(2,100),'min_count':(1,1000),'top_n':(1,100),'trees':(20,200),'depth':(2,16),'neighbors':(1,20),'min_common':(2,10)}
    for k,n in out.items():
        lo,hi=ranges[k]
        if not lo<=n<=hi:raise ValueError(f'{k}需在{lo}至{hi}之间')
        if k not in ('smoothing','eps'):
            if n!=int(n):raise ValueError(k+'必须为整数')
            out[k]=int(n)
    return out
def validate_grid(raw):
    """校验嵌套留一研究的候选参数网格：候选邻居数与共同评分门槛均由页面指定。"""
    if not isinstance(raw,dict):raise ValueError('候选参数必须是对象')
    if set(raw)!={'neighbors','min_common'}:raise ValueError('候选参数只支持 neighbors 与 min_common')
    bounds={'neighbors':(1,20),'min_common':(2,10)}
    grid={}
    for key,(lo,hi) in bounds.items():
        vals=raw.get(key)
        if not isinstance(vals,list) or not vals:raise ValueError(key+'候选至少包含一个值，且必须是数组')
        if len(vals)>12:raise ValueError(key+'候选最多12个值')
        checked=[]
        for v in vals:
            if isinstance(v,bool):raise ValueError(key+'候选必须为整数')
            try:n=float(v)
            except (TypeError,ValueError):raise ValueError(key+'候选必须为数值')
            if not math.isfinite(n) or n!=int(n):raise ValueError(key+'候选必须为整数')
            n=int(n)
            if not lo<=n<=hi:raise ValueError(f'{key}候选需在{lo}至{hi}之间')
            checked.append(n)
        if len(set(checked))!=len(checked):raise ValueError(key+'候选存在重复值')
        grid[key]=sorted(checked)
    if len(grid['neighbors'])*len(grid['min_common'])>100:
        raise ValueError('候选参数组合不可超过100组')
    return grid
