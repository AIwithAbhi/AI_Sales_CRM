let jobId = null;
let timer = null;

async function readApiJson(res) {
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_err) {
      const snippet = text.replace(/\s+/g, ' ').trim().slice(0, 180);
      throw new Error(
        res.ok
          ? `Unexpected server response: ${snippet || '(empty)'}`
          : `Request failed (${res.status}): ${snippet || res.statusText || 'server error'}`,
      );
    }
  }
  if (!res.ok) {
    const detail = data.detail;
    const msg = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
        : (data.error || `Request failed (${res.status})`);
    throw new Error(msg);
  }
  return data;
}

const els = {
  companyName: document.getElementById('companyName'),
  file: document.getElementById('file'),
  btnStart: document.getElementById('btnStart'),
  btnPush: document.getElementById('btnPush'),
  btnDownload: document.getElementById('btnDownload'),
  btnReset: document.getElementById('btnReset'),
  statusBox: document.getElementById('statusBox'),
  progressWrap: document.getElementById('progressWrap'),
  progressBar: document.getElementById('progressBar'),
  progressText: document.getElementById('progressText'),
  resultsCard: document.getElementById('resultsCard'),
  resultsBody: document.getElementById('resultsBody'),
  resultsMeta: document.getElementById('resultsMeta'),
  insightsCard: document.getElementById('insightsCard'),
  insightsGrid: document.getElementById('insightsGrid'),
  insightsCompanyName: document.getElementById('insightsCompanyName'),
  closeInsights: document.getElementById('closeInsights'),
  icpCard: document.getElementById('icpCard'),
  icpContent: document.getElementById('icpContent'),
  recsCard: document.getElementById('recsCard'),
  recsList: document.getElementById('recsList'),
  disambiguationCard: document.getElementById('disambiguationCard'),
  disambiguationLead: document.getElementById('disambiguationLead'),
  disambiguationList: document.getElementById('disambiguationList'),
  scoringProfiles: document.getElementById('scoringProfiles'),
  kpiDone: document.getElementById('kpiDone'),
  kpiHot: document.getElementById('kpiHot'),
  kpiRecs: document.getElementById('kpiRecs'),
  kpiAvg: document.getElementById('kpiAvg'),
};

let scoringProfileCatalog = [];
let defaultScoringProfileIds = ['ai_automation_readiness'];

function selectedScoringProfileIds() {
  if (!els.scoringProfiles) return defaultScoringProfileIds.slice();
  const checked = [...els.scoringProfiles.querySelectorAll('input[type="checkbox"]:checked')]
    .map((el) => el.value)
    .filter(Boolean);
  return checked.length ? checked : defaultScoringProfileIds.slice();
}

function renderScoringProfileSelector() {
  if (!els.scoringProfiles) return;
  const defaults = new Set(defaultScoringProfileIds);
  els.scoringProfiles.innerHTML = scoringProfileCatalog.map((p) => {
    const checked = defaults.has(p.id) ? 'checked' : '';
    return `<label class="scoring-profile-option">
      <input type="checkbox" value="${escapeHtml(p.id)}" ${checked} />
      <span>
        <div class="sp-name">${escapeHtml(p.short_name || p.name)}</div>
        <div class="sp-desc">${escapeHtml(p.description || '')}</div>
      </span>
    </label>`;
  }).join('');
}

async function loadScoringProfiles() {
  try {
    const res = await fetch('/api/scoring-profiles');
    const data = await readApiJson(res);
    scoringProfileCatalog = data.profiles || [];
    defaultScoringProfileIds = data.default_ids || ['ai_automation_readiness'];
    renderScoringProfileSelector();
  } catch (e) {
    console.warn('Could not load scoring profiles', e);
    scoringProfileCatalog = [
      {
        id: 'ai_automation_readiness',
        name: 'AI & Automation Readiness',
        short_name: 'AI Readiness',
        description: 'Default AI maturity / transformation scorecard',
      },
    ];
    renderScoringProfileSelector();
  }
}

function setStatus(msg, kind = 'info') {
  els.statusBox.style.display = 'block';
  els.statusBox.textContent = msg;
  els.statusBox.style.borderColor = kind === 'error' ? '#FECACA' : 'var(--border)';
  els.statusBox.style.background = kind === 'error' ? '#FEF2F2' : 'var(--bg)';
}

function statusPill(status) {
  const s = (status || 'Unknown').toLowerCase();
  if (s === 'hot') return '<span class="pill hot">Hot</span>';
  if (s === 'warm') return '<span class="pill warm">Warm</span>';
  if (s === 'cold') return '<span class="pill cold">Cold</span>';
  if (s === 'review') return '<span class="pill warm">Review</span>';
  return '<span class="pill neutral">Unknown</span>';
}

function confBadge(level) {
  const v = String(level || '').toLowerCase();
  if (!['high', 'medium', 'low'].includes(v)) return '';
  const label = v === 'low' ? 'low confidence' : `${v} conf`;
  return `<span class="conf-badge ${v}">${label}</span>`;
}

function scoreBadge(score) {
  const s = Number(score) || 0;
  let cls = 'score-cold';
  if (s >= 8) cls = 'score-hot';
  else if (s >= 5) cls = 'score-warm';
  return `<span class="score-badge ${cls}">${s}</span>`;
}

function companyInitial(name) {
  return String(name || '?').trim().charAt(0).toUpperCase() || '?';
}

