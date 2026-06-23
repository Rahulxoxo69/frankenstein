/**
 * Frankenstein Vision — Frontend Logic
 */

const API = '';

// ── State ──
let selectedFiles = [];
let lastManifest = null;

// ── DOM refs ──
const $ = id => document.getElementById(id);
const navTabs = document.querySelectorAll('.nav-tab');
const panels = document.querySelectorAll('.tab-panel');
const uploadZone = $('upload-zone');
const fileInput = $('file-input');
const previewGrid = $('preview-grid');
const btnAnalyze = $('btn-analyze');
const pipelineCard = $('pipeline-card');
const chips = document.querySelectorAll('.chip');
const btnExportReport = $('btn-export-report');

// ── Tab Navigation ──
navTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        const target = tab.dataset.tab;
        navTabs.forEach(t => t.classList.remove('active'));
        panels.forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        $(`panel-${target}`).classList.add('active');
        if (target === 'vault') loadVaultData();
    });
});

// ── Demo Scenario Cards ──
document.querySelectorAll('.demo-card').forEach(card => {
    card.addEventListener('click', async () => {
        const scenario = card.dataset.scenario;
        if (!scenario) return;

        card.classList.add('loading');
        card.querySelector('.demo-name').textContent = 'Analyzing...';
        pipelineCard.hidden = false;

        const steps = ['upload', 'yolo', 'condition', 'grounding', 'vault'];
        steps.forEach(s => {
            $(`step-${s}`).className = 'pipeline-step';
            $(`step-${s}`).querySelector('.step-status').textContent = 'Waiting';
        });

        try {
            setStepActive('upload'); await sleep(400); setStepDone('upload');
            setStepActive('yolo');
            const resp = await fetch(API + '/api/demo/run/' + scenario, { method: 'POST' });
            if (!resp.ok) throw new Error('Demo error: ' + resp.status);
            await sleep(300); setStepDone('yolo');
            setStepActive('condition'); await sleep(400); setStepDone('condition');
            setStepActive('grounding'); await sleep(350); setStepDone('grounding');
            setStepActive('vault'); await sleep(250); setStepDone('vault');

            lastManifest = await resp.json();
            renderResults(lastManifest);
            showToast('Demo loaded: ' + lastManifest.context.device_model, 'success');

            navTabs.forEach(t => t.classList.remove('active'));
            panels.forEach(p => p.classList.remove('active'));
            $('nav-results').classList.add('active');
            $('panel-results').classList.add('active');
        } catch (err) {
            console.error(err);
            showToast('Demo error: ' + err.message, 'error');
            pipelineCard.hidden = true;
        } finally {
            card.classList.remove('loading');
            const names = { phone: 'Broken Phone', coffee: 'Coffee Machine', laptop: 'Laptop Board', drone: 'FPV Drone' };
            card.querySelector('.demo-name').textContent = names[scenario] || scenario;
        }
    });
});

// ── File Upload ──
uploadZone.addEventListener('click', () => fileInput.click());
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
uploadZone.addEventListener('drop', e => { e.preventDefault(); uploadZone.classList.remove('dragover'); addFiles(Array.from(e.dataTransfer.files)); });
fileInput.addEventListener('change', () => addFiles(Array.from(fileInput.files)));

function addFiles(files) {
    const validFiles = [];
    for (const f of files) {
        if (!f.type.startsWith('image/')) continue;
        if (f.size > 20 * 1024 * 1024) {
            showToast('File ' + f.name + ' is too large (max 20MB)', 'error');
            continue;
        }
        validFiles.push(f);
    }
    selectedFiles.push(...validFiles);
    renderPreviews();
    btnAnalyze.disabled = selectedFiles.length === 0;
}

function addBadgeToPreview(filename, count) {
    const imgs = previewGrid.querySelectorAll('img');
    for (const img of imgs) {
        if (img.alt === filename || img.src.includes(filename)) {
            let badge = img.parentElement.querySelector('.detect-badge');
            if (!badge) {
                badge = document.createElement('div');
                badge.className = 'detect-badge';
                img.parentElement.appendChild(badge);
            }
            badge.textContent = count + ' components';
        }
    }
}

