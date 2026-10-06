"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {jobs: [], applications: [], dashboard: [], profile: {}, csrf: "", view: "discover", category: "", busy: false, ready: false, pendingApply: null, jobsRequest: 0, pauseSeconds: 900};
state.account = null;
state.authRole = "candidate";
state.authMode = "login";
state.currentReservation = null;
state.adminRequired = false;
state.adminAuthenticated = false;
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
const dateTime = (timestamp) => new Date(timestamp * 1000).toLocaleString("it-IT", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"});
let toastTimer;

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", error);
  box.hidden = false;
  toastTimer = setTimeout(() => { box.hidden = true; }, 5500);
}

async function api(path, data) {
  const options = {credentials: "same-origin"};
  if (data !== undefined) {
    options.method = "POST";
    options.headers = {"Content-Type": "application/json", "X-CSRF-Token": state.csrf};
    options.body = JSON.stringify(data);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error("Connessione assente. Verifica che il server Skillify sia avviato e riprova."); }
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || "Operazione non riuscita.");
    error.status = response.status;
    throw error;
  }
  return result;
}

function filteredJobs() {
  const query = $("#search").value.toLowerCase().trim();
  const mode = $("#mode-filter").value;
  return state.jobs.filter((job) => (!state.category || job.category === state.category) &&
    (!mode || mode === job.mode) &&
    (!query || [job.title, job.company, job.location, ...job.tags].join(" ").toLowerCase().includes(query)));
}

function renderProfile() {
  $("#profile-label").textContent = state.profile.name ? state.profile.name.split(" ")[0] : "Il tuo profilo";
  $("#avatar").textContent = state.profile.name ? state.profile.name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase() : "TU";
}

function renderDeck() {
  const jobs = filteredJobs();
  $("#available-count").textContent = jobs.length;
  $("#apply-button").disabled = $("#skip-button").disabled = state.busy || !jobs.length || !state.ready;
  $("#swipe-controls").hidden = !jobs.length;
  if (!jobs.length) {
    $("#card-area").innerHTML = `<div class="empty-card"><span aria-hidden="true">✧</span><h2>Una pausa, nuove possibilità.</h2><p>Nessuna offerta disponibile con questi filtri. Le offerte non attivate tornano alla scadenza della prenotazione. Puoi anche rivedere quelle saltate.</p><div class="empty-actions"><button class="text-button" id="clear-filters">Azzera filtri</button><button class="text-button" id="reset-passes">Rivedi offerte saltate →</button></div></div>`;
    return;
  }
  const job = jobs[0];
  $("#card-area").innerHTML = `<article class="job-card" id="current-card" data-job-id="${job.id}" aria-label="${escapeHTML(job.title)} presso ${escapeHTML(job.company)}"><div class="swipe-stamp yes" aria-hidden="true">MI INTERESSA</div><div class="swipe-stamp no" aria-hidden="true">PASSO</div><div class="company-row"><div class="company-icon ${escapeHTML(job.color)}">${escapeHTML(job.initials)}</div><div><div class="company-name">${escapeHTML(job.company)}</div><div class="company-caption">Un’opportunità per il tuo futuro</div></div><span class="category-pill">${escapeHTML(job.category)}</span></div><h2>${escapeHTML(job.title)}</h2><div class="job-meta"><span>⌖ ${escapeHTML(job.location)}</span><span>◷ ${escapeHTML(job.mode)}</span><span>▤ ${escapeHTML(job.contract)}</span></div><div class="tags">${job.tags.map((tag) => `<span class="tag">${escapeHTML(tag)}</span>`).join("")}</div><p class="job-description">${escapeHTML(job.description)}</p><div class="salary-row"><strong>${escapeHTML(job.salary)}</strong><span>Un nuovo passo ti aspetta ↗</span></div></article>`;
  attachDrag($("#current-card"));
}

function pauseLabel() {
  return state.pauseSeconds % 60 === 0 ? `${state.pauseSeconds / 60} minuti` : `${state.pauseSeconds} secondi`;
}