function companyAvatar(url, name) {
  const host = url ? stripUrl(url) : '';
  const initial = companyInitial(name);
  const avatar = host
    ? `<img class="company-logo" src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" /><span class="company-logo-fallback" style="display:none">${initial}</span>`
    : `<span class="company-logo-fallback">${initial}</span>`;
  return `<div class="company-cell">${avatar}<div class="company-meta"><span class="company-name">${escapeHtml(name)}</span></div></div>`;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function stripUrl(u) {
  try { return new URL(u).hostname; } catch { return u; }
}

function renderKPIs(job) {
  const results = job.results || [];
  const processed = job.processed ?? results.length;
  if (els.kpiDone) els.kpiDone.textContent = processed;
  if (els.kpiRecs) els.kpiRecs.textContent = (job.recommendations || []).length;

  let hot = 0, sum = 0, count = 0;
  for (const r of results) {
    if (!r.error) {
      if (r.status_tag === 'Hot') hot++;
      sum += r.lead_score || 0;
      count++;
    }
  }
  if (els.kpiHot) els.kpiHot.textContent = hot;
  if (els.kpiAvg) els.kpiAvg.textContent = count ? (sum / count).toFixed(1) : '0';
}

function renderIcp(job) {
  const icp = job.icp;
  if (!icp) {
    els.icpCard.style.display = 'none';
    return;
  }
  els.icpCard.style.display = 'block';
  const chars = (icp.key_characteristics || []).map(c => `<li>${escapeHtml(c)}</li>`).join('');
  els.icpContent.innerHTML = `
    <p><b>${escapeHtml(icp.icp_summary || '')}</b></p>
    <p class="muted">Industries: ${escapeHtml((icp.target_industries || []).join(', '))}</p>
    <p class="muted">Target size: ${escapeHtml(icp.target_size || '')}</p>
    ${chars ? `<ul>${chars}</ul>` : ''}
  `;
}

function renderRecommendations(job) {
  const recs = job.recommendations || [];
  if (!recs.length) {
    els.recsCard.style.display = 'none';
    return;
  }
  els.recsCard.style.display = 'block';
  els.recsList.innerHTML = recs.map((rec) => {
    const initial = companyInitial(rec.company_name);
    const host = rec.website ? stripUrl(rec.website) : '';
    const logo = host
      ? `<img class="company-logo" src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" /><span class="company-logo-fallback" style="display:none">${initial}</span>`
      : `<span class="company-logo-fallback">${initial}</span>`;
    return `
    <div class="rec-card">
      ${logo}
      <div class="rec-body">
        <div class="rec-head">
          <b>${escapeHtml(rec.company_name)}</b>
          <span class="icp-badge">${rec.similarity_score ?? 0}/10 similar</span>
        </div>
        <a href="${escapeHtml(rec.website)}" target="_blank">${escapeHtml(stripUrl(rec.website) || rec.website)}</a>
        <p class="small">${escapeHtml(rec.industry)} — ${escapeHtml(rec.description)}</p>
        <p class="small"><b>Why:</b> ${escapeHtml(rec.match_reason)}</p>
      </div>
    </div>`;
  }).join('');
}

function matchBadge(r) {
  const conf = (r.match_confidence || 'Low').toString();
  const amb = !!r.match_ambiguous;
  const label = amb ? `${conf} · auto` : conf;
  const cls = conf.toLowerCase() === 'high' ? 'hot' : conf.toLowerCase() === 'medium' ? 'warm' : 'cold';
  const title = escapeHtml(r.match_reason || r.match_domain || '');
  return `<span class="pill ${cls}" title="${title}">${escapeHtml(label)}</span>`;
}

function renderDisambiguation(job) {
  const card = els.disambiguationCard;
  const list = els.disambiguationList;
  if (!card || !list) return;
  if (job.status !== 'needs_disambiguation') {
    card.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  const dis = job.disambiguation || {};
  const cands = dis.candidates || [];
  card.style.display = 'block';
  if (els.disambiguationLead) {
    els.disambiguationLead.textContent =
      `Pick the correct match for “${dis.company_name || 'this company'}” `
      + `(${dis.match_confidence || 'Low'} confidence — ${dis.match_reason || 'multiple candidates'}).`;
  }
  list.innerHTML = cands.map((c, idx) => {
    const domain = escapeHtml(c.domain || stripUrl(c.url) || '');
    const title = escapeHtml(c.title || domain);
    const snip = escapeHtml((c.snippet || '').slice(0, 140));
    const official = c.is_official_domain ? ' <span class="pill hot">Official domain</span>' : '';
    const reach = c.reachable === false ? ' <span class="muted">(may be slow to scrape)</span>' : '';
    return `
      <button type="button" class="disambiguation-option" data-url="${escapeHtml(c.url || '')}" data-idx="${idx}">
        <div class="disambiguation-option-head"><b>${title}</b>${official}</div>
        <div class="mono small">${domain}${reach}</div>
        <div class="muted small">${snip}</div>
      </button>`;
  }).join('');
  list.querySelectorAll('[data-url]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const url = btn.getAttribute('data-url');
      if (!url || !jobId) return;
      btn.disabled = true;
      setStatus('Continuing with selected company…');
      try {
        await readApiJson(await fetch(`/api/jobs/${jobId}/resolve`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        }));
        card.style.display = 'none';
        if (!timer) timer = setInterval(() => poll(jobId), 1200);
        poll(jobId);
      } catch (e) {
        setStatus(e.message || 'Could not resolve match', 'error');
        btn.disabled = false;
      }
    });
  });
}

function renderResults(job) {
  const results = job.results || [];
  els.resultsCard.style.display = 'block';
  const profileIds = job.scoring_profile_ids
    || (results[0] && results[0].scoring_profile_ids)
    || [];
  const profileNote = profileIds.length
    ? ` • profiles: ${profileIds.join(', ')}`
    : '';
  els.resultsMeta.textContent = `${job.total ?? results.length} companies • ${job.processed ?? results.length} processed${profileNote}`;

  els.resultsBody.innerHTML = '';
  for (const r of results) {
    const err = !!r.error;
    const company = r.company_name || '';
    const insightBtn = err
      ? '<button class="btn sm" disabled>—</button>'
      : `<button class="btn ghost sm" data-insight="${encodeURIComponent(company)}">Details</button>`;
    const companyCell = err
      ? `<div class="company-cell"><span class="company-logo-fallback">${companyInitial(company)}</span><div class="company-meta"><span class="company-name">${escapeHtml(company)}</span><span class="company-error">${escapeHtml(r.error)}</span></div></div>`
      : companyAvatar(r.url, company);

    const reviewFlag = (!err && r.review_needed)
      ? ' <span class="pill warm" title="' + escapeHtml((r.validation_errors || []).join('; ')) + '">Review</span>'
      : '';
    const statusCell = err
      ? statusPill('Error')
      : (statusPill(r.status_tag) + reviewFlag);
    const matchCell = err ? '<span class="muted">—</span>' : matchBadge(r);
    const profileHint = err ? '' : profileScoresHint(r);

    els.resultsBody.innerHTML += `
      <tr class="${(!err && r.review_needed) ? 'row-review' : ''}">
        <td>${companyCell}</td>
        <td>${err ? '<span class="muted">—</span>' : `${scoreBadge(r.lead_score)}${confBadge(r.lead_score_confidence)}${profileHint}`}</td>
        <td>${statusCell}</td>
        <td>${matchCell}</td>
        <td>${insightBtn}</td>
      </tr>
    `;
  }

  els.resultsBody.querySelectorAll('[data-insight]').forEach(btn => {
    btn.addEventListener('click', () => {
      openInsights(decodeURIComponent(btn.getAttribute('data-insight') || ''));
    });
  });
}

function profileScoresHint(r) {
  const scores = r.profile_scores;
  if (!scores || typeof scores !== 'object') return '';
  const bits = Object.values(scores).map((block) => {
    const label = block.short_name || block.name || block.id || '';
    const dims = Object.values(block.dimensions || {});
    if (!dims.length) return null;
    const avg = (dims.reduce((s, d) => s + (Number(d.score) || 0), 0) / dims.length).toFixed(0);
    return `${label} ${avg}`;
  }).filter(Boolean);
  if (!bits.length) return '';
  return `<div class="muted small" style="margin-top:4px">${escapeHtml(bits.join(' · '))}</div>`;
}