function renderPreviews() {
    previewGrid.innerHTML = '';
    if (selectedFiles.length === 0) {
        uploadZone.style.display = 'block';
        return;
    }
    uploadZone.style.display = 'none';
    selectedFiles.forEach((file, i) => {
        const div = document.createElement('div');
        div.className = 'preview-item';
        const img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        img.alt = file.name;
        const btn = document.createElement('button');
        btn.className = 'preview-remove';
        btn.innerHTML = 'x';
        btn.onclick = () => { selectedFiles.splice(i, 1); renderPreviews(); btnAnalyze.disabled = selectedFiles.length === 0; };
        div.appendChild(img);
        div.appendChild(btn);
        previewGrid.appendChild(div);
    });
}

// ── Chip Toggle ──
chips.forEach(chip => chip.addEventListener('click', () => chip.classList.toggle('active')));

// ── Demo Image Loader ──
const btnDemo = $('btn-demo');
if (btnDemo) {
    btnDemo.addEventListener('click', async (e) => {
        e.stopPropagation();
        btnDemo.textContent = 'Loading sample...';
        btnDemo.disabled = true;
        try {
            const listResp = await fetch(API + '/api/sample-images');
            const listData = await listResp.json();
            if (!listData.images || listData.images.length === 0) {
                showToast('No sample images available', 'error');
                return;
            }
            const filename = listData.images[Math.floor(Math.random() * listData.images.length)];
            const imgResp = await fetch(API + '/api/sample-images/' + filename);
            const blob = await imgResp.blob();
            const file = new File([blob], filename, { type: 'image/jpeg' });
            selectedFiles = [file];
            renderPreviews();
            btnAnalyze.disabled = false;
            $('device-model').value = 'Unknown Device';
            $('failure-cause').value = '';
            showToast('Loaded ' + filename, 'success');
        } catch (err) {
            showToast('Failed to load sample: ' + err.message, 'error');
        } finally {
            btnDemo.textContent = 'Load Sample';
            btnDemo.disabled = false;
        }
    });
}

// ── Analyze ──
btnAnalyze.addEventListener('click', () => runAnalysis());

async function runAnalysis() {
    if (selectedFiles.length === 0) return;

    const btnText = btnAnalyze.querySelector('.btn-text');
    const btnLoader = btnAnalyze.querySelector('.btn-loader');
    btnText.hidden = true;
    btnLoader.hidden = false;
    btnAnalyze.disabled = true;

    pipelineCard.hidden = false;
    const steps = ['upload', 'yolo', 'condition', 'grounding', 'vault'];
    steps.forEach(s => {
        $(`step-${s}`).className = 'pipeline-step';
        $(`step-${s}`).querySelector('.step-status').textContent = 'Waiting';
    });

    setStepActive('upload');
    try {
        const formData = new FormData();
        selectedFiles.forEach(f => formData.append('images', f));
        formData.append('device_model', $('device-model').value || 'Unknown');
        formData.append('failure_cause', $('failure-cause').value || 'Unknown');
        formData.append('skill_level', 3);
        const activeTools = Array.from(document.querySelectorAll('.chip.active')).map(c => c.dataset.tool);
        formData.append('available_tools', JSON.stringify(activeTools));
        setStepDone('upload');

        setStepActive('yolo');
        const resp = await fetch(API + '/api/teardown', { method: 'POST', body: formData });
        if (!resp.ok) {
            try { const errorData = await resp.json(); throw new Error(errorData.detail || 'Server error'); }
            catch { throw new Error('Server error: ' + resp.status); }
        }
        setStepDone('yolo');

        setStepActive('condition'); await sleep(500); setStepDone('condition');
        setStepActive('grounding'); await sleep(400); setStepDone('grounding');
        setStepActive('vault'); await sleep(300); setStepDone('vault');

        lastManifest = await resp.json();
        renderResults(lastManifest);
        if (lastManifest.parts && selectedFiles.length > 0) {
            addBadgeToPreview(selectedFiles[0].name, lastManifest.parts.length);
        }
        showToast('Analysis complete!', 'success');

        navTabs.forEach(t => t.classList.remove('active'));
        panels.forEach(p => p.classList.remove('active'));
        $('nav-results').classList.add('active');
        $('panel-results').classList.add('active');
    } catch (err) {
        console.error(err);
        showToast('Error: ' + err.message, 'error');
        pipelineCard.hidden = true;
    } finally {
        btnText.hidden = false;
        btnLoader.hidden = true;
        btnAnalyze.disabled = selectedFiles.length === 0;
    }
}

