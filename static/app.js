"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {jobs: [], applications: [], dashboard: [], profile: {}, csrf: "", view: "discover", category: "", busy: false, ready: false, pendingApply: null, jobsRequest: 0, pauseSeconds: 900};
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
    $("#card-area").innerHTML = `<div class="empty-card"><span aria-hidden="true">✧</span><h2>Una pausa, nuove possibilità.</h2><p>Nessuna offerta disponibile con questi filtri. Le offerte in pausa tornano dopo ${escapeHTML(pauseLabel())}; puoi anche rivedere quelle saltate.</p><div class="empty-actions"><button class="text-button" id="clear-filters">Azzera filtri</button><button class="text-button" id="reset-passes">Rivedi offerte saltate →</button></div></div>`;
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

async function loadApplications() {
  state.applications = (await api("/api/applications")).applications;
  $("#application-count").textContent = state.applications.length;
  renderApplications();
}

function openProfile(pendingJob = null) {
  state.pendingApply = pendingJob;
  const form = $("#profile-form");
  for (const key of ["name", "email", "skills"]) form.elements[key].value = state.profile[key] || "";
  $("#profile-error").textContent = "";
  $("#profile-dialog").showModal();
}

async function swipe(action, selectedId = null) {
  if (state.busy || !state.ready) return;
  const job = selectedId ? state.jobs.find((item) => item.id === selectedId) : filteredJobs()[0];
  if (!job) return;
  if (action === "apply" && (!state.profile.name || !state.profile.email)) {
    openProfile(job.id);
    return;
  }
  state.busy = true;
  $("#apply-button").disabled = $("#skip-button").disabled = true;
  try {
    await api(`/api/jobs/${job.id}/${action}`, {});
    const card = $("#current-card");
    if (card && Number(card.dataset.jobId) === job.id) {
      card.style.transform = `translateX(${action === "apply" ? 80 : -80}px) rotate(${action === "apply" ? 7 : -7}deg)`;
      card.style.opacity = "0";
      await new Promise((resolve) => setTimeout(resolve, 190));
    }
    if (action === "apply") toast(`Candidatura registrata. L’offerta è in pausa per ${pauseLabel()}.`);
    await Promise.all([loadJobs(), loadApplications()]);
  } catch (error) {
    toast(error.message, true);
    if (error.status === 409) {
      try { await loadJobs(); } catch { /* Keep the actionable original error. */ }
    }
  } finally {
    state.busy = false;
    renderDeck();
  }
}

function statusPill(job) {
  const label = job.status === "closed" ? "Chiusa" : job.status === "paused" ? `In pausa fino alle ${new Date(job.paused_until * 1000).toLocaleTimeString("it-IT", {hour: "2-digit", minute: "2-digit", second: "2-digit"})}` : "Disponibile";
  return `<span class="status-pill ${job.status}">${escapeHTML(label)}</span>`;
}

function renderApplications() {
  $("#applications-list").innerHTML = state.applications.length ? state.applications.map((job) => `<article class="list-card"><div class="company-row"><div class="company-icon ${escapeHTML(job.color)}">${escapeHTML(job.initials)}</div><div class="company-name">${escapeHTML(job.company)}</div><span class="category-pill">Candidatura registrata</span></div><h2>${escapeHTML(job.title)}</h2><p>${escapeHTML(job.location)} · ${escapeHTML(job.mode)} · ${escapeHTML(job.salary)}</p><div class="list-bottom"><p>Candidatura del ${escapeHTML(dateTime(job.applied_at))}</p>${statusPill(job)}</div></article>`).join("") : `<div class="empty-card"><span aria-hidden="true">♡</span><h2>Il primo passo è tutto tuo.</h2><p>Le tue candidature appariranno qui. Scegli un’offerta e fai swipe a destra.</p><button class="primary-button" data-view="discover">Esplora le offerte →</button></div>`;
}

async function loadDashboard() {
  state.dashboard = (await api("/api/dashboard")).jobs;
  const jobs = state.dashboard;
  $("#dashboard-stats").innerHTML = `<div class="stat-box"><strong>${jobs.filter((job) => job.status === "active").length}</strong><span>Offerte disponibili</span></div><div class="stat-box"><strong>${jobs.filter((job) => job.status === "paused").length}</strong><span>Offerte in pausa</span></div><div class="stat-box"><strong>${jobs.reduce((sum, job) => sum + job.candidates.length, 0)}</strong><span>Candidature ricevute</span></div>`;
  $("#employer-list").innerHTML = jobs.map((job) => `<article class="list-card" data-job-id="${job.id}"><div class="company-row"><div class="company-icon ${escapeHTML(job.color)}">${escapeHTML(job.initials)}</div><div class="company-name">${escapeHTML(job.company)}</div>${statusPill(job)}</div><h2>${escapeHTML(job.title)}</h2><p>${escapeHTML(job.location)} · ${escapeHTML(job.mode)} · ${escapeHTML(job.salary)}</p><div class="list-bottom"><p>${escapeHTML(job.category)} · ${escapeHTML(job.contract)}</p><div class="row-actions">${job.status !== "active" ? `<button class="text-button" data-job-action="reopen" data-id="${job.id}">Riapri offerta</button>` : ""}${job.status !== "closed" ? `<button class="text-button close-offer" data-job-action="close" data-id="${job.id}">Chiudi offerta</button>` : ""}</div></div><details class="candidate-list"><summary>${job.candidates.length} candidature ricevute</summary>${job.candidates.length ? job.candidates.map((person) => `<div class="candidate-row"><strong>${escapeHTML(person.name)}</strong><a href="mailto:${escapeHTML(encodeURIComponent(person.email))}">${escapeHTML(person.email)}</a><p>${escapeHTML(person.skills || "Competenze non specificate")}</p><p>${escapeHTML(dateTime(person.created_at))}</p></div>`).join("") : `<p>Nessuna candidatura ricevuta per ora.</p>`}</details></article>`).join("");
}