function openInsights(company) {
  const job = window.__jobState;
  const r = (job?.results || []).find(x => x.company_name === company);
  if (!r) return;

  els.insightsCard.style.display = 'block';
  if (els.insightsCompanyName) {
    els.insightsCompanyName.textContent = r.company_name || company;
  }
  const breakdown = r.qualification_breakdown || {};
  const breakdownHtml = Object.entries(breakdown)
    .map(([k, v]) => `<div class="break-row"><span>${escapeHtml(k)}</span><span>${escapeHtml(v)}</span></div>`)
    .join('');

  const signals = r.buying_signals || [];
  const signalsHtml = signals.length
    ? signals.map((s) => `<span class="pill neutral" style="margin:2px">${escapeHtml(String(s))}</span>`).join(' ')
    : '<span class="muted">None detected</span>';
  const reviewHtml = r.review_needed
    ? `<div class="i-card span3" style="border-color:#F59E0B">
         <div class="i-title">Needs review</div>
         <div class="i-value small">${escapeHtml((r.validation_errors || []).join('; ') || 'Flagged for manual review before Airtable')}</div>
       </div>`
    : '';

  const profileCards = renderProfileScoreCards(r);

  els.insightsGrid.innerHTML = `
    <div class="i-card span2">
      <div class="i-title">Why this score</div>
      <div class="i-value">${escapeHtml(r.score_explanation || r.score_reason || '')}</div>
      <div class="i-value small" style="margin-top:10px">${escapeHtml(r.summary || '')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Lead Score</div>
      <div class="i-value">${r.lead_score ?? 0}/10 ${confBadge(r.lead_score_confidence)}</div>
      <div style="margin-top:10px">${statusPill(r.status_tag)}</div>
    </div>
    ${profileCards}
    <div class="i-card">
      <div class="i-title">Customer Fit</div>
      <div class="i-value">${r.icp_match_score ?? 0}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Confidence</div>
      <div class="i-value">${escapeHtml(r.confidence || 'LOW')}</div>
      <div class="i-value small" style="margin-top:6px">${escapeHtml(r.business_model || '')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Match Confidence</div>
      <div class="i-value">${escapeHtml(r.match_confidence || '—')}</div>
      <div class="i-value small" style="margin-top:6px">${escapeHtml(r.match_domain || r.url || '')}</div>
      <div class="i-value small" style="margin-top:4px">${escapeHtml(r.match_reason || '')}${r.match_ambiguous ? ' (auto-resolved)' : ''}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Size</div>
      <div class="i-value">${escapeHtml(r.size_estimate || '—')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Contact</div>
      <div class="i-value small">
        Email: ${r.email_display && r.email_display !== 'Not Available' ? escapeHtml(r.email_display) : 'Not found'}<br/>
        Phone: ${escapeHtml(r.phone_display || 'Not found')}
      </div>
    </div>
    <div class="i-card span2">
      <div class="i-title">Buying signals</div>
      <div class="i-value small">${signalsHtml}</div>
      ${r.b2b_evidence ? `<div class="i-value small" style="margin-top:8px"><b>B2B evidence:</b> ${escapeHtml(r.b2b_evidence)}</div>` : ''}
    </div>
    <div class="i-card span2">
      <div class="i-title">Qualification</div>
      ${breakdownHtml}
    </div>
    <div class="i-card span3">
      <div class="i-title">Why contact</div>
      <div class="i-value">${escapeHtml(r.contact_reason || '')}</div>
    </div>
    ${reviewHtml}
  `;
}

function renderProfileScoreCards(r) {
  const scores = r.profile_scores;
  if (scores && typeof scores === 'object' && Object.keys(scores).length) {
    return Object.values(scores).map((block) => {
      const name = escapeHtml(block.short_name || block.name || block.id || 'Profile');
      const dims = block.dimensions || {};
      const dimHtml = Object.entries(dims).map(([dimId, vals]) => {
        const label = escapeHtml(vals?.name || dimId.replace(/_/g, ' '));
        const score = vals?.score ?? '—';
        const conf = confBadge(vals?.confidence);
        const reason = escapeHtml(vals?.reason || '');
        return `<div style="margin-top:8px">
          <div class="i-value">${label}: ${score}/10 ${conf}</div>
          <div class="i-value small">${reason}</div>
        </div>`;
      }).join('');
      const tier = block.tier
        ? `<div class="i-value small" style="margin-top:8px">Tier: ${escapeHtml(block.tier)} (avg ${block.avg ?? '—'})</div>`
        : '';
      return `<div class="i-card span2">
        <div class="i-title">${name}</div>
        ${dimHtml || '<div class="i-value small">No dimension scores</div>'}
        ${tier}
      </div>`;
    }).join('');
  }

  // Legacy fallback when profile_scores missing (older jobs)
  return `
    <div class="i-card">
      <div class="i-title">AI Maturity</div>
      <div class="i-value">${r.ai_maturity_score ?? '—'}/10 ${confBadge(r.ai_maturity_confidence)}</div>
      <div class="i-value small" style="margin-top:6px">${escapeHtml(r.ai_maturity_reason || '')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Transform Ready</div>
      <div class="i-value">${r.transformation_readiness_score ?? '—'}/10 ${confBadge(r.transformation_readiness_confidence)}</div>
      <div class="i-value small" style="margin-top:6px">${escapeHtml(r.transformation_readiness_reason || '')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Enterprise Tier</div>
      <div class="i-value">${escapeHtml(r.enterprise_readiness_tier || '—')}</div>
      <div class="i-value small" style="margin-top:6px">Avg ${r.enterprise_readiness_avg ?? '—'}</div>
    </div>
  `;
}