function setStepActive(name) {
    const el = $(`step-${name}`);
    el.className = 'pipeline-step active';
    el.querySelector('.step-status').textContent = 'Processing...';
}

function setStepDone(name) {
    const el = $(`step-${name}`);
    el.className = 'pipeline-step done';
    el.querySelector('.step-status').textContent = 'Done';
    el.parentElement.querySelectorAll('.step-connector').forEach(c => {
        if (el.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) {
            c.classList.add('done');
        }
    });
}

// ── Results ──
function renderResults(manifest) {
    const partsGrid = $('parts-grid');
    const damagesSection = $('damages-section');
    const damagesGrid = $('damages-grid');

    if (!manifest || !manifest.parts || manifest.parts.length === 0) {
        partsGrid.innerHTML = '<div class="empty-state" style="padding:40px;text-align:center;color:var(--text-muted)">No parts detected</div>';
        return;
    }

    const fCount = manifest.parts.filter(p => p.status === 'functional').length;
    const rCount = manifest.parts.filter(p => p.status === 'repairable').length;
    const uCount = manifest.parts.filter(p => p.status === 'unsafe').length;

    let summary = document.getElementById('results-summary');
    if (!summary) {
        summary = document.createElement('div');
        summary.id = 'results-summary';
        partsGrid.parentElement.insertBefore(summary, partsGrid);
    }
    summary.innerHTML = '<div style="display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap">' +
        '<div class="stat-card" style="min-width:80px;text-align:center;padding:8px 16px;border:1px solid var(--border-subtle);border-radius:8px"><div style="font-size:24px;font-weight:700;color:var(--status-good)">' + fCount + '</div><div style="font-size:11px;color:var(--text-muted)">Functional</div></div>' +
        '<div class="stat-card" style="min-width:80px;text-align:center;padding:8px 16px;border:1px solid var(--border-subtle);border-radius:8px"><div style="font-size:24px;font-weight:700;color:var(--status-mid)">' + rCount + '</div><div style="font-size:11px;color:var(--text-muted)">Repairable</div></div>' +
        '<div class="stat-card" style="min-width:80px;text-align:center;padding:8px 16px;border:1px solid var(--border-subtle);border-radius:8px"><div style="font-size:24px;font-weight:700;color:var(--status-bad)">' + uCount + '</div><div style="font-size:11px;color:var(--text-muted)">Unsafe</div></div>' +
        '<div class="stat-card" style="min-width:80px;text-align:center;padding:8px 16px;border:1px solid var(--border-subtle);border-radius:8px"><div style="font-size:24px;font-weight:700;color:var(--accent)">' + manifest.parts.length + '</div><div style="font-size:11px;color:var(--text-muted)">Total Parts</div></div>' +
        '</div>';

    if (btnExportReport) btnExportReport.hidden = false;
    partsGrid.innerHTML = manifest.parts.map(p => renderPartCard(p)).join('');

    if (manifest.board_damages && manifest.board_damages.length > 0) {
        damagesSection.hidden = false;
        damagesGrid.innerHTML = manifest.board_damages.map(d => 
            '<div class="part-card" style="border-left:3px solid var(--status-bad);margin-bottom:8px">' +
            '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<div><strong>' + escapeHTML(d.defect_type) + '</strong><div style="font-size:12px;color:var(--text-muted)">Board Defect</div></div>' +
            '<span style="font-size:12px;color:var(--text-muted)">' + (d.confidence * 100).toFixed(0) + '%</span>' +
            '</div>' +
            (d.affects_part ? '<div style="font-size:12px;color:var(--text-muted)">Affects: ' + escapeHTML(d.affects_part) + '</div>' : '') +
            '</div>'
        ).join('');
    } else {
        damagesSection.hidden = true;
    }
}