async function changeView(view) {
  if (!["discover", "applications", "employer"].includes(view)) return;
  state.view = view;
  document.querySelectorAll(".view").forEach((element) => { element.hidden = element.id !== `${view}-view`; });
  document.querySelectorAll(".nav-item").forEach((element) => element.classList.toggle("active", element.dataset.view === view));
  $("#current-page").textContent = {discover: "Scopri offerte", applications: "Le candidature", employer: "Area aziende"}[view];
  try {
    if (view === "employer") await loadDashboard();
    else if (view === "applications") await loadApplications();
    else await loadJobs();
  } catch (error) { toast(error.message, true); }
}

document.addEventListener("click", async (event) => {
  const viewButton = event.target.closest("[data-view]");
  if (viewButton) { void changeView(viewButton.dataset.view); return; }
  const close = event.target.closest("[data-close]");
  if (close) { document.getElementById(close.dataset.close).close(); return; }
  const category = event.target.closest("[data-category]");
  if (category) {
    state.category = category.dataset.category;
    document.querySelectorAll("[data-category]").forEach((button) => button.classList.toggle("selected", button === category));
    renderDeck(); return;
  }
  if (event.target.closest("#clear-filters")) {
    $("#search").value = ""; $("#mode-filter").value = ""; state.category = "";
    document.querySelectorAll("[data-category]").forEach((button) => button.classList.toggle("selected", button.dataset.category === ""));
    renderDeck(); return;
  }
  if (event.target.closest("#reset-passes")) {
    try { await api("/api/passes/reset", {}); await loadJobs(); toast("Puoi rivedere le offerte saltate."); }
    catch (error) { toast(error.message, true); }
    return;
  }
  const jobAction = event.target.closest("[data-job-action]");
  if (jobAction) {
    jobAction.disabled = true;
    try { await api(`/api/jobs/${jobAction.dataset.id}/state`, {action: jobAction.dataset.jobAction}); await Promise.all([loadDashboard(), loadJobs()]); toast("Stato dell’offerta aggiornato."); }
    catch (error) { toast(error.message, true); jobAction.disabled = false; }
  }
});

$("#search").addEventListener("input", renderDeck);
$("#mode-filter").addEventListener("change", renderDeck);
$("#skip-button").addEventListener("click", () => { void swipe("pass"); });
$("#apply-button").addEventListener("click", () => { void swipe("apply"); });
$("#refresh-button").addEventListener("click", async () => {
  try { await loadJobs(); toast("Offerte aggiornate."); } catch (error) { toast(error.message, true); }
});
$("#profile-button").addEventListener("click", () => openProfile());
$("#edit-profile").addEventListener("click", () => openProfile());
$("#profile-dialog").addEventListener("close", () => { state.pendingApply = null; });
$("#new-job").addEventListener("click", () => { $("#job-error").textContent = ""; $("#job-dialog").showModal(); });

$("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('[type="submit"]'); button.disabled = true;
  $("#profile-error").textContent = "";
  try {
    state.profile = (await api("/api/profile", Object.fromEntries(new FormData(event.target)))).profile;
    renderProfile();
    const pending = state.pendingApply;
    state.pendingApply = null;
    $("#profile-dialog").close();
    if (pending) await swipe("apply", pending); else toast("Profilo salvato.");
  } catch (error) { $("#profile-error").textContent = error.message; }
  finally { button.disabled = false; }
});

$("#job-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('[type="submit"]'); button.disabled = true;
  $("#job-error").textContent = "";
  try {
    await api("/api/jobs", Object.fromEntries(new FormData(event.target)));
    $("#job-dialog").close(); event.target.reset();
    await Promise.all([loadDashboard(), loadJobs()]); toast("La tua offerta è stata pubblicata.");
  } catch (error) { $("#job-error").textContent = error.message; }
  finally { button.disabled = false; }
});

document.addEventListener("keydown", (event) => {
  if (event.repeat || state.view !== "discover" || document.querySelector("dialog[open]") || event.target.closest("input,textarea,select,[contenteditable]")) return;
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault(); void swipe(event.key === "ArrowRight" ? "apply" : "pass");
  }
});

async function init() {
  try {
    const result = await api("/api/bootstrap");
    state.csrf = result.csrf; state.profile = result.profile; state.pauseSeconds = result.pause_seconds;
    document.querySelectorAll(".pause-duration").forEach((node) => { node.textContent = pauseLabel(); });
    renderProfile();
    await Promise.all([loadJobs(), loadApplications()]);
    state.ready = true;
    renderDeck();
  } catch (error) {
    $("#card-area").innerHTML = `<div class="empty-card"><h2>Connessione da ripristinare.</h2><p>${escapeHTML(error.message)}</p><button class="primary-button" id="retry-init">Riprova</button></div>`;
    $("#retry-init").addEventListener("click", () => { void init(); });
    $("#swipe-controls").hidden = true;
    toast(error.message, true);
  }
}

setInterval(async () => {
  if (!state.ready || state.busy || document.hidden || document.querySelector("dialog[open]")) return;
  try {
    if (state.view === "discover") await loadJobs();
    else if (state.view === "employer") await loadDashboard();
    else await loadApplications();
  } catch { /* Manual actions display connection errors; avoid background toast loops. */ }
}, 10000);
void init();