function downloadCsv(job) {
  const rows = job.results || [];
  if (!rows.length) return;
  const headers = [
    'company_name','url','industry','size_estimate','lead_score','lead_score_confidence','status_tag',
    'match_confidence','match_domain','match_ambiguous','match_reason',
    'ai_maturity_score','transformation_readiness_score','enterprise_readiness_tier',
    'scoring_profiles','profile_scores_json',
    'icp_match_score','email','phone','error',
  ];
  const lines = [headers.join(',')];
  for (const r of rows) {
    const vals = headers.map((h) => {
      let v;
      if (h === 'scoring_profiles') v = (r.scoring_profile_ids || []).join('|');
      else if (h === 'profile_scores_json') v = r.profile_scores ? JSON.stringify(r.profile_scores) : '';
      else v = r[h] ?? '';
      return `"${String(v).replace(/"/g, '""')}"`;
    });
    lines.push(vals.join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'searched_companies.csv';
  a.click();
}

function updateSearchFormState() {
  const hasFile = !!els.file?.files?.[0];
  const hasCompany = !!els.companyName?.value.trim();
  if (els.btnStart) {
    els.btnStart.disabled = !(hasFile || hasCompany);
    els.btnStart.title = hasFile || hasCompany
      ? ''
      : 'Enter a company name or upload a CSV';
  }
}

function clearResults() {
  jobId = null;
  if (timer) clearInterval(timer);
  timer = null;
  window.__jobState = null;
  ['resultsCard','insightsCard','icpCard','recsCard','disambiguationCard'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  if (els.disambiguationList) els.disambiguationList.innerHTML = '';
  els.resultsBody.innerHTML = '';
  els.statusBox.style.display = 'none';
  els.progressWrap.style.display = 'none';
  els.progressBar.style.width = '0%';
  els.progressText.textContent = '0%';
  els.btnPush.disabled = true;
  els.btnDownload.disabled = true;
  if (els.kpiDone) els.kpiDone.textContent = '0';
  if (els.kpiHot) els.kpiHot.textContent = '0';
  if (els.kpiRecs) els.kpiRecs.textContent = '0';
  if (els.kpiAvg) els.kpiAvg.textContent = '0';
}

function resetAll() {
  clearResults();
  if (els.companyName) els.companyName.value = '';
  if (els.file) els.file.value = '';
  updateSearchFormState();
}

els.closeInsights.addEventListener('click', () => {
  els.insightsCard.style.display = 'none';
  if (els.insightsCompanyName) els.insightsCompanyName.textContent = '';
});
els.btnReset.addEventListener('click', resetAll);
els.companyName?.addEventListener('input', updateSearchFormState);
els.file?.addEventListener('change', updateSearchFormState);
updateSearchFormState();

els.btnStart.addEventListener('click', async () => {
  const file = els.file?.files?.[0];
  const companiesText = els.companyName?.value.trim() || '';
  if (!file && !companiesText) {
    setStatus('Enter a company name or upload a CSV file.', 'error');
    return;
  }

  clearResults();

  const form = new FormData();
  if (file) form.append('file', file);
  else form.append('companies_text', companiesText);
  form.append('scoring_profiles', selectedScoringProfileIds().join(','));

  els.progressWrap.style.display = 'block';
  els.btnStart.disabled = true;
  els.statusBox.style.display = 'none';

  try {
    const res = await fetch('/api/jobs/search', { method: 'POST', body: form });
    const data = await readApiJson(res);
    jobId = data.job_id;
    timer = setInterval(() => poll(jobId), 1200);
    poll(jobId);
  } catch (e) {
    updateSearchFormState();
    setStatus(e.message || 'Failed', 'error');
    els.progressWrap.style.display = 'none';
  }
});

els.btnPush.addEventListener('click', async () => {
  if (!jobId) return;
  setStatus('Pushing to Airtable...');
  els.btnPush.disabled = true;
  try {
    const res = await fetch(`/api/jobs/${jobId}/push`, { method: 'POST' });
    const data = await readApiJson(res);
    setStatus(`Pushed: ${data.pushed}, Failed: ${data.failed}` +
      (data.skipped_review ? ` (${data.skipped_review} need review)` : ''),
      data.failed ? 'error' : 'info');
  } catch (e) {
    setStatus(e.message || 'Push failed', 'error');
  } finally {
    els.btnPush.disabled = false;
  }
});

els.btnDownload.addEventListener('click', () => {
  if (window.__jobState) downloadCsv(window.__jobState);
});

function stopLeadPoll() {
  if (timer) clearInterval(timer);
  timer = null;
}

async function poll(id) {
  if (!id) return;
  try {
    const res = await fetch(`/api/jobs/${id}`);
    const data = await readApiJson(res);
    window.__jobState = data;
    const prog = Math.round((data.progress || 0) * 100);
    els.progressBar.style.width = prog + '%';
    els.progressText.textContent = prog + '%';
    renderKPIs(data);
    renderDisambiguation(data);

    if ((data.results || []).length > 0) {
      renderResults(data);
      renderIcp(data);
      renderRecommendations(data);
    }

    if (data.status === 'needs_disambiguation') {
      setStatus('Multiple possible matches — pick the correct company to continue.');
      updateSearchFormState();
      stopLeadPoll();
      return;
    }

    if (data.status === 'done') {
      setStatus('✓ Done. Push to Airtable or download CSV.');
      els.btnPush.disabled = false;
      els.btnDownload.disabled = !(data.results || []).length;
      updateSearchFormState();
      stopLeadPoll();
      return;
    }
    if (data.status === 'cancelled') {
      setStatus('Stopped. Partial results kept — download CSV or push to Airtable.');
      els.btnDownload.disabled = !(data.results || []).length;
      updateSearchFormState();
      stopLeadPoll();
      return;
    }
    if (data.status === 'interrupted') {
      setStatus(data.error || 'Search was interrupted. Click Search All to run again.', 'error');
      els.btnDownload.disabled = !(data.results || []).length;
      updateSearchFormState();
      stopLeadPoll();
      return;
    }
    if (data.status === 'failed') {
      setStatus('Job failed: ' + (data.error || 'Unknown'), 'error');
      updateSearchFormState();
      stopLeadPoll();
    }
  } catch (e) {
    setStatus(e.message || 'Polling error', 'error');
    stopLeadPoll();
    jobId = null;
    updateSearchFormState();
  }
}

// --- Tabs ---
const panelSearch = document.getElementById('panelSearch');
const panelAlerts = document.getElementById('panelAlerts');
const panelDashboard = document.getElementById('panelDashboard');
const pageSubtitle = document.getElementById('pageSubtitle');
const pageTitle = document.getElementById('pageTitle');

function selectTab(name) {
  if (panelSearch) panelSearch.style.display = name === 'search' ? '' : 'none';
  if (panelAlerts) panelAlerts.style.display = name === 'alerts' ? '' : 'none';
  if (panelDashboard) panelDashboard.style.display = name === 'dashboard' ? '' : 'none';
  const titles = {
    alerts: 'Industry Updates',
    dashboard: 'Dashboard',
    search: 'Research Companies',
  };
  const subtitles = {
    alerts: 'Monitor news for your prospects',
    dashboard: 'KPIs, funnel, and recent lead activity',
    search: 'Enter a company name or upload a CSV',
  };
  if (pageTitle) pageTitle.textContent = titles[name] || titles.search;
  if (pageSubtitle) pageSubtitle.textContent = subtitles[name] || subtitles.search;
  if (name === 'dashboard') loadDashboard();
}

// --- View router (Home <-> App) ---
const viewHome = document.getElementById('viewHome');
const viewApp = document.getElementById('viewApp');

function showView(view, tab) {
  const isApp = view === 'app';
  viewHome.classList.toggle('active', !isApp);
  viewApp.classList.toggle('active', isApp);
  document.querySelectorAll('.nav-link').forEach((l) => {
    const nav = l.getAttribute('data-nav');
    l.classList.toggle('active', isApp ? nav === tab : nav === 'home');
  });
  if (isApp) selectTab(tab || 'search');
  document.body.classList.toggle('in-app', isApp);
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (!isApp) {
    window.requestAnimationFrame(() => {
      window.resetHomeNavLabel?.();
      window.resetHomeHeroMotion?.();
    });
  }
}

function handleNav(target) {
  if (target === 'home') showView('home');
  else showView('app', target);
}

document.querySelectorAll('[data-nav]').forEach((el) => {
  el.addEventListener('click', () => handleNav(el.getAttribute('data-nav')));
  if (el.getAttribute('role') === 'button') {
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handleNav(el.getAttribute('data-nav'));
      }
    });
  }
});

// Dynamic navbar subtitle + hero motion on home scroll
function initHomeScrollDynamics() {
  const navbar = document.querySelector('.navbar');
  const hero = document.querySelector('.landing-hero');
  const navSub = document.querySelector('.nav-sub');
  if (!navbar || !hero || !navSub) return;

  const navSections = [
    { el: hero, label: 'Upload, qualify, and discover leads' },
    { el: document.querySelector('.story-section'), label: 'Problem, solution, and vision' },
    { el: document.querySelector('.workflow-section'), label: 'How it works' },
    { el: document.querySelector('.features-section'), label: 'Platform features' },
    { el: document.querySelector('.demo-cta'), label: 'Ready for the live demo' },
  ].filter((s) => s.el);

  const defaultLabel = navSections[0]?.label || 'Upload, qualify, and discover leads';
  let lastLabel = '';
  let ticking = false;

  function setNavLabel(label) {
    if (!label || label === lastLabel) return;
    lastLabel = label;
    navSub.classList.add('is-changing');
    window.setTimeout(() => {
      navSub.textContent = label;
      navSub.classList.remove('is-changing');
    }, 120);
  }

  function updateNavSubtitle() {
    if (!viewHome?.classList.contains('active')) return;

    const marker = window.innerHeight * 0.34;
    const navBottom = 72;
    let active = defaultLabel;

    for (const section of navSections) {
      const rect = section.el.getBoundingClientRect();
      if (rect.top <= marker && rect.bottom > navBottom) active = section.label;
    }
    setNavLabel(active);
  }

  function updateScroll() {
    if (!viewHome?.classList.contains('active')) {
      ticking = false;
      return;
    }
    updateNavSubtitle();
    ticking = false;
  }

  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(updateScroll);
  }

  window.resetHomeNavLabel = () => {
    lastLabel = '';
    navSub.classList.remove('is-changing');
    navSub.textContent = defaultLabel;
    lastLabel = defaultLabel;
    requestAnimationFrame(updateNavSubtitle);
  };

  window.resetHomeHeroMotion = () => {
    requestAnimationFrame(updateScroll);
  };

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
  window.resetHomeNavLabel();
  updateScroll();
}

initHomeScrollDynamics();