function renderPartCard(part) {
    const confPct = (part.confidence * 100).toFixed(0);
    const confClass = part.confidence > 0.8 ? 'high' : part.confidence > 0.5 ? 'medium' : 'low';

    let specsHTML = '';
    if (part.specs) {
        const s = part.specs;
        const entries = [];
        if (s.voltage) entries.push(['Voltage', s.voltage]);
        if (s.current_rating) entries.push(['Current', s.current_rating]);
        if (s.package) entries.push(['Package', s.package]);
        if (s.part_number) entries.push(['Part #', s.part_number]);
        if (s.raw) {
            Object.entries(s.raw).forEach(function(kv) {
                var k = kv[0], v = kv[1];
                if (k !== 'note' && k !== 'ocr_text' && entries.length < 6) {
                    entries.push([k.replace(/_/g, ' '), String(v)]);
                }
            });
        }
        if (entries.length > 0) {
            specsHTML = '<div class="part-specs">' + entries.map(function(e) {
                return '<div class="spec-item"><span class="spec-label">' + e[0] + '</span><span class="spec-value">' + e[1] + '</span></div>';
            }).join('') + '</div>';
        }
    }

    var descHTML = '';
    if (part.description) descHTML = '<div class="part-description">' + escapeHTML(part.description) + '</div>';
    var reuseHTML = '';
    if (part.reuse_suggestion) reuseHTML = '<div class="part-reuse">' + escapeHTML(part.reuse_suggestion) + '</div>';

    var noteHTML = '';
    if (part.repair_note) noteHTML = '<div class="part-note repair">' + escapeHTML(part.repair_note) + '</div>';
    if (part.disposal_reason) noteHTML = '<div class="part-note disposal">' + escapeHTML(part.disposal_reason) + '</div>';

    var sourceHTML = '';
    if (part.service_specs) {
        sourceHTML = '<div class="part-source">Source: ' + escapeHTML(part.source || 'unknown') + '</div>';
    }

    return '<div class="part-card">' +
        '<div class="part-card-header">' +
        '<div><div class="part-name">' + escapeHTML(part.name) + '</div><div class="part-category">' + escapeHTML(part.category || '') + '</div></div>' +
        '<span class="status-badge ' + part.status + '">' + part.status + '</span>' +
        '</div>' +
        '<div class="part-confidence"><div class="confidence-bar"><div class="confidence-fill ' + confClass + '" style="width:' + confPct + '%"></div></div><span class="confidence-text">' + confPct + '%</span></div>' +
        descHTML +
        reuseHTML +
        specsHTML +
        noteHTML +
        sourceHTML +
        '<button class="part-delete" onclick="deletePart(\'' + part.part_id + '\')" title="Remove from vault">x</button>' +
        '</div>';
}

// ── Vault ──
async function loadVaultData() {
    try {
        var statsResp = await fetch(API + '/api/vault/stats');
        var partsResp = await fetch(API + '/api/vault/parts');
        if (statsResp.ok) {
            var stats = await statsResp.json();
            var ip = document.getElementById('impact-parts');
            var is_ = document.getElementById('impact-sessions');
            if (ip) ip.textContent = stats.total_parts || 0;
            if (is_) is_.textContent = stats.teardown_sessions || 0;
            $('stat-total').textContent = stats.total_parts || 0;
            $('stat-functional').textContent = stats.functional || 0;
            $('stat-repairable').textContent = stats.repairable || 0;
            $('stat-unsafe').textContent = stats.unsafe || 0;
        }
        if (partsResp.ok) {
            var data = await partsResp.json();
            renderVaultParts(data.parts || [], $('vault-parts-grid'));
        }
    } catch (err) {
        console.error('Vault load error:', err);
    }
}

