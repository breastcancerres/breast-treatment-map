(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('map-data').textContent);
  const catalog = data.catalog, state = data.state;
  let selected = catalog.drugs.find(x => x.id === 'dato-dxd').id;
  let setting = 'all';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = x => !x ? 'не указана' : (x.length === 7 ? x.split('-').reverse().join('.') : x.slice(0,10).split('-').reverse().join('.'));
  const url = x => {try {const u=new URL(x);return u.protocol==='https:' ? u.href : '#';} catch {return '#';}};
  const link = (href,label) => '<a href="'+esc(url(href))+'" target="_blank" rel="noopener noreferrer">'+esc(label)+'</a>';
  const statusNames = {RECRUITING:'Идёт набор',ACTIVE_NOT_RECRUITING:'Продолжается, набор закрыт',COMPLETED:'Завершено',TERMINATED:'Досрочно прекращено',SUSPENDED:'Приостановлено',WITHDRAWN:'Отменено до включения участников',NOT_YET_RECRUITING:'Набор ещё не начат',ENROLLING_BY_INVITATION:'Набор по приглашению',UNKNOWN:'Статус не подтверждён'};
  const sourceOK = id => state.sources[id] && state.sources[id].status === 'ok';
  const recent = d => d && Date.parse(d) <= Date.parse(state.asOf+'T23:59:59Z') && Date.parse(state.asOf)-Date.parse(d) <= 30*86400000;
  function publications(nct) {
    return Object.values(state.publications || {}).filter(p => p.trialIds.includes(nct)).sort((a,b) => (b.publicationDate||'').localeCompare(a.publicationDate||''));
  }
  function trialHTML(id) {
    const def=catalog.trials.find(t=>t.id===id), t=state.trials[id];
    if (!def) return '';
    let html='<div class="trial"><div class="trial-title"><strong>'+esc(def.name)+'</strong>'+link('https://clinicaltrials.gov/study/'+id,id)+'</div>';
    if (!t) return html+'<p>Данные реестра ещё не получены.</p></div>';
    const pc=t.primaryCompletion||{};
    html+='<dl><dt>Статус реестра</dt><dd>'+esc(statusNames[t.status]||t.status)+'</dd><dt>Первичные данные</dt><dd>'+date(pc.date)+(pc.type==='ESTIMATED'?' · плановая дата':' · фактическая дата')+'</dd><dt>Обновление записи</dt><dd>'+date(t.updatedOn)+'</dd><dt>Результаты в реестре</dt><dd>'+(t.hasResults?'Размещены':'Не размещены')+'</dd></dl>';
    html+='<p>Размещение результатов в реестре не определяет их клинический вывод и не отражает все публикации.</p>';
    if (!sourceOK('ctg:'+id)) html+='<p class="health-warn">Последняя проверка не удалась; сохранены предыдущие данные.</p>';
    const pubs=publications(id);
    if (pubs.length) html+='<details><summary>Связанные записи PubMed · '+pubs.length+'</summary><p>Связь по номеру исследования или ссылке из реестра. Подборка может включать протоколы, обзоры и вторичные анализы; это не перечень результатов исследования.</p><ul class="publications">'+pubs.map(p=>'<li>'+link('https://pubmed.ncbi.nlm.nih.gov/'+p.pmid+'/',p.title||'PubMed '+p.pmid)+' <span>'+esc(p.publicationDate||'')+'</span></li>').join('')+'</ul></details>';
    return html+'</div>';
  }
  function regimenHTML(item) {
    const r=(catalog.regimens||[]).find(x=>x.id===item.regimenId);
    if(!r) return '';
    let html='<details class="regimen-details" data-regimen="'+esc(r.id)+'"><summary><strong>Режим терапии</strong><span>'+esc(r.cadence)+'</span></summary>';
    html+='<div class="regimen-content"><p class="regimen-basis">'+esc(r.basis)+' · сверено '+date(r.reviewedOn)+'</p>';
    if(r.kind==='investigational') html+='<p class="regimen-trial-note">Исследовательская схема для этого показания</p>';
    html+=r.stages.map(stage=>'<section class="regimen-stage"><h5>'+esc(stage.title)+'</h5><table class="regimen-table"><caption class="visually-hidden">'+esc(stage.title)+'</caption><thead><tr><th scope="col">Препарат</th><th scope="col">Доза и путь</th><th scope="col">График</th></tr></thead><tbody>'+stage.agents.map(a=>'<tr><th scope="row">'+esc(a.name)+'</th><td><strong>'+esc(a.dose)+'</strong><span>'+esc(a.route)+'</span></td><td>'+esc(a.schedule)+'</td></tr>').join('')+'</tbody></table></section>').join('');
    html+='<p class="regimen-duration"><strong>Длительность:</strong> '+esc(r.duration)+'</p>';
    if(r.notes&&r.notes.length) html+='<ul class="regimen-notes">'+r.notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul>';
    if(r.comparator) html+='<p class="regimen-comparator"><strong>Группа сравнения:</strong> '+esc(r.comparator)+'</p>';
    html+='<div class="regimen-sources">'+r.sources.map(s=>link(s.url,s.label)).join('<span aria-hidden="true"> · </span>')+'</div>';
    return html+'</div></details>';
  }
  function indicationHTML(item) {
    const approval=state.approvals[item.id];
    const approved=!!approval;
    let html='<article class="indication" data-indication="'+esc(item.id)+'"><div class="indication-top"><span class="tag">'+esc(item.subtype)+'</span><span class="tag">'+(item.setting==='early'?'Ранний РМЖ':'Распространённый РМЖ')+'</span>';
    if (approved && recent(approval.effectiveOn)) html+='<span class="tag tag-new">Новое одобрение</span>';
    if (approval && approval.needsReview) html+='<span class="tag tag-review">Документ изменился · нужна сверка</span>';
    html+='</div><h4>'+esc(item.title)+'</h4><p class="regimen">'+esc(item.regimen)+'</p><p class="summary">'+esc(item.summary)+'</p>';
    if(approved) {
      const s=catalog.approvalSources.find(x=>x.id===approval.sourceId);
      html+='<div class="citation-row"><span>FDA · '+(approval.effectiveOn?date(approval.effectiveOn):'подтверждено '+date(approval.confirmedOn))+'</span>'+link(s.url,s.kind==='label'?'Инструкция FDA':'Решение FDA')+'</div>';
    } else {
      html+='<p class="research-status">Исследуемое применение · регистрация по этому показанию в пилоте не подтверждена</p>';
    }
    html+=regimenHTML(item);
    html+='<details><summary>Исследования и первоисточники</summary>'+item.trialIds.map(trialHTML).join('')+'</details></article>';
    return html;
  }
  function eventsHTML(drugId) {
    const events=Object.values(state.events).filter(e=>e.drugIds.includes(drugId)).sort((a,b)=>(b.effectiveOn||b.observedOn).localeCompare(a.effectiveOn||a.observedOn)).slice(0,15);
    if(!events.length)return '';
    return '<details class="drug-events"><summary>История и новые документы · '+events.length+'</summary><div class="event-list">'+events.map(e=>'<div class="event"><span>'+esc(e.title)+'</span><p>'+date(e.effectiveOn||e.observedOn)+(e.reviewRequired?' · требуется сверка':'')+' · '+link(e.url,'Источник')+'</p></div>').join('')+'</div></details>';
  }
  function render() {
    const drug=catalog.drugs.find(d=>d.id===selected);
    const items=catalog.indications.filter(i=>i.drugId===selected&&(setting==='all'||i.setting===setting));
    const mapped=new Set(catalog.approvalSources.flatMap(s=>s.indicationIds));
    const approved=items.filter(i=>state.approvals[i.id]);
    const research=items.filter(i=>!state.approvals[i.id]&&!mapped.has(i.id));
    const unknown=items.filter(i=>!state.approvals[i.id]&&mapped.has(i.id));
    document.getElementById('drug-list').innerHTML=catalog.drugs.map(d=>'<button class="drug-button" data-drug="'+esc(d.id)+'" aria-pressed="'+(d.id===selected)+'"><strong>'+esc(d.shortName)+'</strong><small>'+esc(d.brand)+' · '+esc(d.classShort)+'</small></button>').join('');
    let html='<div class="drug-header"><h2>'+esc(drug.name)+'</h2><p>'+esc(drug.englishName)+' · '+esc(drug.brand)+' · '+esc(drug.mechanism)+'</p><div class="drug-filters" aria-label="Этап заболевания">'+[['all','Все применения'],['early','Ранний РМЖ'],['advanced','Распространённый']].map(([v,l])=>'<button data-setting="'+v+'" aria-pressed="'+(v===setting)+'">'+l+'</button>').join('')+'</div></div>';
    html+='<div class="levels"><section class="level level-guideline"><div class="level-heading"><h3>Клинические рекомендации</h3><span>Отдельный статус</span></div><p class="empty-note">Версия ESMO пока не сверена. Это не означает отсутствия препарата в рекомендациях.</p></section>';
    html+='<section class="level level-approved"><div class="level-heading"><h3>Зарегистрированное применение</h3><span>FDA, США · '+approved.length+'</span></div><div class="indications">'+(approved.map(indicationHTML).join('')||'<p class="empty-note">Для выбранного этапа в пилоте нет подтверждённых регистраций.</p>')+'</div></section>';
    html+='<section class="level level-research"><div class="level-heading"><h3>III фаза · новые применения</h3><span>'+research.length+'</span></div><div class="indications">'+(research.map(indicationHTML).join('')||'<p class="empty-note">В этой подборке отдельных исследуемых применений нет.</p>')+'</div></section></div>';
    if(unknown.length) html+='<section class="level level-review"><div class="level-heading"><h3>Регистрация требует сверки</h3></div><p class="empty-note">Не удалось подтвердить документы для '+unknown.length+' применений. Они не переведены автоматически в исследовательский слой.</p></section>';
    html+=eventsHTML(selected);
    document.getElementById('drug-content').innerHTML=html;
    document.querySelectorAll('[data-drug]').forEach(b=>b.addEventListener('click',()=>{selected=b.dataset.drug;setting='all';render();}));
    document.querySelectorAll('[data-setting]').forEach(b=>b.addEventListener('click',()=>{setting=b.dataset.setting;render();}));
  }
  const checked=state.lastRun && state.lastRun.finishedAt;
  document.getElementById('snapshot-label').textContent='Сведения проверены: '+date(checked||state.asOf)+' · регистрационные статусы на '+date(state.asOf);
  document.getElementById('automation-label').textContent=data.automationConfigured?'Расписание задано · ежедневная проверка':'Автозапуск ещё не подключён';
  const failed=Object.values(state.sources).filter(s=>s.status!=='ok');
  if(failed.length) document.getElementById('snapshot-label').textContent='Проверка: '+date(checked||state.asOf)+' · часть источников недоступна';
  const age=checked?(Date.now()-Date.parse(checked))/86400000:Infinity;
  if(age>3){document.getElementById('snapshot-label').textContent='Последняя проверка: '+date(checked||state.asOf)+' · требуется обновление';document.getElementById('snapshot-label').classList.add('health-warn');}
  document.getElementById('source-summary').textContent=failed.length?'Есть недоступные источники':'Последний сбор выполнен';
  const groups=[['ClinicalTrials.gov','ctg:'],['FDA · решения и инструкции','fda:'],['PubMed','pubmed:']];
  document.getElementById('source-content').innerHTML='<p class="source-intro">Регистрация подтверждается документом FDA для конкретного показания. Новые и изменённые документы без проверенного правила сопоставления добавляются для сверки. Ошибка загрузки сохраняет ранее подтверждённые сведения. Выгрузка инструкции может отставать от решения FDA.</p><div class="source-grid">'+groups.map(([title,prefix])=>{const ss=Object.entries(state.sources).filter(([k])=>k.startsWith(prefix));const errors=ss.filter(([,v])=>v.status!=='ok');return '<div class="source-block"><strong>'+title+'</strong><p>'+ss.length+' проверок · '+errors.length+' ошибок</p><p>'+esc(errors.length?'Часть сведений сохранена с предыдущей проверки.':'Источники доступны при последнем сборе.')+'</p></div>';}).join('')+'</div>';
  render();
})();