function initLandingMotion() {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const reveals = document.querySelectorAll('.reveal');
  if (reveals.length && !reduced) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('visible');
        io.unobserve(entry.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -20px 0px' });
    reveals.forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.top < window.innerHeight * 0.92) el.classList.add('visible');
      else io.observe(el);
    });
  } else {
    reveals.forEach((el) => el.classList.add('visible'));
  }

  document.querySelectorAll('[data-count]').forEach((el) => {
    const target = Number(el.getAttribute('data-count'));
    const suffix = el.getAttribute('data-suffix') || '';
    if (!Number.isFinite(target) || reduced) {
      el.textContent = `${target}${suffix}`;
      return;
    }
    const parent = el.closest('li') || el.closest('.story-card');
    const startCount = () => {
      const duration = 900;
      const start = performance.now();
      const tick = (now) => {
        const p = Math.min((now - start) / duration, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        const val = Math.round(target * eased);
        el.textContent = `${val}${suffix}`;
        if (p < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    };
    if (parent && !reduced) {
      const cio = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) {
          startCount();
          cio.disconnect();
        }
      }, { threshold: 0.4 });
      cio.observe(parent);
    } else {
      startCount();
    }
  });
}

initLandingMotion();

// --- Regulatory Alerts ---
let alertJobId = null;
let alertTimer = null;

const alertEls = {
  email: document.getElementById('alertEmail'),
  emailBadge: document.getElementById('alertEmailBadge'),
  company: document.getElementById('alertCompany'),
  file: document.getElementById('alertFile'),
  btnTest: document.getElementById('btnTestEmail'),
  btnSearch: document.getElementById('btnAlertSearch'),
  btnStop: document.getElementById('btnAlertStop'),
  btnDownload: document.getElementById('btnAlertDownload'),
  btnReset: document.getElementById('btnAlertReset'),
  progressLabel: document.getElementById('alertProgressLabel'),
  statusBox: document.getElementById('alertStatusBox'),
  progressWrap: document.getElementById('alertProgressWrap'),
  progressBar: document.getElementById('alertProgressBar'),
  progressText: document.getElementById('alertProgressText'),
  log: document.getElementById('alertLog'),
  resultsCard: document.getElementById('alertResultsCard'),
  resultsBody: document.getElementById('alertResultsBody'),
  resultsMeta: document.getElementById('alertResultsMeta'),
  kpiCompanies: document.getElementById('alertKpiCompanies'),
  kpiUrgent: document.getElementById('alertKpiUrgent'),
  kpiSent: document.getElementById('alertKpiSent'),
  kpiArticles: document.getElementById('alertKpiArticles'),
  salesOppCard: document.getElementById('salesOppCard'),
  salesOppList: document.getElementById('salesOppList'),
  salesOppMeta: document.getElementById('salesOppMeta'),
};

function parseAlertEmails(v) {
  return String(v || '')
    .split(/[,;]/)
    .map((e) => e.trim())
    .filter((e) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e));
}

function isValidEmail(v) {
  return parseAlertEmails(v).length > 0;
}

function updateAlertFormState() {
  const okEmail = isValidEmail(alertEls.email?.value);
  const hasFile = !!alertEls.file?.files?.[0];
  const hasCompany = !!alertEls.company?.value.trim();
  if (alertEls.emailBadge) {
    alertEls.emailBadge.textContent = okEmail ? 'Email valid' : 'Enter email';
    alertEls.emailBadge.className = 'email-badge ' + (okEmail ? 'ok' : 'muted');
  }
  if (alertEls.btnTest) alertEls.btnTest.disabled = !okEmail;
  if (alertEls.btnSearch) {
    const canSearch = okEmail && (hasFile || hasCompany);
    alertEls.btnSearch.disabled = !canSearch;
    alertEls.btnSearch.title = !okEmail
      ? 'Enter your email first'
      : !(hasFile || hasCompany)
        ? 'Type a company name or upload a CSV'
        : '';
  }
}

function setAlertStatus(msg, kind = 'info') {
  alertEls.statusBox.style.display = 'block';
  alertEls.statusBox.textContent = msg;
  alertEls.statusBox.style.borderColor = kind === 'error' ? 'rgba(239,68,68,.5)' : 'rgba(51,65,85,1)';
}

function urgencyPill(u) {
  if (u === 'urgent') return '<span class="pill urgent">High priority</span>';
  return '<span class="pill neutral">Normal</span>';
}

function emailStatusPill(s) {
  const m = {
    sent: ['sent', 'Sent'],
    partial: ['warm', 'Partial'],
    skipped_duplicate: ['skipped', 'Duplicate'],
    skipped_not_urgent: ['skipped', 'Skipped'],
    failed: ['urgent', 'Failed'],
  };
  const [cls, label] = m[s] || ['neutral', s || '—'];
  return `<span class="pill ${cls}">${label}</span>`;
}

function renderAlertKPIs(job) {
  const summary = job.alerts_summary || {};
  if (alertEls.kpiCompanies) alertEls.kpiCompanies.textContent = job.processed ?? 0;
  if (alertEls.kpiUrgent) alertEls.kpiUrgent.textContent = summary.urgent_count ?? 0;
  if (alertEls.kpiSent) alertEls.kpiSent.textContent = summary.emails_sent ?? 0;
  if (alertEls.kpiArticles) alertEls.kpiArticles.textContent = summary.articles_found ?? 0;
}

function renderAlertResults(job) {
  const rows = job.results || [];
  alertEls.resultsCard.style.display = rows.length ? 'block' : 'none';
  alertEls.resultsMeta.textContent = `${job.total ?? 0} companies checked`;

  alertEls.resultsBody.innerHTML = rows.map((r) => `
    <tr>
      <td><b>${escapeHtml(r.company_name)}</b></td>
      <td>
        ${escapeHtml(r.headline || '')}
        ${r.url ? `<div><a href="${escapeHtml(r.url)}" target="_blank">Read article</a></div>` : ''}
      </td>
      <td>${urgencyPill(r.urgency)}</td>
    </tr>
  `).join('');
}

function copyText(text) {
  if (!text) return;
  const write = () => {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(write);
  } else {
    write();
  }
}

