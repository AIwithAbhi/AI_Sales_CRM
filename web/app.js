let jobId = null;
let timer = null;

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
  kpiDone: document.getElementById('kpiDone'),
  kpiHot: document.getElementById('kpiHot'),
  kpiRecs: document.getElementById('kpiRecs'),
  kpiAvg: document.getElementById('kpiAvg'),
  typoCard: document.getElementById('typoCard'),
  typoLead: document.getElementById('typoLead'),
  typoBody: document.getElementById('typoBody'),
  typoDecline: document.getElementById('typoDecline'),
};

function setStatus(msg, kind = 'info') {
  els.statusBox.style.display = 'block';
  els.statusBox.textContent = msg;
  els.statusBox.style.borderColor = kind === 'error' ? '#FECACA' : 'var(--border)';
  els.statusBox.style.background = kind === 'error' ? '#FEF2F2' : 'var(--bg)';
}

/** Turn FastAPI `detail` (string | object | validation array) into readable text. */
function formatApiDetail(detail, fallback = 'Request failed') {
  if (detail == null || detail === '') return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const loc = Array.isArray(item.loc)
          ? item.loc.filter((p) => p !== 'body').join('.')
          : '';
        const msg = item.msg || item.message || '';
        if (loc && msg) return `${loc}: ${msg}`;
        return msg || loc || JSON.stringify(item);
      }
      return String(item);
    }).filter(Boolean);
    return parts.join('; ') || fallback;
  }
  if (typeof detail === 'object') {
    return detail.msg || detail.message || detail.detail || JSON.stringify(detail);
  }
  return String(detail);
}

function statusPill(status) {
  const s = (status || 'Unknown').toLowerCase();
  if (s === 'hot') return '<span class="pill hot">Hot</span>';
  if (s === 'warm') return '<span class="pill warm">Warm</span>';
  if (s === 'cold') return '<span class="pill cold">Cold</span>';
  if (s === 'not scored' || s === 'not_scored') return '<span class="pill neutral">Not scored</span>';
  if (s === 'review') return '<span class="pill warm">Review</span>';
  if (s === 'error') return '<span class="pill cold">Error</span>';
  return '<span class="pill neutral">Unknown</span>';
}

function matchBadge(r) {
  const conf = (r.match_confidence || '').toString();
  if (!conf) return '<span class="muted">—</span>';
  const cls = conf.toLowerCase() === 'high' ? 'hot' : conf.toLowerCase() === 'medium' ? 'warm' : 'cold';
  const title = escapeHtml(r.match_reason || r.match_domain || '');
  const amb = r.match_ambiguous ? ' · ambiguous' : '';
  return `<span class="pill ${cls}" title="${title}">${escapeHtml(conf)}${amb}</span>`;
}

function errorMatchCell(r) {
  const errText = (r.error || '').toLowerCase();
  const reason = (r.match_reason || '').trim();
  const conf = (r.match_confidence || '').toString();
  const noMatch =
    errText.includes('website not found')
    || errText.includes('url validation')
    || errText.includes('no confident')
    || errText.includes('no valid company')
    || (conf.toLowerCase() === 'low' && !r.url);
  if (noMatch) {
    const title = escapeHtml(reason || r.error || 'No confident company match');
    return `<span class="pill cold" title="${title}">No confident match</span>`;
  }
  if (conf) return matchBadge(r);
  return `<span class="muted" title="${escapeHtml(r.error || '')}">—</span>`;
}

function pushableResults(results) {
  return (results || []).filter((r) => !r.error && !r.review_needed && r.scored !== false && r.lead_score != null);
}

function updatePushDownloadButtons(job) {
  const results = job?.results || [];
  const pushable = pushableResults(results);
  if (els.btnPush) els.btnPush.disabled = pushable.length === 0;
  if (els.btnDownload) els.btnDownload.disabled = results.length === 0;
}