function attachDrag(card) {
  let start = null;
  let delta = 0;
  let vertical = 0;
  const reset = () => {
    card.style.transform = "";
    card.querySelectorAll(".swipe-stamp").forEach((stamp) => { stamp.style.opacity = "0"; });
    start = null;
  };
  card.addEventListener("pointerdown", (event) => {
    if (state.busy || (event.pointerType === "mouse" && event.button !== 0)) return;
    start = {x: event.clientX, y: event.clientY}; delta = vertical = 0;
    card.setPointerCapture(event.pointerId);
  });
  card.addEventListener("pointermove", (event) => {
    if (!start) return;
    delta = event.clientX - start.x; vertical = event.clientY - start.y;
    if (Math.abs(vertical) > Math.abs(delta)) return;
    card.style.transform = `translateX(${delta * 0.55}px) rotate(${delta / 26}deg)`;
    card.querySelector(".yes").style.opacity = delta > 0 ? String(Math.min(delta / 180, 1)) : "0";
    card.querySelector(".no").style.opacity = delta < 0 ? String(Math.min(-delta / 180, 1)) : "0";
  });
  card.addEventListener("pointerup", () => {
    if (!start) return;
    const choice = delta > 0 ? "apply" : "pass";
    const accepted = Math.abs(delta) > 95 && Math.abs(delta) > Math.abs(vertical);
    reset();
    if (accepted) void swipe(choice);
  });
  card.addEventListener("pointercancel", reset);
}

async function loadJobs() {
  const request = ++state.jobsRequest;
  const result = await api("/api/jobs");
  if (request !== state.jobsRequest) return;
  state.jobs = result.jobs;
  if (!state.busy) renderDeck();
}