function renderSalesOpportunities(job) {
  const opps = (job.sales_opportunities || []).filter((o) => o && o.sales_opportunity);
  const card = alertEls.salesOppCard;
  const list = alertEls.salesOppList;
  if (!card || !list) return;
  if (!opps.length) {
    card.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  card.style.display = 'block';
  if (alertEls.salesOppMeta) alertEls.salesOppMeta.textContent = `${opps.length} draft(s)`;
  window.__salesOpps = opps;
  list.innerHTML = opps.map((o, idx) => {
    const hasEmail = !!(o.email && o.email_publicly_available);
    const emailBlock = hasEmail
      ? `<div><b>Public email:</b> ${escapeHtml(o.email)}</div>
         <div class="muted small">Source: ${escapeHtml(o.email_source_name || '')}
         ${o.email_source_url ? ` · <a href="${escapeHtml(o.email_source_url)}" target="_blank" rel="noopener">Open email source</a>` : ''}</div>
         <div class="muted small">Confidence: ${escapeHtml(String(o.email_confidence || '').toUpperCase())}</div>`
      : `<div class="warn-line">⚠ No publicly verified business email found</div>`;
    return `
      <article class="sales-opp-card" data-opp-idx="${idx}">
        <div class="sales-opp-kicker">Sales opportunity</div>
        <h3>${escapeHtml(o.company_name || '')}</h3>
        <div class="sales-opp-grid">
          <div><b>Regulatory event:</b> ${escapeHtml(o.regulatory_event || '')}</div>
          <div><b>Problem:</b> ${escapeHtml(o.problem || '')}</div>
          <div><b>Business impact:</b> ${escapeHtml(o.business_impact || '')}</div>
          <div><b>Recommended solution:</b> ${escapeHtml(o.solution || '')}</div>
          <div><b>Contact:</b> ${escapeHtml(o.contact_name || o.contact_role || '—')}</div>
          ${emailBlock}
          <div><b>Subject:</b> ${escapeHtml(o.recommended_subject || '')}</div>
        </div>
        <pre class="sales-email-draft">${escapeHtml(o.email_body || '')}</pre>
        <div class="sales-opp-actions">
          <button type="button" class="btn ghost" data-action="copy-email" data-idx="${idx}">Copy Email</button>
          <button type="button" class="btn ghost" data-action="copy-both" data-idx="${idx}">Copy Email + Subject</button>
          ${o.source_url ? `<a class="btn ghost" href="${escapeHtml(o.source_url)}" target="_blank" rel="noopener">Open Source</a>` : ''}
          ${hasEmail && o.email_source_url ? `<a class="btn ghost" href="${escapeHtml(o.email_source_url)}" target="_blank" rel="noopener">Open Email Source</a>` : ''}
          <button type="button" class="btn primary" data-action="push-airtable" data-idx="${idx}">Push to Airtable</button>
        </div>
      </article>
    `;
  }).join('');
}

function summaryText(job) {
  const s = job.alerts_summary || {};
  const opps = s.sales_opportunities ?? (job.sales_opportunities || []).length;
  return `${s.emails_sent ?? 0} emails sent · ${opps} sales draft(s)`;
}

function renderAlertLog(job) {
  const log = job.log || [];
  if (!log.length) {
    alertEls.log.style.display = 'none';
    return;
  }
  alertEls.log.style.display = 'block';
  alertEls.log.innerHTML = log.map((line) => `<div>${escapeHtml(line)}</div>`).join('');
}

function downloadAlertsCsv(job) {
  const rows = job.results || [];
  if (!rows.length) return;
  const headers = ['company_name', 'headline', 'url', 'urgency', 'email_status', 'why_matters', 'talking_points'];
  const lines = [headers.join(',')];
  for (const r of rows) {
    lines.push(headers.map((h) => `"${String(r[h] ?? '').replace(/"/g, '""')}"`).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'regulatory_alerts.csv';
  a.click();
}

function setAlertRunning(running) {
  if (alertEls.btnStop) alertEls.btnStop.style.display = running ? '' : 'none';
  if (alertEls.progressLabel) {
    alertEls.progressLabel.textContent = running
      ? 'Search in progress — click Stop to end immediately'
      : '';
  }
}

function resetAlerts() {
  alertJobId = null;
  if (alertTimer) clearInterval(alertTimer);
  alertTimer = null;
  window.__alertJobState = null;
  alertEls.resultsCard.style.display = 'none';
  alertEls.resultsBody.innerHTML = '';
  if (alertEls.salesOppCard) alertEls.salesOppCard.style.display = 'none';
  if (alertEls.salesOppList) alertEls.salesOppList.innerHTML = '';
  window.__salesOpps = [];
  alertEls.statusBox.style.display = 'none';
  alertEls.progressWrap.style.display = 'none';
  alertEls.log.style.display = 'none';
  alertEls.progressBar.style.width = '0%';
  alertEls.progressText.textContent = '0%';
  alertEls.btnDownload.disabled = true;
  alertEls.btnSearch.disabled = false;
  setAlertRunning(false);
  if (alertEls.company) alertEls.company.value = '';
  if (alertEls.kpiCompanies) alertEls.kpiCompanies.textContent = '0';
  if (alertEls.kpiUrgent) alertEls.kpiUrgent.textContent = '0';
  if (alertEls.kpiSent) alertEls.kpiSent.textContent = '0';
  if (alertEls.kpiArticles) alertEls.kpiArticles.textContent = '0';
  updateAlertFormState();
}

async function pollAlerts(id) {
  if (!id) return;
  try {
    const res = await fetch(`/api/jobs/${id}`);
    const data = await readApiJson(res);

    window.__alertJobState = data;
    const prog = Math.round((data.progress || 0) * 100);
    alertEls.progressBar.style.width = prog + '%';
    alertEls.progressText.textContent = prog + '%';
    renderAlertKPIs(data);
    renderAlertLog(data);
    if ((data.results || []).length) renderAlertResults(data);
    renderSalesOpportunities(data);

    if (data.status === 'done') {
      const s = data.alerts_summary || {};
      const nOpps = s.sales_opportunities ?? (data.sales_opportunities || []).length;
      const nArts = s.articles_found ?? (data.results || []).length;
      const failRows = (data.results || []).filter((r) => r.email_status === 'failed' && r.email_error);
      let msg =
        `Done. ${nArts} article(s) · ${s.emails_sent ?? 0} consolidated alert email(s) sent · ${nOpps} sales draft(s).`;
      if (failRows.length && !(s.emails_sent)) {
        msg += ` Email not sent: ${failRows[0].email_error}`;
        setAlertStatus(msg, 'error');
      } else if (!(s.emails_sent) && nArts === 0) {
        setAlertStatus(msg + ' No news found for this company name — try a clearer legal name.', 'error');
      } else {
        setAlertStatus(msg);
      }
      alertEls.btnDownload.disabled = false;
      alertEls.btnSearch.disabled = false;
      setAlertRunning(false);
      if (alertTimer) clearInterval(alertTimer);
      alertTimer = null;
      return;
    }
    if (data.status === 'cancelled') {
      const s = data.alerts_summary || {};
      const n = data.processed ?? 0;
      setAlertStatus(
        `Stopped. ${n} of ${data.total ?? n} companies processed. ` +
        `${s.emails_sent ?? 0} email(s) sent before stop.`,
      );
      alertEls.btnDownload.disabled = !(data.results || []).length;
      alertEls.btnSearch.disabled = false;
      setAlertRunning(false);
      if (alertEls.btnStop) alertEls.btnStop.disabled = false;
      if (alertTimer) clearInterval(alertTimer);
      alertTimer = null;
      return;
    }
    if (data.status === 'failed') {
      setAlertStatus('Job failed: ' + (data.error || 'Unknown'), 'error');
      alertEls.btnSearch.disabled = false;
      setAlertRunning(false);
      if (alertTimer) clearInterval(alertTimer);
      alertTimer = null;
      return;
    }
    if (data.status === 'interrupted') {
      setAlertStatus(data.error || 'Job interrupted. Please click Search again.', 'error');
      alertEls.btnDownload.disabled = !(data.results || []).length;
      alertEls.btnSearch.disabled = false;
      setAlertRunning(false);
      if (alertTimer) clearInterval(alertTimer);
      alertTimer = null;
      return;
    }
  } catch (e) {
    setAlertStatus(e.message || 'Polling error', 'error');
    if (alertTimer) clearInterval(alertTimer);
    alertTimer = null;
    alertJobId = null;
    alertEls.btnSearch.disabled = false;
    setAlertRunning(false);
  }
}

async function stopAlerts() {
  if (!alertJobId) return;
  if (alertEls.btnStop) alertEls.btnStop.disabled = true;
  setAlertStatus('Stopping — current company will finish, then search ends…');
  try {
    const res = await fetch(`/api/jobs/${alertJobId}/cancel`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Could not stop');
    if (alertTimer) clearInterval(alertTimer);
    alertTimer = null;
    await pollAlerts(alertJobId);
    if (alertEls.btnStop) alertEls.btnStop.disabled = false;
  } catch (e) {
    setAlertStatus(e.message || 'Stop failed', 'error');
    if (alertEls.btnStop) alertEls.btnStop.disabled = false;
  }
}

async function loadResendHint() {
  const hint = document.getElementById('resendHint');
  if (!hint) return;
  try {
    const res = await fetch('/api/alerts/resend-hint');
    const data = await readApiJson(res);
    if (data.show_hint && data.message) {
      hint.style.display = 'block';
      hint.textContent = data.message;
      if (data.allowed_test_recipient && alertEls.email && !alertEls.email.value.trim()) {
        alertEls.email.placeholder = data.allowed_test_recipient;
      }
    }
  } catch { /* ignore */ }
}

if (alertEls.email) {
  loadResendHint();
  alertEls.email.addEventListener('input', updateAlertFormState);
  alertEls.company?.addEventListener('input', updateAlertFormState);
  alertEls.file?.addEventListener('change', updateAlertFormState);

  alertEls.btnTest?.addEventListener('click', async () => {
    const email = alertEls.email.value.trim();
    if (!isValidEmail(email)) return;
    setAlertStatus('Sending test email...');
    alertEls.btnTest.disabled = true;
    try {
      const form = new FormData();
      form.append('recipient_email', email);
      const res = await fetch('/api/alerts/test-email', { method: 'POST', body: form });
      const data = await readApiJson(res);
      const sent = (data.sent_to || []).join(', ');
      const partial = data.partial ? ` Some failed: ${data.message || ''}` : '';
      setAlertStatus(`Test sent to: ${sent || 'inbox'}.${partial}`);
    } catch (e) {
      setAlertStatus(e.message || 'Test failed', 'error');
    } finally {
      updateAlertFormState();
    }
  });

  alertEls.btnSearch.addEventListener('click', async () => {
    const email = alertEls.email.value.trim();
    const file = alertEls.file.files[0];
    const companyText = alertEls.company?.value.trim() || '';
    if (!isValidEmail(email)) {
      setAlertStatus('Enter a valid email address first.', 'error');
      return;
    }
    if (!file && !companyText) {
      setAlertStatus('Type a company name or upload a CSV file.', 'error');
      return;
    }

    resetAlerts();

    const form = new FormData();
    form.append('recipient_email', email);
    if (file) form.append('file', file);
    else form.append('companies_text', companyText);

    alertEls.progressWrap.style.display = 'block';
    alertEls.btnSearch.disabled = true;
    setAlertRunning(true);
    setAlertStatus('Starting regulatory news search...');

    try {
      const res = await fetch('/api/jobs/alerts', { method: 'POST', body: form });
      const data = await readApiJson(res);
      if (!data.job_id) throw new Error('Server did not return a job id');
      alertJobId = data.job_id;
      if (!data.smtp_configured) {
        setAlertStatus('Warning: SMTP not configured — analysis will run but emails may fail.');
      }
      alertTimer = setInterval(() => pollAlerts(alertJobId), 1200);
      pollAlerts(alertJobId);
    } catch (e) {
      alertEls.btnSearch.disabled = false;
      setAlertStatus(e.message || 'Failed', 'error');
      alertEls.progressWrap.style.display = 'none';
      setAlertRunning(false);
    }
  });

  alertEls.btnStop?.addEventListener('click', stopAlerts);

  alertEls.btnDownload.addEventListener('click', () => {
    if (window.__alertJobState) downloadAlertsCsv(window.__alertJobState);
  });

  alertEls.btnReset.addEventListener('click', resetAlerts);

  alertEls.salesOppList?.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const idx = Number(btn.getAttribute('data-idx'));
    const opp = (window.__salesOpps || [])[idx];
    if (!opp) return;
    const action = btn.getAttribute('data-action');
    if (action === 'copy-email') {
      copyText(opp.email_body || '');
      setAlertStatus('Email draft copied.');
      return;
    }
    if (action === 'copy-both') {
      copyText(`Subject: ${opp.recommended_subject || ''}\n\n${opp.email_body || ''}`);
      setAlertStatus('Subject + email draft copied.');
      return;
    }
    if (action === 'push-airtable') {
      btn.disabled = true;
      try {
        const res = await fetch('/api/alerts/sales-opportunity/push', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(opp),
        });
        await readApiJson(res);
        setAlertStatus(`Pushed ${opp.company_name} opportunity to Airtable.`);
      } catch (e) {
        setAlertStatus(e.message || 'Airtable push failed', 'error');
      } finally {
        btn.disabled = false;
      }
    }
  });

  updateAlertFormState();
}

// ---- Dashboard analytics ----
let dashTrendChart = null;
let dashIndustryChart = null;
let dashFunnelChart = null;
let dashPollTimer = null;
let dashLoading = false;

function setDashStatus(msg, kind) {
  const el = document.getElementById('dashStatus');
  if (!el) return;
  if (!msg) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = '';
  el.textContent = msg;
  el.className = 'status' + (kind === 'error' ? ' error' : '');
}

function destroyChart(chart) {
  if (chart && typeof chart.destroy === 'function') chart.destroy();
  return null;
}

async function fetchAnalytics(path) {
  const res = await fetch(path);
  return readApiJson(res);
}

function renderFunnelSteps(stages) {
  const root = document.getElementById('dashFunnel');
  if (!root) return;
  const keys = ['uploaded', 'scraped', 'scored', 'pushed', 'skipped_duplicate', 'failed'];
  const byKey = Object.fromEntries((stages || []).map((s) => [s.key, s]));
  root.innerHTML = keys.map((key) => {
    const s = byKey[key] || { label: key, count: 0, dropoff_pct: 0 };
    const drop = key === 'uploaded' || key === 'skipped_duplicate' || key === 'failed'
      ? ''
      : `<div class="drop">${s.dropoff_pct || 0}% drop-off</div>`;
    return `<div class="dash-funnel-step">
      <div class="count">${s.count ?? 0}</div>
      <div class="label">${s.label || key}</div>
      ${drop}
    </div>`;
  }).join('');
}

function renderRecentRows(activity) {
  const body = document.getElementById('dashRecentBody');
  if (!body) return;
  if (!activity || !activity.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted">No recent activity yet. Run a search to populate the funnel log.</td></tr>';
    return;
  }
  body.innerHTML = activity.map((row) => {
    const score = row.lead_score == null || row.lead_score === '' ? '—' : row.lead_score;
    const status = row.status_tag || row.stage_reached || '—';
    const when = row.timestamp
      ? new Date(row.timestamp).toLocaleString()
      : '—';
    const lowConf = row.low_confidence
      || String(row.ai_maturity_confidence || '').toLowerCase() === 'low'
      || String(row.transformation_readiness_confidence || '').toLowerCase() === 'low'
      || String(row.lead_score_confidence || '').toLowerCase() === 'low';
    const badge = lowConf ? ' <span class="conf-badge low">low confidence</span>' : '';
    const tier = row.enterprise_readiness_tier
      ? ` <span class="muted">· ${escapeHtml(row.enterprise_readiness_tier)}</span>`
      : '';
    const scoreCell = `${escapeHtml(String(score))}${confBadge(row.lead_score_confidence)}`;
    return `<tr class="${lowConf ? 'dash-row-low-conf' : ''}">
      <td>${escapeHtml(row.company_name || '—')}${badge}</td>
      <td class="mono">${scoreCell}</td>
      <td>${escapeHtml(String(status))}${tier}</td>
      <td>${escapeHtml(row.industry || '—')}</td>
      <td class="muted">${escapeHtml(when)}</td>
    </tr>`;
  }).join('');
}

async function loadDashboard() {
  if (dashLoading) return;
  dashLoading = true;
  setDashStatus('Loading analytics…');
  try {
    const [summary, industries, trend, funnel, recent] = await Promise.all([
      fetchAnalytics('/api/analytics/summary'),
      fetchAnalytics('/api/analytics/industries'),
      fetchAnalytics('/api/analytics/trend?days=30'),
      fetchAnalytics('/api/analytics/funnel'),
      fetchAnalytics('/api/analytics/recent?limit=20'),
    ]);

    const total = document.getElementById('kpiTotal');
    const hot = document.getElementById('kpiHot');
    const warm = document.getElementById('kpiWarm');
    const cold = document.getElementById('kpiCold');
    const avg = document.getElementById('kpiAvg');
    if (total) total.textContent = summary.total_leads ?? 0;
    if (hot) hot.textContent = summary.hot?.count ?? 0;
    if (warm) warm.textContent = summary.warm?.count ?? 0;
    if (cold) cold.textContent = summary.cold?.count ?? 0;
    if (avg) avg.textContent = summary.avg_lead_score ?? 0;
    const setStatusTier = (bucket, cardId, badgeId, pctId) => {
      const card = document.getElementById(cardId);
      const badge = document.getElementById(badgeId);
      const pctEl = document.getElementById(pctId);
      if (pctEl) pctEl.textContent = `${bucket?.pct ?? 0}%`;
      const lowN = bucket?.low_confidence_count || 0;
      if (card) card.classList.toggle('dash-kpi-muted', lowN > 0);
      if (badge) {
        if (lowN > 0) {
          badge.style.display = '';
          badge.textContent = `${lowN} low confidence — needs deeper research`;
        } else {
          badge.style.display = 'none';
        }
      }
    };
    setStatusTier(summary.hot, 'kpiHotCard', 'kpiHotBadge', 'kpiHotPct');
    setStatusTier(summary.warm, 'kpiWarmCard', 'kpiWarmBadge', 'kpiWarmPct');
    setStatusTier(summary.cold, 'kpiColdCard', 'kpiColdBadge', 'kpiColdPct');
    const aiMat = document.getElementById('kpiAiMaturity');
    const transform = document.getElementById('kpiTransform');
    if (aiMat) aiMat.textContent = summary.avg_ai_maturity ?? 0;
    if (transform) transform.textContent = summary.avg_transformation_readiness ?? 0;
    const ent = summary.enterprise_readiness || {};
    const setTier = (id, pctId, bucket, cardId, badgeId) => {
      const el = document.getElementById(id);
      const pctEl = document.getElementById(pctId);
      const card = document.getElementById(cardId);
      const badge = document.getElementById(badgeId);
      if (el) el.textContent = bucket?.count ?? 0;
      if (pctEl) pctEl.textContent = `${bucket?.pct ?? 0}%`;
      const lowN = bucket?.low_confidence_count || 0;
      if (card) card.classList.toggle('dash-kpi-muted', lowN > 0);
      if (badge) {
        if (lowN > 0) {
          badge.style.display = '';
          badge.textContent = `${lowN} low confidence — needs deeper research`;
        } else {
          badge.style.display = 'none';
        }
      }
    };
    setTier('kpiEntHigh', 'kpiEntHighPct', ent.high, 'kpiEntHighCard', 'kpiEntHighBadge');
    setTier('kpiEntMedium', 'kpiEntMediumPct', ent.medium, 'kpiEntMediumCard', 'kpiEntMediumBadge');
    setTier('kpiEntLow', 'kpiEntLowPct', ent.low, 'kpiEntLowCard', 'kpiEntLowBadge');
    const hotPct = document.getElementById('kpiHotPct');
    const warmPct = document.getElementById('kpiWarmPct');
    const coldPct = document.getElementById('kpiColdPct');
    if (hotPct) hotPct.textContent = `${summary.hot?.pct ?? 0}%`;
    if (warmPct) warmPct.textContent = `${summary.warm?.pct ?? 0}%`;
    if (coldPct) coldPct.textContent = `${summary.cold?.pct ?? 0}%`;

    const updated = document.getElementById('dashUpdated');
    if (updated) {
      updated.textContent = summary.generated_at
        ? ` · Updated ${new Date(summary.generated_at).toLocaleTimeString()}`
        : '';
    }

    renderFunnelSteps(funnel.stages || []);
    const note = document.getElementById('dashFunnelNote');
    if (note) {
      const air = funnel.airtable_pushed_count;
      note.textContent = air == null
        ? ''
        : ` Airtable currently has ${air} lead(s).`;
    }
    renderRecentRows(recent.activity || []);

    if (typeof Chart !== 'undefined') {
      const trendLabels = (trend.series || []).map((d) => d.date.slice(5));
      const trendData = (trend.series || []).map((d) => d.leads);
      dashTrendChart = destroyChart(dashTrendChart);
      const trendCanvas = document.getElementById('chartTrend');
      if (trendCanvas) {
        dashTrendChart = new Chart(trendCanvas, {
          type: 'line',
          data: {
            labels: trendLabels,
            datasets: [{
              label: 'Leads',
              data: trendData,
              borderColor: '#2563EB',
              backgroundColor: 'rgba(37, 99, 235, 0.12)',
              fill: true,
              tension: 0.3,
              pointRadius: 0,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { maxTicksLimit: 8 } },
              y: { beginAtZero: true, ticks: { precision: 0 } },
            },
          },
        });
      }

      const ind = (industries.industries || []).slice(0, 8);
      dashIndustryChart = destroyChart(dashIndustryChart);
      const indCanvas = document.getElementById('chartIndustries');
      if (indCanvas) {
        dashIndustryChart = new Chart(indCanvas, {
          type: 'bar',
          data: {
            labels: ind.map((i) => i.industry),
            datasets: [{
              label: 'Leads',
              data: ind.map((i) => i.count),
              backgroundColor: '#60A5FA',
            }],
          },
          options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
          },
        });
      }

      const funnelKeys = ['uploaded', 'scraped', 'scored', 'pushed'];
      const funnelMap = Object.fromEntries((funnel.stages || []).map((s) => [s.key, s.count]));
      dashFunnelChart = destroyChart(dashFunnelChart);
      const funnelCanvas = document.getElementById('chartFunnel');
      if (funnelCanvas) {
        dashFunnelChart = new Chart(funnelCanvas, {
          type: 'bar',
          data: {
            labels: ['Uploaded', 'Scraped', 'Scored', 'Pushed'],
            datasets: [{
              label: 'Companies',
              data: funnelKeys.map((k) => funnelMap[k] || 0),
              backgroundColor: ['#93C5FD', '#60A5FA', '#2563EB', '#1D4ED8'],
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
          },
        });
      }
    }

    setDashStatus('');
  } catch (e) {
    setDashStatus(e.message || 'Failed to load dashboard', 'error');
  } finally {
    dashLoading = false;
  }

  if (!dashPollTimer) {
    dashPollTimer = setInterval(() => {
      if (panelDashboard && panelDashboard.style.display !== 'none'
          && document.visibilityState === 'visible') {
        loadDashboard();
      }
    }, 60000);
  }
}

document.getElementById('btnDashRefresh')?.addEventListener('click', () => loadDashboard());

// Clear stale job UI, then show home
loadScoringProfiles();
resetAll();
resetAlerts();
showView('home');