function scoreBadge(score, statusTag) {
  if (score == null || statusTag === 'Not scored') {
    return '<span class="score-badge score-cold" title="Not scored">—</span>';
  }
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
    // Exclude errors and unscored rows from Hot / average KPIs
    if (r.error || r.status_tag === 'Not scored' || r.lead_score == null || r.scored === false) {
      continue;
    }
    if (r.status_tag === 'Hot') hot++;
    sum += Number(r.lead_score) || 0;
    count++;
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

function renderResults(job) {
  const results = job.results || [];
  els.resultsCard.style.display = 'block';
  els.resultsMeta.textContent = `${job.total ?? results.length} companies • ${job.processed ?? results.length} processed`;

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
    const matchCell = err ? errorMatchCell(r) : matchBadge(r);

    els.resultsBody.innerHTML += `
      <tr class="${(!err && r.review_needed) ? 'row-review' : ''}">
        <td>${companyCell}</td>
        <td>${err ? '<span class="muted">—</span>' : scoreBadge(r.lead_score, r.status_tag)}</td>
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
  const unknownList = (r.unknown_fields || []).length
    ? (r.unknown_fields || []).join(', ')
    : [
        (!r.industry || String(r.industry).toLowerCase() === 'unknown') ? 'industry' : '',
        (!r.size_estimate || String(r.size_estimate).toLowerCase() === 'unknown') ? 'size_estimate' : '',
        r.b2b_buyer == null ? 'b2b_buyer' : '',
        (r.lead_score == null || r.status_tag === 'Not scored') ? 'lead_score' : '',
      ].filter(Boolean).join(', ');
  const unknownHtml = unknownList
    ? `<div class="i-card span3">
         <div class="i-title">Unknown fields</div>
         <div class="i-value small">${escapeHtml(unknownList)}</div>
       </div>`
    : '';
  const scoreLabel = (r.lead_score == null || r.status_tag === 'Not scored')
    ? 'Not scored'
    : `${r.lead_score}/10`;

  els.insightsGrid.innerHTML = `
    <div class="i-card span2">
      <div class="i-title">Why this score</div>
      <div class="i-value">${escapeHtml(r.score_explanation || r.score_reason || '')}</div>
      <div class="i-value small" style="margin-top:10px">${escapeHtml(r.summary || '')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Score</div>
      <div class="i-value">${escapeHtml(scoreLabel)}</div>
      <div style="margin-top:10px">${statusPill(r.status_tag)}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Customer Fit</div>
      <div class="i-value">${r.icp_match_score ?? 0}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Industry</div>
      <div class="i-value">${escapeHtml(r.industry || 'Unknown')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Confidence</div>
      <div class="i-value">${escapeHtml(r.confidence || 'LOW')}</div>
      <div class="i-value small" style="margin-top:6px">${escapeHtml(r.business_model || '')}</div>
    </div>
    <div class="i-card">
      <div class="i-title">Size</div>
      <div class="i-value">${escapeHtml(r.size_estimate || 'Unknown')}</div>
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
    ${unknownHtml}
    ${reviewHtml}
  `;
}

function downloadCsv(job) {
  const rows = job.results || [];
  if (!rows.length) return;
  const headers = ['company_name','url','industry','size_estimate','lead_score','status_tag','icp_match_score','email','phone','error'];
  const lines = [headers.join(',')];
  for (const r of rows) {
    lines.push(headers.map(h => `"${String(r[h] ?? '').replace(/"/g, '""')}"`).join(','));
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
  ['resultsCard','insightsCard','icpCard','recsCard','typoCard'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  if (els.typoBody) els.typoBody.innerHTML = '';
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

function hideTypoCard() {
  if (els.typoCard) els.typoCard.style.display = 'none';
  if (els.typoBody) els.typoBody.innerHTML = '';
}

function renderTypoSuggestion(job) {
  const card = els.typoCard;
  const body = els.typoBody;
  if (!card || !body) return;
  const awaitingTypo = job.status === 'needs_typo_confirm';
  const awaitingAmb = job.status === 'needs_disambiguation';
  if (!awaitingTypo && !awaitingAmb) {
    hideTypoCard();
    return;
  }

  card.style.display = 'block';

  if (awaitingAmb) {
    const dis = job.disambiguation || {};
    const original = dis.original_name || (job.companies || [])[0] || 'this company';
    const cands = dis.candidates || [];
    if (els.typoLead) {
      els.typoLead.textContent =
        `“${original}” matches multiple different entities. Pick one to continue, or keep as needs review (never auto-scored).`;
    }
    body.innerHTML = cands.map((c, idx) => {
      const url = c.url || '';
      const domain = (() => {
        try { return url ? new URL(url).hostname.replace(/^www\./, '') : (c.domain || ''); }
        catch { return c.domain || ''; }
      })();
      const label = c.title || c.brand || domain || `Candidate ${idx + 1}`;
      const name = dis.original_name || original;
      return `
        <button type="button" class="disambiguation-option amb-pick"
          data-name="${escapeHtml(name)}" data-url="${escapeHtml(url)}">
          <div class="disambiguation-option-head"><b>Did you mean ${escapeHtml(label)}?</b></div>
          <div class="mono small">${escapeHtml(domain || url || '—')}</div>
          <div class="muted small">${escapeHtml((c.snippet || '').slice(0, 180))}</div>
        </button>`;
    }).join('') || '<p class="muted">No selectable candidates.</p>';

    body.querySelectorAll('.amb-pick').forEach((btn) => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        const suggested = btn.getAttribute('data-name') || original;
        const url = btn.getAttribute('data-url') || '';
        setStatus(`Confirming ${suggested}…`);
        try {
          const res = await fetch(`/api/jobs/${jobId}/resolve-disambiguation`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              accept: true,
              suggested_name: suggested,
              url,
            }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(formatApiDetail(data.detail, 'Could not confirm'));
          hideTypoCard();
          if (!timer) timer = setInterval(() => poll(jobId), 1200);
          poll(jobId);
        } catch (e) {
          setStatus(e.message || 'Could not confirm', 'error');
          btn.disabled = false;
        }
      });
    });
    return;
  }

  const sug = job.typo_suggestion || {};
  const original = sug.original_name || (job.companies || [])[0] || 'this company';
  const suggested = sug.suggested_name || '';
  const url = sug.suggested_url || '';
  const domain = (() => {
    try { return url ? new URL(url).hostname.replace(/^www\./, '') : ''; }
    catch { return ''; }
  })();
  if (els.typoLead) {
    els.typoLead.textContent =
      `No exact match for “${original}”. Search results strongly suggest a nearby company — confirm to continue (never auto-switched).`;
  }
  body.innerHTML = `
    <button type="button" class="disambiguation-option" id="typoAcceptBtn"
      data-name="${escapeHtml(suggested)}" data-url="${escapeHtml(url)}">
      <div class="disambiguation-option-head"><b>Did you mean ${escapeHtml(suggested)}?</b></div>
      <div class="mono small">${escapeHtml(domain || url || '—')}</div>
      <div class="muted small">${escapeHtml((sug.snippet || sug.reason || '').slice(0, 180))}</div>
    </button>`;
  const acceptBtn = document.getElementById('typoAcceptBtn');
  if (acceptBtn) {
    acceptBtn.addEventListener('click', async () => {
      acceptBtn.disabled = true;
      setStatus(`Confirming ${suggested}…`);
      try {
        const res = await fetch(`/api/jobs/${jobId}/resolve-typo`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            accept: true,
            suggested_name: suggested,
            url,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(formatApiDetail(data.detail, 'Could not confirm'));
        hideTypoCard();
        if (!timer) timer = setInterval(() => poll(jobId), 1200);
        poll(jobId);
      } catch (e) {
        setStatus(e.message || 'Could not confirm', 'error');
        acceptBtn.disabled = false;
      }
    });
  }
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

els.typoDecline?.addEventListener('click', async () => {
  if (!jobId) return;
  els.typoDecline.disabled = true;
  const st = window.__jobState?.status;
  const endpoint = st === 'needs_disambiguation'
    ? `/api/jobs/${jobId}/resolve-disambiguation`
    : `/api/jobs/${jobId}/resolve-typo`;
  setStatus(st === 'needs_disambiguation'
    ? 'Keeping ambiguous match as needs review (Not scored).'
    : 'Keeping original query — no confident match.');
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accept: false }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(formatApiDetail(data.detail, 'Could not decline'));
    hideTypoCard();
    if (!timer) timer = setInterval(() => poll(jobId), 1200);
    poll(jobId);
  } catch (e) {
    setStatus(e.message || 'Could not decline', 'error');
  } finally {
    els.typoDecline.disabled = false;
  }
});

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

  els.progressWrap.style.display = 'block';
  els.btnStart.disabled = true;
  els.statusBox.style.display = 'none';

  try {
    const res = await fetch('/api/jobs/search', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(formatApiDetail(data.detail, 'Failed to start job'));
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
  const pushable = pushableResults(window.__jobState?.results || []);
  if (!pushable.length) {
    setStatus('Nothing valid to push — all rows failed or need review.', 'error');
    updatePushDownloadButtons(window.__jobState || {});
    return;
  }
  setStatus('Pushing to Airtable...');
  els.btnPush.disabled = true;
  try {
    const res = await fetch(`/api/jobs/${jobId}/push`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(formatApiDetail(data.detail, 'Push failed'));
    setStatus(`Pushed: ${data.pushed}, Failed: ${data.failed}` +
      (data.skipped_review ? ` (${data.skipped_review} need review)` : ''),
      data.failed ? 'error' : 'info');
  } catch (e) {
    setStatus(e.message || 'Push failed', 'error');
  } finally {
    updatePushDownloadButtons(window.__jobState || {});
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
    const data = await res.json();
    if (!res.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : 'Failed to fetch job';
      if (res.status === 404) {
        throw new Error('Job session lost. Select your CSV and click Search All again.');
      }
      throw new Error(detail);
    }

    window.__jobState = data;
    const prog = Math.round((data.progress || 0) * 100);
    els.progressBar.style.width = prog + '%';
    els.progressText.textContent = prog + '%';
    renderKPIs(data);
    renderTypoSuggestion(data);

    if ((data.results || []).length > 0) {
      renderResults(data);
      renderIcp(data);
      renderRecommendations(data);
    }

    if (data.status === 'needs_typo_confirm') {
      setStatus('Did you mean a different company? Confirm below to continue.');
      els.btnPush.disabled = true;
      els.btnDownload.disabled = !(data.results || []).length;
      return;
    }
    if (data.status === 'needs_disambiguation') {
      setStatus('Multiple companies match this name — pick one below, or keep as needs review.');
      els.btnPush.disabled = true;
      els.btnDownload.disabled = !(data.results || []).length;
      return;
    }

    if (data.status === 'done') {
      setStatus('✓ Done. Push to Airtable or download CSV.');
      updatePushDownloadButtons(data);
      updateSearchFormState();
      hideTypoCard();
      stopLeadPoll();
      return;
    }
    if (data.status === 'cancelled') {
      setStatus('Stopped. Partial results kept — download CSV or push to Airtable.');
      updatePushDownloadButtons(data);
      updateSearchFormState();
      hideTypoCard();
      stopLeadPoll();
      return;
    }
    if (data.status === 'interrupted') {
      setStatus(data.error || 'Search was interrupted. Click Search All to run again.', 'error');
      updatePushDownloadButtons(data);
      updateSearchFormState();
      stopLeadPoll();
      return;
    }
    if (data.status === 'failed') {
      setStatus('Job failed: ' + (data.error || 'Unknown'), 'error');
      updatePushDownloadButtons(data);
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
const panelApprovals = document.getElementById('panelApprovals');
const pageSubtitle = document.getElementById('pageSubtitle');
const pageTitle = document.getElementById('pageTitle');

function selectTab(name) {
  if (panelSearch) panelSearch.style.display = name === 'search' ? '' : 'none';
  if (panelAlerts) panelAlerts.style.display = name === 'alerts' ? '' : 'none';
  if (panelApprovals) panelApprovals.style.display = name === 'approvals' ? '' : 'none';
  const titles = {
    alerts: 'Industry Updates',
    approvals: 'Approvals',
    search: 'Research Companies',
  };
  const subtitles = {
    alerts: 'Monitor news for your prospects',
    approvals: 'Review outreach & content drafts; call log',
    search: 'Enter a company name or upload a CSV',
  };
  if (pageTitle) pageTitle.textContent = titles[name] || titles.search;
  if (pageSubtitle) pageSubtitle.textContent = subtitles[name] || subtitles.search;
  if (name === 'approvals') loadApprovals();
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

function summaryText(job) {
  const s = job.alerts_summary || {};
  return `${s.emails_sent ?? 0} emails sent`;
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
    const data = await res.json();
    if (!res.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : 'Failed to fetch job';
      if (res.status === 404) {
        throw new Error('Job session lost. Please click Search again to start a new run.');
      }
      throw new Error(detail);
    }

    window.__alertJobState = data;
    const prog = Math.round((data.progress || 0) * 100);
    alertEls.progressBar.style.width = prog + '%';
    alertEls.progressText.textContent = prog + '%';
    renderAlertKPIs(data);
    renderAlertLog(data);
    if ((data.results || []).length) renderAlertResults(data);

    if (data.status === 'done') {
      const s = data.alerts_summary || {};
      setAlertStatus(`Done. ${s.emails_sent ?? 0} consolidated alert email(s) sent.`);
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
    const data = await res.json();
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
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Test failed');
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
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to start');
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
  updateAlertFormState();
}

// ---- Approvals (multi-agent queue) ----
let approvalsFilter = 'pending';
let approvalsLoading = false;

function setApprovalsStatus(msg, kind) {
  const el = document.getElementById('approvalsStatus');
  if (!el) return;
  if (!msg) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = '';
  el.textContent = msg;
  el.className = 'status' + (kind ? ` ${kind}` : '');
}

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatTs(ts) {
  if (!ts) return '—';
  try {
    return new Date(Number(ts) * 1000).toLocaleString();
  } catch {
    return '—';
  }
}

function statusBadge(status, replied) {
  if (status === 'approved_ready_to_send') {
    const reply = replied ? ' · Replied' : '';
    return `<span class="badge-ready">Ready to send${reply}</span>`;
  }
  if (status === 'rejected') return '<span class="badge-rejected">Rejected</span>';
  return '<span class="badge-pending">Pending</span>';
}

function renderApprovalCard(d) {
  const snap = d.lead_snapshot || {};
  const typeLabel = d.type === 'content' ? 'Content' : 'Outreach';
  const company = d.company_name || (d.type === 'content' ? 'Content piece' : '—');
  const score = snap.lead_score != null ? `Score ${snap.lead_score}` : '';
  const canEdit = d.status === 'pending' || d.status === 'approved_ready_to_send';
  const canApprove = d.status === 'pending';
  const canReject = d.status === 'pending';
  const canReply = d.type === 'outreach' && d.status === 'approved_ready_to_send' && !d.replied;
  return `
    <article class="approval-card" data-id="${escHtml(d.id)}">
      <header class="approval-card-head">
        <div>
          <div class="approval-type">${escHtml(typeLabel)}</div>
          <h3 class="approval-company">${escHtml(company)}</h3>
          <p class="muted approval-meta">
            ${escHtml(d.context_summary || '')}
            ${score ? ` · ${escHtml(score)}` : ''}
            · ${formatTs(d.created_at)}
          </p>
        </div>
        <div>${statusBadge(d.status, d.replied)}</div>
      </header>
      <label class="muted sm">Subject / title</label>
      <input class="approval-subject email-input" ${canEdit ? '' : 'readonly'}
        value="${escHtml(d.subject || d.title || '')}" />
      <label class="muted sm">Body</label>
      <textarea class="approval-body" rows="8" ${canEdit ? '' : 'readonly'}>${escHtml(d.body || '')}</textarea>
      <div class="approval-actions">
        ${canEdit ? `<button type="button" class="btn ghost sm" data-act="save">Save edits</button>` : ''}
        ${canApprove ? `<button type="button" class="btn primary sm" data-act="approve">Approve</button>` : ''}
        ${canReject ? `<button type="button" class="btn ghost sm" data-act="reject">Reject</button>` : ''}
        ${canReply ? `<button type="button" class="btn alert-primary sm" data-act="replied">Mark as replied</button>` : ''}
      </div>
    </article>`;
}

async function loadCallLog() {
  const body = document.getElementById('callLogBody');
  if (!body) return;
  try {
    const res = await fetch('/api/agents/calls?limit=50');
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Call log failed');
    const events = data.events || [];
    if (!events.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted">No call events yet</td></tr>';
      return;
    }
    body.innerHTML = events.map((e) => `
      <tr>
        <td>${escHtml(e.company_name || '—')}</td>
        <td class="mono">${escHtml(e.phone_masked || '—')}</td>
        <td>${escHtml(e.rule || '—')}</td>
        <td>${escHtml(e.status || '—')}${e.error ? ` <span class="muted">(${escHtml(e.error)})</span>` : ''}</td>
        <td>${formatTs(e.created_at)}</td>
      </tr>`).join('');
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" class="muted">${escHtml(err.message || 'Failed')}</td></tr>`;
  }
}

async function loadApprovals() {
  if (approvalsLoading) return;
  approvalsLoading = true;
  const list = document.getElementById('approvalsList');
  const meta = document.getElementById('approvalsMeta');
  try {
    let url = '/api/agents/drafts?limit=100';
    if (approvalsFilter && approvalsFilter !== 'all') {
      url += `&status=${encodeURIComponent(approvalsFilter)}`;
    }
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to load drafts');
    const drafts = data.drafts || [];
    if (meta) meta.textContent = `${drafts.length} item(s)`;
    if (list) {
      list.innerHTML = drafts.length
        ? drafts.map(renderApprovalCard).join('')
        : '<p class="muted approvals-empty">No drafts in this filter. Run Scan leads or Run content.</p>';
    }
    setApprovalsStatus('');
    await loadCallLog();
  } catch (e) {
    setApprovalsStatus(e.message || 'Failed to load approvals', 'error');
  } finally {
    approvalsLoading = false;
  }
}

document.querySelectorAll('.approvals-filter').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.approvals-filter').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    approvalsFilter = btn.getAttribute('data-filter') || 'pending';
    loadApprovals();
  });
});

document.getElementById('btnApprovalsRefresh')?.addEventListener('click', () => loadApprovals());

document.getElementById('btnOutreachScan')?.addEventListener('click', async () => {
  setApprovalsStatus('Scanning leads for outreach drafts…');
  try {
    const res = await fetch('/api/agents/outreach/run', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Scan failed');
    setApprovalsStatus(`Created ${data.created || 0} draft(s), skipped ${data.skipped || 0}`, 'ok');
    await loadApprovals();
  } catch (e) {
    setApprovalsStatus(e.message || 'Scan failed', 'error');
  }
});

document.getElementById('btnContentRun')?.addEventListener('click', async () => {
  setApprovalsStatus('Running content creation…');
  try {
    const res = await fetch('/api/agents/content/run', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Content run failed');
    if (!data.ok) {
      setApprovalsStatus(data.message || data.reason || 'No content draft', 'error');
    } else {
      setApprovalsStatus('Content draft created', 'ok');
    }
    await loadApprovals();
  } catch (e) {
    setApprovalsStatus(e.message || 'Content run failed', 'error');
  }
});

document.getElementById('btnCallsRun')?.addEventListener('click', async () => {
  setApprovalsStatus('Running call trigger…');
  try {
    const res = await fetch('/api/agents/calls/run', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Call trigger failed');
    const note = data.vapi_configured ? '' : ' (Vapi not configured — events logged as skipped)';
    setApprovalsStatus(`Triggered ${data.triggered || 0} event(s)${note}`, 'ok');
    await loadApprovals();
  } catch (e) {
    setApprovalsStatus(e.message || 'Call trigger failed', 'error');
  }
});

document.getElementById('approvalsList')?.addEventListener('click', async (ev) => {
  const btn = ev.target.closest('[data-act]');
  if (!btn) return;
  const card = btn.closest('.approval-card');
  if (!card) return;
  const id = card.getAttribute('data-id');
  const act = btn.getAttribute('data-act');
  const subjectEl = card.querySelector('.approval-subject');
  const bodyEl = card.querySelector('.approval-body');
  try {
    if (act === 'save') {
      const res = await fetch(`/api/agents/drafts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subject: subjectEl?.value || '',
          body: bodyEl?.value || '',
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Save failed');
      setApprovalsStatus('Draft saved', 'ok');
    } else if (act === 'approve') {
      // Persist edits before approve
      await fetch(`/api/agents/drafts/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subject: subjectEl?.value || '',
          body: bodyEl?.value || '',
        }),
      });
      const res = await fetch(`/api/agents/drafts/${id}/approve`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Approve failed');
      setApprovalsStatus('Approved — Ready to send (not emailed)', 'ok');
    } else if (act === 'reject') {
      const res = await fetch(`/api/agents/drafts/${id}/reject`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Reject failed');
      setApprovalsStatus('Draft rejected', 'ok');
    } else if (act === 'replied') {
      const res = await fetch(`/api/agents/drafts/${id}/replied`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Mark replied failed');
      setApprovalsStatus('Marked as replied — Call Trigger will pick this up', 'ok');
    }
    await loadApprovals();
  } catch (e) {
    setApprovalsStatus(e.message || 'Action failed', 'error');
  }
});

// Clear stale job UI, then show home
resetAll();
resetAlerts();
showView('home');