var searchInput = $('vault-search');
var btnSearch = $('btn-vault-search');
if (btnSearch && searchInput) {
    btnSearch.addEventListener('click', function() { vaultSearch(); });
    searchInput.addEventListener('keydown', function(e) { if (e.key === 'Enter') vaultSearch(); });
}

async function vaultSearch() {
    var q = searchInput.value.trim();
    if (!q) return;
    try {
        var resp = await fetch(API + '/api/vault/search?q=' + encodeURIComponent(q) + '&top_k=12');
        if (!resp.ok) throw new Error('Search failed');
        var data = await resp.json();
        var vaultResults = $('vault-results');
        if (data.results && data.results.length > 0) {
            vaultResults.innerHTML = '<h3>' + data.results.length + ' results</h3>' + data.results.map(renderVaultResultCard).join('');
        } else {
            vaultResults.innerHTML = '<p>No parts found.</p>';
        }
    } catch (err) {
        showToast('Search error: ' + err.message, 'error');
    }
}

function renderVaultResultCard(result) {
    var p = result.part || result;
    return '<div class="part-card">' +
        '<div class="part-card-header">' +
        '<div><div class="part-name">' + escapeHTML(p.name) + '</div><div class="part-category">' + escapeHTML(p.category || '') + '</div></div>' +
        '<span class="status-badge ' + p.status + '">' + p.status + '</span>' +
        '</div>' +
        (p.description ? '<div class="part-description">' + escapeHTML(p.description) + '</div>' : '') +
        (p.reuse_suggestion ? '<div class="part-reuse">' + escapeHTML(p.reuse_suggestion) + '</div>' : '') +
        (p.specs ? renderSpecsInline(p.specs) : '') +
        (p.repair_note ? '<div class="part-note">' + escapeHTML(p.repair_note) + '</div>' : '') +
        '<div class="part-source">' + (p.source || 'vault') + '</div>' +
        '</div>';
}

function renderSpecsInline(specs) {
    if (!specs || typeof specs !== 'object') return '';
    var entries = Object.entries(specs).filter(function(kv) { return kv[1] && kv[0] !== 'note'; }).slice(0, 4);
    if (entries.length === 0) return '';
    return '<div class="part-specs">' + entries.map(function(kv) {
        return '<div class="spec-item"><span class="spec-label">' + escapeHTML(kv[0].replace(/_/g, ' ')) + '</span><span class="spec-value">' + escapeHTML(kv[1]) + '</span></div>';
    }).join('') + '</div>';
}

function renderVaultParts(parts, container) {
    if (parts.length === 0) {
        container.innerHTML = '<div class="results-empty"><p>Vault is empty. Run a teardown to add parts.</p></div>';
        return;
    }
    container.innerHTML = parts.map(function(p) {
        return '<div class="part-card">' +
            '<div class="part-card-header">' +
            '<div><div class="part-name">' + escapeHTML(p.name) + '</div><div class="part-category">' + escapeHTML(p.category || '') + '</div></div>' +
            '<span class="status-badge ' + p.status + '">' + p.status + '</span>' +
            '</div>' +
            (p.description ? '<div class="part-description">' + escapeHTML(p.description) + '</div>' : '') +
            (p.reuse_suggestion ? '<div class="part-reuse">' + escapeHTML(p.reuse_suggestion) + '</div>' : '') +
            (p.specs ? renderSpecsInline(p.specs) : '') +
            (p.repair_note ? '<div class="part-note">' + escapeHTML(p.repair_note) + '</div>' : '') +
            '<div class="part-source">' + (p.is_available ? 'Available' : 'Used') + ' &middot; ' + escapeHTML(p.session_id || '') + '</div>' +
            '</div>';
    }).join('');
}

// ── Export Report ──
if (btnExportReport) {
    btnExportReport.addEventListener('click', function() {
        if (lastManifest) exportReport(lastManifest.teardown_id);
        else showToast('No analysis results to export', 'error');
    });
}