const labels={active:'Nel feed',reserved:'Nel carrello',confirmed:'Interesse confermato',in_progress:'Test in corso',submitted:'In valutazione',hired:'Candidato selezionato',failed:'Test non superato',expired:'Prenotazione scaduta',cancelled:'Offerta ritirata',closed:'Offerta chiusa'};
const candidate=()=>state.account?.role==='candidate';
const employer=()=>state.account?.role==='employer'||state.adminAuthenticated;
function pill(status){return `<span class="status-pill ${['hired','closed','failed','expired','cancelled'].includes(status)?'closed':status==='active'?'active':'paused'}">${escapeHTML(labels[status]||status)}</span>`;}
function files(list){return list?.length?`<ul class="file-list">${list.map(f=>`<li><a href="/api/files/${f.id}" download>${escapeHTML(f.name)} ↓</a></li>`).join('')}</ul>`:'<p>Nessun allegato.</p>';}
function remaining(r){return `<span class="countdown" data-until="${r.reserved_until}"></span>`;}
function updateTimers(){document.querySelectorAll('[data-until]').forEach(el=>{const n=Math.max(0,Math.ceil(Number(el.dataset.until)-Date.now()/1000));el.textContent=n?`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')} per avviare il test`:'Scaduta · aggiornamento in corso';});}
function renderAccount(){
  $('#login-candidate').hidden=!!state.account||state.adminAuthenticated;
  $('#login-employer').hidden=!!state.account||state.adminAuthenticated;
  $('#account-logout').hidden=!state.account&&!state.adminAuthenticated;
  $('#profile-button').hidden=state.account?.role==='employer'||state.adminAuthenticated;
  $('#admin-logout').hidden=true;
  $('#employer-note').textContent='Il candidato prenota con lo swipe. Senza avvio entro 15 minuti, l’offerta torna nel feed. Un test avviato resta riservato fino alla tua decisione; selezionando il candidato l’offerta si conclude definitivamente.';
  renderProfile();
}
function openAuth(role='candidate',mode='login'){
  state.authRole=role;state.authMode=mode;
  $('#auth-role-label').textContent=role==='candidate'?'ACCESSO CANDIDATO':'ACCESSO AZIENDA';
  $('#auth-title').textContent=mode==='register'?'Crea il tuo account':'Accedi al tuo account';
  $('#auth-name-label').hidden=mode!=='register';$('#auth-form').elements.name.required=mode==='register';
  $('#auth-form').elements.password.autocomplete=mode==='register'?'new-password':'current-password';
  $('#auth-submit').textContent=mode==='register'?'Crea account':'Accedi';
  $('#legacy-admin').hidden=role!=='employer';$('#auth-error').textContent='';
  document.querySelectorAll('[data-auth-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.authMode===mode));
  if(!$('#auth-dialog').open)$('#auth-dialog').showModal();
}
function openProfile(){if(!candidate()){openAuth('candidate');return;}for(const k of ['name','email','skills'])$('#profile-form').elements[k].value=state.profile[k]||'';$('#profile-error').textContent='';$('#profile-dialog').showModal();}
async function loadApplications(){
  state.applications=candidate()?(await api('/api/applications')).applications:[];
  $('#application-count').textContent=state.applications.filter(r=>['reserved','confirmed','in_progress','submitted'].includes(r.status)).length;
  renderApplications();
}
function renderApplications(){
  if(!candidate()){$('#applications-list').innerHTML='<div class="empty-card"><h2>Il tuo spazio candidato.</h2><p>Accedi per ritrovare prenotazioni e test anche da un altro dispositivo.</p><button class="primary-button" data-login="candidate">Accedi candidato</button></div>';return;}
  $('#applications-list').innerHTML=state.applications.length?state.applications.map(r=>`<article class="list-card"><div class="company-row"><strong>${escapeHTML(r.company)}</strong>${pill(r.status)}</div><h2>${escapeHTML(r.title)}</h2><p>Prenotata il ${dateTime(r.created_at)}</p>${['reserved','confirmed'].includes(r.status)?`<p>${remaining(r)}</p><p>La conferma non prolunga il tempo: avvia il test prima della scadenza.</p>`:''}${r.review_note?`<p class="review-note">Esito azienda: ${escapeHTML(r.review_note)}</p>`:''}<div class="row-actions">${r.status==='reserved'?`<button class="primary-button" data-reservation="${r.id}" data-res-action="confirm">Conferma interesse</button>`:r.status==='confirmed'?`<button class="primary-button" data-reservation="${r.id}" data-res-action="start">Avvia test</button>`:['in_progress','submitted','hired','failed'].includes(r.status)?`<button class="primary-button" data-reservation="${r.id}" data-res-action="room">${r.status==='in_progress'?'Apri test':'Vedi test e consegna'}</button>`:'<button class="text-button" data-view="discover">Torna alle offerte →</button>'}</div></article>`).join(''):'<div class="empty-card"><h2>Il prossimo passo è tuo.</h2><p>Fai swipe a destra per prenotare un’offerta.</p><button class="primary-button" data-view="discover">Scopri offerte</button></div>';
  updateTimers();
}
function openTest(r){state.currentReservation=r;$('#test-title').textContent=r.title;
  $('#test-content').innerHTML=`${pill(r.status)}<h3>La prova richiesta</h3><p class="test-brief">${escapeHTML(r.test_brief)}</p><h3>Materiali dell’azienda</h3>${files(r.test_files)}<h3>I tuoi allegati</h3><div id="test-attachments">${files(r.attachments)}</div>${r.status==='in_progress'?`<form id="test-form"><label>Il tuo lavoro<textarea name="response" maxlength="20000" rows="9" placeholder="Scrivi la soluzione, descrivi il lavoro oppure allega i file della consegna…">${escapeHTML(r.response)}</textarea></label><label>Allega file (massimo 5 file da 2 MB ciascuno)<input type="file" id="solution-files" multiple></label><button class="text-button" id="upload-solution" type="button">Carica allegati</button><p class="form-error" id="test-error" role="alert"></p><button class="primary-button" type="submit">Consegna test all’azienda</button></form>`:`<h3>La tua consegna</h3><p class="test-brief">${escapeHTML(r.response||'Consegna tramite allegati.')}</p>${r.review_note?`<p>Esito: ${escapeHTML(r.review_note)}</p>`:''}`}`;
  if(!$('#test-dialog').open)$('#test-dialog').showModal();
}
async function loadDashboard(){
  if(!employer()){$('#dashboard-stats').innerHTML='';$('#employer-list').innerHTML='<div class="empty-card"><h2>Il tuo spazio azienda.</h2><p>Pubblica offerte con test e gestisci soltanto i tuoi candidati.</p><button class="primary-button" data-login="employer">Accedi azienda</button></div>';return;}
  state.dashboard=(await api('/api/dashboard')).jobs;
  $('#dashboard-stats').innerHTML=`<div class="stat-box"><strong>${state.dashboard.filter(j=>j.workflow_status==='active').length}</strong><span>Offerte nel feed</span></div><div class="stat-box"><strong>${state.dashboard.filter(j=>['reserved','confirmed','in_progress','submitted'].includes(j.workflow_status)).length}</strong><span>Offerte riservate</span></div><div class="stat-box"><strong>${state.dashboard.filter(j=>j.workflow_status==='hired').length}</strong><span>Candidati selezionati</span></div>`;
  $('#employer-list').innerHTML=state.dashboard.length?state.dashboard.map(j=>`<article class="list-card"><div class="company-row"><strong>${escapeHTML(j.company)}</strong>${pill(j.workflow_status)}</div><h2>${escapeHTML(j.title)}</h2><p>${escapeHTML(j.description)}</p><details class="candidate-list"><summary>Test e materiali</summary><p class="test-brief">${escapeHTML(j.test_brief)}</p>${files(j.test_files)}${j.workflow_status==='active'?`<label class="attachment-picker">Aggiungi materiali (max 5 file da 2 MB)<input type="file" data-job-upload="${j.id}" multiple></label>`:''}</details>${j.candidates.map(r=>`<details class="candidate-list" ${j.active_reservation_id===r.id?'open':''}><summary>${escapeHTML(r.name)} · ${escapeHTML(labels[r.status]||r.status)}</summary><div class="candidate-row"><p>${escapeHTML(r.email)} · ${escapeHTML(r.skills)}</p><p><strong>Motivazione</strong>${escapeHTML(r.motivation||'In attesa della conferma.')}</p><p>Disponibilità: ${escapeHTML(r.availability||'Non indicata')}</p>${['reserved','confirmed'].includes(r.status)?remaining(r):''}${r.started_at?`<p>Test avviato: ${dateTime(r.started_at)}</p>`:''}${r.submitted_at?`<p>Consegnato: ${dateTime(r.submitted_at)}</p>`:''}<p class="test-brief">${escapeHTML(r.response)}</p>${files(r.attachments)}${j.active_reservation_id===r.id&&j.closed!==2?`<label>Feedback al candidato<textarea maxlength="2000" data-review="${j.id}" rows="3" placeholder="Spiega l’esito o il motivo della riapertura…"></textarea></label><div class="row-actions">${r.status==='submitted'?`<button class="primary-button" data-job-action="select" data-id="${j.id}">Seleziona candidato · chiudi definitivamente</button>`:''}<button class="text-button" data-job-action="reopen" data-id="${j.id}">Test non superato · rimetti annuncio nel feed</button></div>`:''}</div></details>`).join('')}<div class="row-actions">${j.workflow_status==='closed'?`<button class="text-button" data-job-action="reopen" data-id="${j.id}">Rimetti annuncio nel feed</button>`:''}${j.closed===0?`<button class="text-button close-offer" data-job-action="close" data-id="${j.id}">Ritira offerta</button>`:''}</div></article>`).join(''):'<div class="empty-card"><h2>La tua prima opportunità.</h2><p>Pubblica un’offerta e descrivi il test da svolgere.</p></div>';
  updateTimers();
}
async function changeView(view){state.view=view;document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!==`${view}-view`);document.querySelectorAll('.nav-item').forEach(v=>v.classList.toggle('active',v.dataset.view===view));$('#current-page').textContent={discover:'Scopri offerte',applications:'Carrello e test',employer:'Area aziende'}[view];try{if(view==='discover')await loadJobs();else if(view==='applications')await loadApplications();else await loadDashboard();}catch(e){toast(e.message,true);}}
async function swipe(action){if(state.busy||!state.ready)return;const job=filteredJobs()[0];if(!job)return;if(action==='apply'&&!candidate()){openAuth('candidate');return;}state.busy=true;renderDeck();try{const r=await api(`/api/jobs/${job.id}/${action}`,{});state.jobs=state.jobs.filter(j=>j.id!==job.id);if(action==='apply'){await loadApplications();await changeView('applications');toast(r.message);}else{await loadJobs();}}catch(e){toast(e.message,true);if(e.status===409)await loadJobs();}finally{state.busy=false;renderDeck();}}
async function upload(path,list){if(list.length>5)throw new Error('Seleziona al massimo 5 file.');for(const file of list){if(!file.size||file.size>2*1024*1024)throw new Error(`${file.name}: il limite è 2 MB.`);const form=new FormData();form.append('file',file);const response=await fetch(path,{method:'POST',headers:{'X-CSRF-Token':state.csrf},body:form,credentials:'same-origin'});let result;try{result=await response.json();}catch{throw new Error('Caricamento non riuscito. Riprova.');}if(!response.ok)throw new Error(result.error||'Caricamento non riuscito.');}}
async function init(){try{const r=await api('/api/bootstrap');state.csrf=r.csrf;state.account=r.account;state.profile=r.profile;state.adminAuthenticated=r.admin_authenticated;state.pauseSeconds=r.pause_seconds;renderAccount();document.querySelectorAll('.pause-duration').forEach(e=>e.textContent=pauseLabel());await Promise.all([loadJobs(),loadApplications()]);state.ready=true;renderDeck();}catch(e){$('#card-area').innerHTML=`<div class="empty-card"><h2>Connessione da ripristinare.</h2><p>${escapeHTML(e.message)}</p><button class="primary-button" id="retry-init">Riprova</button></div>`;toast(e.message,true);}}
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.dataset.close){document.getElementById(b.dataset.close).close();return;}
  if(b.dataset.view){await changeView(b.dataset.view);return;}
  if(b.dataset.login){openAuth(b.dataset.login);return;}
  if(b.dataset.authMode){openAuth(state.authRole,b.dataset.authMode);return;}
  if(b.dataset.category!==undefined){state.category=b.dataset.category;document.querySelectorAll('[data-category]').forEach(e=>e.classList.toggle('selected',e===b));renderDeck();return;}
  if(b.id==='login-candidate'){openAuth('candidate');return;}if(b.id==='login-employer'){openAuth('employer');return;}
  if(['profile-button','edit-profile'].includes(b.id)){openProfile();return;}
  if(b.id==='legacy-admin'){if(state.account){await api('/api/auth/logout',{});await init();}$('#auth-dialog').close();$('#admin-form').reset();$('#admin-dialog').showModal();return;}
  if(b.id==='new-job'){if(!employer()){openAuth('employer');return;}$('#job-error').textContent='';$('#job-dialog').showModal();return;}
  if(b.id==='clear-filters'){$('#search').value='';$('#mode-filter').value='';state.category='';document.querySelectorAll('[data-category]').forEach(e=>e.classList.toggle('selected',e.dataset.category===''));renderDeck();return;}
  if(b.id==='skip-button'){await swipe('pass');return;}if(b.id==='apply-button'){await swipe('apply');return;}
  if(b.id==='retry-init'){await init();return;}
  b.disabled=true;
  try{
    if(b.id==='account-logout'){await api('/api/auth/logout',{});await init();await changeView('discover');}
    if(b.id==='refresh-button')await loadJobs();
    if(b.id==='reset-passes'){await api('/api/passes/reset',{});await loadJobs();}
    if(b.dataset.reservation){const r=state.applications.find(r=>r.id===Number(b.dataset.reservation));state.currentReservation=r;if(b.dataset.resAction==='confirm'){$('#confirm-form').reset();$('#confirm-error').textContent='';$('#confirm-context').textContent=`${r.title} · ${r.company}. Hai tempo fino alle ${dateTime(r.reserved_until)} per avviare il test.`;$('#confirm-dialog').showModal();}else if(b.dataset.resAction==='start'){await api(`/api/reservations/${r.id}/start`,{});await loadApplications();openTest(state.applications.find(x=>x.id===r.id));}else openTest(r);}
    if(b.id==='upload-solution'){await upload(`/api/reservations/${state.currentReservation.id}/files`,Array.from($('#solution-files').files));await loadApplications();const r=state.applications.find(x=>x.id===state.currentReservation.id);$('#test-attachments').innerHTML=files(r.attachments);$('#solution-files').value='';toast('Allegati caricati. Premi Consegna test per inviarlo in valutazione.');}
    if(b.dataset.jobAction){const note=document.querySelector(`[data-review="${b.dataset.id}"]`)?.value||'';await api(`/api/jobs/${b.dataset.id}/state`,{action:b.dataset.jobAction,note});await loadDashboard();toast('Stato aggiornato.');}
  }catch(e){toast(e.message,true);}finally{b.disabled=false;}
});
document.addEventListener('change',async event=>{if(event.target.dataset.jobUpload){event.target.disabled=true;try{await upload(`/api/jobs/${event.target.dataset.jobUpload}/files`,Array.from(event.target.files));await loadDashboard();toast('Materiali aggiunti.');}catch(e){toast(e.message,true);}finally{event.target.disabled=false;}}});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!['auth-form','admin-form','profile-form','job-form','confirm-form','test-form'].includes(form.id))return;event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;
  const errorId={ 'auth-form':'auth-error','admin-form':'admin-error','profile-form':'profile-error','job-form':'job-error','confirm-form':'confirm-error','test-form':'test-error'}[form.id];$('#'+errorId).textContent='';
  try{
    const data=Object.fromEntries(new FormData(form));
    if(form.id==='auth-form'){await api(`/api/auth/${state.authMode}`,{...data,role:state.authRole});$('#auth-dialog').close();form.reset();await init();await changeView(state.account.role==='employer'?'employer':'discover');}
    if(form.id==='admin-form'){await api('/api/admin/login',data);$('#admin-dialog').close();form.reset();await init();await changeView('employer');}
    if(form.id==='profile-form'){state.profile=(await api('/api/profile',data)).profile;renderProfile();$('#profile-dialog').close();}
    if(form.id==='job-form'){let jid=Number(form.dataset.publishedId||0);if(!jid){delete data.test_files;jid=(await api('/api/jobs',data)).id;form.dataset.publishedId=String(jid);}try{await upload(`/api/jobs/${jid}/files`,Array.from(form.elements.test_files.files));}catch(e){form.elements.test_files.value='';throw new Error(`Offerta pubblicata. ${e.message} Aggiungi i file dal pannello aziende.`);}$('#job-dialog').close();delete form.dataset.publishedId;form.reset();await loadDashboard();toast('Offerta con test pubblicata.');}
    if(form.id==='confirm-form'){await api(`/api/reservations/${state.currentReservation.id}/confirm`,{...data,commitment:form.elements.commitment.checked});$('#confirm-dialog').close();await loadApplications();toast('Interesse confermato. Avvia ora il test prima della scadenza.');}
    if(form.id==='test-form'){if($('#solution-files').files.length){await upload(`/api/reservations/${state.currentReservation.id}/files`,Array.from($('#solution-files').files));$('#solution-files').value='';}await api(`/api/reservations/${state.currentReservation.id}/submit`,{response:data.response});$('#test-dialog').close();await loadApplications();toast('Test consegnato: l’offerta resta riservata durante la valutazione.');}
  }catch(e){$('#'+errorId).textContent=e.message;}finally{button.disabled=false;}
});
$('#search').addEventListener('input',renderDeck);$('#mode-filter').addEventListener('change',renderDeck);
document.addEventListener('keydown',event=>{if(event.repeat||state.view!=='discover'||document.querySelector('dialog[open]')||event.target.closest('input,textarea,select,[contenteditable]'))return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();void swipe(event.key==='ArrowRight'?'apply':'pass');}});
setInterval(updateTimers,1000);
setInterval(async()=>{if(!state.ready||state.busy||document.hidden||document.querySelector('dialog[open]'))return;try{if(state.view==='discover')await loadJobs();else if(state.view==='applications')await loadApplications();else await loadDashboard();}catch{/* Explicit actions display errors. */}},10000);
void init();
