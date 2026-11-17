import {research} from '../stores/research.js';
import {session} from '../stores/session.js';
import {nestedApi} from '../api/research.js';
import {download} from '../api/client.js';
import {sourceOptions} from '../components/common/business.js';
import {foldStates} from '../constants/enums.js';
import {esc,fmt,localTime} from '../utils/format.js';
import {action,toast,bind} from '../hooks/actions.js';

function foldBadge(state){return '<span class="status '+esc(state)+'">'+esc(foldStates[state]||state)+'</span>'}
function gridText(grid){return '邻居数 ['+grid.neighbors.join(', ')+'] × 共同评分门槛 ['+grid.min_common.join(', ')+']'}

export async function nestedPage(){
  const study=research.nestedId?await nestedApi.get(research.nestedId):null;
  research.activeNested=study;
  const form='<form id="nested-form"><label>分析来源<select name="source_id">'+sourceOptions(research.sources,research.sourceId)+'</select></label>'
    +'<label>研究名称<input name="name" required value="嵌套留一选参研究"></label>'
    +'<fieldset class="grid-fields"><legend>候选参数（逗号分隔，页面指定）</legend>'
    +'<label>候选邻居数 neighbors（1–20）<input name="neighbors" required value="3, 5, 7" pattern="[0-9,\\s]+"></label>'
    +'<label>共同评分门槛 min_common（2–10）<input name="min_common" required value="2, 3" pattern="[0-9,\\s]+"></label></fieldset>'
    +'<p class="fine">外层留一评分全程隔离；内层每折重算均值、邻居与回退值，以内层误差选参后仅评一次外层。候选折由多个执行者并发计算，中断后续跑不重算已落盘折。</p>'
    +'<button type="submit">冻结输入并发起嵌套研究 →</button></form>';
  const ledger='<div class="ledger">'+(research.nestedStudies.length?research.nestedStudies.map(s=>
    '<a href="#nested" data-nested="'+s.id+'"><small>#'+s.id+'</small><strong>'+esc(s.name)+'</strong>'
    +foldBadge(s.state)+'<time title="北京时间 · Asia/Shanghai">'+esc(localTime(s.created_at))+'</time></a>').join('')
    :'<div class="empty">还没有嵌套留一研究。</div>')+'</div>';
  let body='';
  if(study){
    const folds=study.folds||[];
    const counts={queued:0,running:0,completed:0,failed:0};
    folds.forEach(f=>{counts[f.state]=(counts[f.state]||0)+1});
    const result=study.result;
    body='<div class="run-title"><h2>'+esc(study.name)+'</h2>'+foldBadge(study.state)
      +'<small>#'+study.id+' · 来源 #'+study.source_id+' · '+folds.length+' 个外层折</small>'
      +(result?'<button id="nested-export" class="secondary">下载逐折外层 CSV ↗</button><button id="nested-export-candidates" class="secondary">下载候选选参 CSV ↗</button>':'')+'</div>'
      +'<p class="fine">候选网格：'+esc(gridText(study.grid))+' · 冻结 '+study.snapshot.records.length+' 条输入 · '+esc(study.snapshot.source.filename)+'</p>'
      +'<dl class="metrics"><div><dt>待算</dt><dd>'+counts.queued+'</dd></div><div><dt>计算中</dt><dd>'+counts.running+'</dd></div>'
      +'<div><dt>已完成</dt><dd>'+counts.completed+'</dd></div><div><dt>失败</dt><dd>'+counts.failed+'</dd></div></dl>'
      +(study.error?'<div class="failure"><h2>研究未能完成</h2><p>'+esc(study.error)+'</p></div>':'')
      +'<h2>候选折进度 <small>待算 / 计算中 / 已完成；失联折由在线执行者自动接管</small></h2>'
      +foldTable(folds,session.writable)
      +(result?resultsBlock(result):'<div class="pending"><span class="spinner"></span><h2>候选折正在并发计算</h2><p>页面自动刷新；全部外层折完成后统一发布，中途不会产生半成品汇总。</p></div>');
  }
  return '<section class="page-heading"><p class="eyebrow">07 / NESTED LOOCV</p><h1>嵌套留一选参，让误差不再乐观。</h1><p>同批评分既选参又报误差会高估表现；外层留出评分全程不参与内层选参，逐折保留选参依据。原“分析配置/完整结果”研究不受影响、继续保留。</p></section>'
    +'<div class="split config-split"><section class="config-sheet">'+(session.writable?form:'<p>阅读者可以查看研究与逐折依据；新建嵌套研究由分析师或负责人执行。</p>')
    +(study?'<h2>研究列表</h2>'+ledger:'')+'</section><aside>'+(study?body:'<h2>嵌套研究</h2>'+ledger)
    +'<div class="method-note"><b>嵌套口径</b><p>外层：每位至少 3 评分访客按展品代码固定留最后一件，该评分从训练矩阵剔除。</p><p>内层：在外层训练集内再做留一，逐候选参数重算访客均值、相似邻居与展品/全局回退值，取内层 RMSE 最小的参数。</p><p>选参后外层留出评分只评估一次。</p></div></aside></div>';
}