function exportReport(teardown_id) {
    fetch(API + '/api/report/' + teardown_id)
        .then(function(resp) {
            if (!resp.ok) throw new Error('Report failed');
            return resp.json();
        })
        .then(function(report) {
            var device = (report.context && report.context.device_model) || (lastManifest && lastManifest.context && lastManifest.context.device_model) || 'Unknown Device';
            var tid = teardown_id || 'unknown';
            var partsHtml = (report.parts || []).map(function(p) {
                var bg = p.status === 'functional' ? '#d4edda' : p.status === 'repairable' ? '#fff3cd' : '#f8d7da';
                var cl = p.status === 'functional' ? '#155724' : p.status === 'repairable' ? '#856404' : '#721c24';
                return '<div style="border:1px solid #ddd;border-radius:8px;padding:16px;margin:8px 0;font-family:sans-serif">' +
                    '<h3 style="margin:0">' + escapeHTML(p.name || '') + '</h3>' +
                    '<p style="margin:4px 0;color:#666">' + (p.category || '') + ' <span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;background:' + bg + ';color:' + cl + '">' + (p.status || '') + '</span></p>' +
                    '<p><strong>Confidence:</strong> ' + (p.confidence * 100).toFixed(0) + '%</p>' +
                    (p.description ? '<p>' + escapeHTML(p.description) + '</p>' : '') +
                    (p.reuse_suggestion ? '<p>' + escapeHTML(p.reuse_suggestion) + '</p>' : '') +
                    (p.repair_note ? '<p><strong>Repair:</strong> ' + escapeHTML(p.repair_note) + '</p>' : '') +
                    '</div>';
            }).join('');
            var html = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Repair Guide - ' + device + '</title>' +
                '<style>body{font-family:system-ui,sans-serif;max-width:800px;margin:auto;padding:20px;background:#f5f5f5}' +
                '.header{background:#fff;border-radius:12px;padding:24px;margin-bottom:20px}' +
                'h1{margin:0 0 8px 0}.footer{text-align:center;color:#999;font-size:12px;margin-top:40px}' +
                '</style></head><body>' +
                '<div class="header"><h1>Frankenstein Repair Guide</h1>' +
                '<p style="color:#666">Device: ' + device + '</p><p style="color:#666">Teardown ID: ' + tid + '</p>' +
                '<p style="color:#666">Generated: ' + new Date().toLocaleDateString() + '</p></div>' +
                partsHtml +
                '<div class="footer">Generated by Frankenstein - E-Waste Component Recovery System</div>' +
                '</body></html>';
            var blob = new Blob([html], { type: 'text/html' });
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'repair-guide-' + tid + '.html';
            document.body.appendChild(a);
            a.click();
            a.remove();
            showToast('Repair Guide downloaded (HTML)', 'success');
        })
        .catch(function(err) {
            showToast('Export error: ' + err.message, 'error');
        });
}

// ── Toast ──
function showToast(msg, type) {
    type = type || 'info';
    var container = $('toast-container');
    var toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(function() { toast.remove(); }, 3000);
}

// ── Health Check ──
async function checkAPI() {
    try {
        var resp = await fetch(API + '/api/health');
        if (resp.ok) {
            $('api-status').className = 'status-dot online';
            $('api-status-text').textContent = 'Connected';
        } else {
            throw new Error();
        }
    } catch (e) {
        $('api-status').className = 'status-dot offline';
        $('api-status-text').textContent = 'Offline';
    }
}

// ── Delete Part ──
async function deletePart(partId) {
    if (!confirm('Remove this part from the vault?')) return;
    try {
        var resp = await fetch(API + '/api/vault/parts/' + encodeURIComponent(partId), { method: 'DELETE' });
        if (!resp.ok) throw new Error('Delete failed');
        showToast('Part removed from vault', 'success');
        if (document.getElementById('panel-vault').classList.contains('active')) {
            loadVaultData();
        }
    } catch (err) {
        showToast('Delete error: ' + err.message, 'error');
    }
}

// ── Utilities ──
var sleep = function(ms) { return new Promise(function(r) { setTimeout(r, ms); }); };
var escapeHTML = function(str) {
    return String(str).replace(/[&<>'"]/g, function(tag) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag;
    });
};

// ── Init ──
checkAPI();
loadVaultData();
setInterval(checkAPI, 60000);
NEW_WRITE_12345  