function foldTable(folds,writable){
  const rows=folds.map(f=>{
    const chosen=f.result?('k='+f.result.chosen_parameters.neighbors+' / mc='+f.result.chosen_parameters.min_common):'—';
    const inner=f.result?fmt(f.result.inner_rmse):'—';
    const outer=f.result?fmt(f.result.outer_evaluation.prediction)+' / 实际 '+fmt(f.result.outer_evaluation.rating):'—';
    const retry=(f.state==='failed'&&writable)?'<button class="secondary" data-retry-fold="'+f.id+'">重试该折</button>':'';
    const detail=f.error?esc(f.error):(f.worker_id?esc(f.worker_id)+' · 接手 '+f.attempts+' 次':'');
    return '<tr><td>'+f.fold_index+'</td><td>'+foldBadge(f.state)+'</td><td>'+chosen+'</td><td>'+inner+'</td><td>'+outer+'</td><td><small>'+detail+'</small></td><td>'+retry+'</td></tr>';
  }).join('');
  return '<div class="table-wrap"><table><thead><tr><th>外层折</th><th>状态</th><th>内层选定参数</th><th>内层 RMSE</th><th>外层 预测/实际</th><th>执行者与接手次数</th><th>操作</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}

function resultsBlock(result){
  const counts=result.selection_counts.map(c=>'k='+c.neighbors+' / mc='+c.min_common+'：'+c.folds+' 折').join('；');
  const foldRows=result.folds.map(f=>{
    const ev=f.outer_evaluation;
    return '<tr><td>'+f.fold_index+'</td><td>'+esc(ev.visitor)+'</td><td>'+esc(ev.exhibit)+'</td><td>'+fmt(ev.rating)+'</td><td>'+fmt(ev.prediction)+'</td><td>'+fmt(Math.sqrt(ev.squared_error))+'</td><td>'+f.chosen_parameters.neighbors+'</td><td>'+f.chosen_parameters.min_common+'</td><td>'+fmt(f.inner_rmse)+'</td><td>'+esc(ev.method)+'</td></tr>';
  }).join('');
  const candidateRows=result.folds.flatMap(f=>{
    const chosen=f.chosen_parameters;
    return f.candidate_results.map(c=>'<tr'+((c.neighbors===chosen.neighbors&&c.min_common===chosen.min_common)?' class="chosen-row"':'')+'><td>'+f.fold_index+'</td><td>'+c.neighbors+'</td><td>'+c.min_common+'</td><td>'+fmt(c.inner_rmse)+'</td><td>'+c.inner_holdout_count+'</td><td>'+((c.neighbors===chosen.neighbors&&c.min_common===chosen.min_common)?'✓ 选定':'')+'</td></tr>');
  }).join('');
  return '<h2>统一发布结果 <small>全部 '+result.outer_fold_count+' 个外层折完成后一次发布</small></h2>'
    +'<dl class="metrics"><div><dt>嵌套留一 RMSE</dt><dd>'+fmt(result.nested_loocv_rmse)+'</dd></div><div><dt>外层折数</dt><dd>'+result.outer_fold_count+'</dd></div></dl>'
    +'<p class="fine">逐折选参分布：'+esc(counts)+'</p>'
    +'<h2>逐折外层评估 <small>每折外层留出评分仅评估一次</small></h2>'
    +'<div class="table-wrap"><table><thead><tr><th>折</th><th>访客</th><th>展品</th><th>实际</th><th>预测</th><th>绝对误差</th><th>选定 neighbors</th><th>选定 min_common</th><th>内层 RMSE</th><th>计算依据</th></tr></thead><tbody>'+foldRows+'</tbody></table></div>'
    +'<details><summary>查看全部候选参数的内层选参依据（'+result.folds.length+' 折 × 候选组合）</summary>'
    +'<div class="table-wrap"><table><thead><tr><th>折</th><th>neighbors</th><th>min_common</th><th>内层 RMSE</th><th>内层留出数</th><th>是否选定</th></tr></thead><tbody>'+candidateRows+'</tbody></table></div></details>';
}

export function bindNested(refresh){
  action('#nested-form',async form=>{
    const data=new FormData(form);
    const parse=name=>String(data.get(name)).split(',').map(x=>x.trim()).filter(Boolean).map(Number);
    const grid={neighbors:parse('neighbors'),min_common:parse('min_common')};
    if(grid.neighbors.some(Number.isNaN)||grid.min_common.some(Number.isNaN))throw Error('候选参数必须是逗号分隔的整数');
    const r=await nestedApi.create({source_id:Number(data.get('source_id')),name:data.get('name'),grid});
    research.nestedId=r.id;await refresh();toast('嵌套研究已发起，候选折进入并发队列');
  });
  for(const a of document.querySelectorAll('[data-nested]'))a.addEventListener('click',()=>{research.nestedId=Number(a.dataset.nested)});
  for(const b of document.querySelectorAll('[data-retry-fold]'))b.onclick=async()=>{
    try{await nestedApi.retry(research.nestedId,b.dataset.retryFold);toast('失败折已重新入队');await refresh()}catch(e){toast(e.message,true)}
  };
  bind('#nested-export','click',async()=>{try{await download('/nested-studies/'+research.nestedId+'/export','nested-'+research.nestedId+'.csv')}catch(e){toast(e.message,true)}});
  bind('#nested-export-candidates','click',async()=>{try{await download('/nested-studies/'+research.nestedId+'/export-candidates','nested-candidates-'+research.nestedId+'.csv')}catch(e){toast(e.message,true)}});
}
