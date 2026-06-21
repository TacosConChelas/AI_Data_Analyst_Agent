/* ─────────────────────────────────────────────────────────
   AI Data Analyst – Frontend JavaScript
   ───────────────────────────────────────────────────────── */

const API = window.location.origin;
let isLoading  = false;
let activeFile = null;
const fileCache = {};   // filename → info object

// Configure marked.js
if (typeof marked !== 'undefined') {
  marked.setOptions({ breaks: true, gfm: true });
}

// ─────────────────────────────────────────────────────────
// TABS
// ─────────────────────────────────────────────────────────
let activeTab = 'chat';

function switchTab(tab) {
  activeTab = tab;
  document.getElementById('tabChat').classList.toggle('active', tab === 'chat');
  document.getElementById('tabData').classList.toggle('active', tab === 'data');

  const chatArea  = document.getElementById('chatArea');
  const inputArea = document.querySelector('.input-area');
  const dataView  = document.getElementById('dataView');
  const clearBtn  = document.getElementById('btnClearChat');

  if (tab === 'chat') {
    chatArea.style.display  = '';
    inputArea.style.display = '';
    dataView.style.display  = 'none';
    clearBtn.style.display  = '';
  } else {
    chatArea.style.display  = 'none';
    inputArea.style.display = 'none';
    dataView.style.display  = 'flex';
    clearBtn.style.display  = 'none';
    if (activeFile) loadDataTable();
  }
}

// ─────────────────────────────────────────────────────────
// DATA TABLE
// ─────────────────────────────────────────────────────────
let _searchTimer = null;

function debounceSearch() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(loadDataTable, 400);
  const val = document.getElementById('dataSearch').value;
  document.getElementById('btnClearSearch').hidden = !val;
}

function clearSearch() {
  document.getElementById('dataSearch').value = '';
  document.getElementById('btnClearSearch').hidden = true;
  loadDataTable();
}

async function loadDataTable() {
  if (!activeFile) return;
  const search = document.getElementById('dataSearch').value.trim();
  const wrap   = document.getElementById('dataTableWrap');

  wrap.innerHTML = '<div class="data-placeholder"><p>Cargando…</p></div>';
  document.getElementById('dataCount').textContent = '';

  try {
    const params = new URLSearchParams({ search });
    const res    = await fetch(`${API}/api/data/${encodeURIComponent(activeFile)}?${params}`);
    const data   = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error al cargar datos');

    renderDataTable(data, wrap);

    const countTxt = search
      ? `Mostrando ${data.showing} de ${data.matched} coincidencias (total: ${data.total.toLocaleString()} filas)`
      : `Mostrando ${data.showing} de ${data.total.toLocaleString()} filas · ${data.columns.length} columnas`;
    document.getElementById('dataCount').textContent = countTxt;

  } catch (err) {
    wrap.innerHTML = `<div class="data-placeholder"><p style="color:var(--red)">Error: ${escHtml(err.message)}</p></div>`;
  }
}

function renderDataTable(data, container) {
  if (!data.data.length) {
    container.innerHTML = '<div class="data-placeholder"><p>Sin resultados.</p></div>';
    return;
  }

  const headers = `<th class="row-num">#</th>` +
    data.columns.map(c => `<th title="${escHtml(String(c))}">${escHtml(String(c))}</th>`).join('');

  const rows = data.data.map((row, i) => {
    const cells = data.columns.map(col => {
      const val = row[col] ?? '';
      return `<td title="${escHtml(String(val))}">${escHtml(String(val))}</td>`;
    }).join('');
    return `<tr><td class="row-num">${i + 1}</td>${cells}</tr>`;
  }).join('');

  container.innerHTML = `
    <table class="full-table">
      <thead><tr>${headers}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ─────────────────────────────────────────────────────────
// FILE UPLOAD
// ─────────────────────────────────────────────────────────
const uploadArea = document.getElementById('uploadArea');
const fileInput  = document.getElementById('fileInput');

['dragover', 'dragenter'].forEach(e =>
  uploadArea.addEventListener(e, ev => { ev.preventDefault(); uploadArea.classList.add('drag-active'); })
);
['dragleave', 'dragend', 'drop'].forEach(e =>
  uploadArea.addEventListener(e, ev => { ev.preventDefault(); uploadArea.classList.remove('drag-active'); })
);
uploadArea.addEventListener('drop', ev => {
  const f = ev.dataTransfer.files[0];
  if (f) handleUpload(f);
});
uploadArea.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', ev => {
  if (ev.target.files[0]) handleUpload(ev.target.files[0]);
  fileInput.value = '';
});

async function handleUpload(file) {
  const ext = '.' + file.name.split('.').pop().toLowerCase();
  if (!['.csv', '.xlsx', '.xls'].includes(ext)) {
    showToast('Only CSV and Excel files are supported.', 'error');
    return;
  }
  showProgress();

  const form = new FormData();
  form.append('file', file);

  try {
    const res  = await fetch(`${API}/api/upload`, { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload failed');

    activeFile = data.filename;
    fileCache[data.filename] = data;

    hideProgress();
    updateFilesList(data);
    updateDataInfo(data);
    updateHeader(data.filename);
    hideWelcome();
    appendSystemMsg(`✅ <strong>${escHtml(data.filename)}</strong> loaded — ${data.rows.toLocaleString()} rows, ${data.columns.length} columns`);
    showToast(`${data.filename} loaded!`, 'success');
    if (activeTab === 'data') loadDataTable();
  } catch (err) {
    hideProgress();
    showToast(err.message, 'error');
  }
}

// ─────────────────────────────────────────────────────────
// SIDEBAR UPDATES
// ─────────────────────────────────────────────────────────
function updateFilesList(data) {
  const list = document.getElementById('filesList');
  const noFiles = list.querySelector('.no-files');
  if (noFiles) noFiles.remove();

  const id = 'file-' + data.filename.replace(/[^a-zA-Z0-9]/g, '-');
  let item = document.getElementById(id);
  if (!item) {
    item = document.createElement('div');
    item.className = 'file-item';
    item.id = id;
    item.addEventListener('click', () => switchFile(data.filename));
    list.appendChild(item);
  }

  const icon = data.filename.endsWith('.csv') ? '📄' : '📊';
  item.innerHTML = `
    <div class="file-icon">${icon}</div>
    <div class="file-details">
      <div class="file-name" title="${escHtml(data.filename)}">${escHtml(data.filename)}</div>
      <div class="file-meta">${data.rows.toLocaleString()} rows · ${data.columns.length} cols</div>
    </div>
    <div class="file-active-dot"></div>
    <button class="btn-delete-file" title="Eliminar archivo"
      onclick="event.stopPropagation(); deleteFile('${escHtml(data.filename)}')">✕</button>
  `;
  markActiveFile(data.filename);
}

function markActiveFile(filename) {
  document.querySelectorAll('.file-item').forEach(el => el.classList.remove('active'));
  const id = 'file-' + filename.replace(/[^a-zA-Z0-9]/g, '-');
  const el = document.getElementById(id);
  if (el) el.classList.add('active');
}

function updateDataInfo(data) {
  const wrap = document.getElementById('dataInfo');
  const grid = document.getElementById('infoGrid');
  wrap.removeAttribute('hidden');

  const cols = data.columns.slice(0, 12)
    .map(c => `<span class="column-tag">${escHtml(c)}</span>`).join('');
  const more = data.columns.length > 12
    ? `<span class="column-tag" style="color:var(--text-3)">+${data.columns.length - 12} more</span>` : '';

  grid.innerHTML = `
    <div class="info-item">
      <div class="info-label">Rows</div>
      <span class="info-value">${data.rows.toLocaleString()}</span>
    </div>
    <div class="info-item">
      <div class="info-label">Columns</div>
      <span class="info-value">${data.columns.length}</span>
    </div>
    <div class="info-item full">
      <div class="info-label">Columns</div>
      <div class="columns-list">${cols}${more}</div>
    </div>
  `;
}

function updateHeader(filename) {
  document.getElementById('activeFileTitle').textContent = filename;
  const dot = document.getElementById('statusDot');
  dot.classList.add('active');
}

async function deleteFile(filename) {
  if (!confirm(`¿Eliminar "${filename}" del proyecto?`)) return;
  try {
    const res  = await fetch(`${API}/api/files/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Error al eliminar');

    // Remove from UI and cache
    delete fileCache[filename];
    const id = 'file-' + filename.replace(/[^a-zA-Z0-9]/g, '-');
    document.getElementById(id)?.remove();

    // Show "no files" if list is empty
    const list = document.getElementById('filesList');
    if (!list.querySelector('.file-item')) {
      list.innerHTML = '<p class="no-files">No files loaded</p>';
      document.getElementById('dataInfo').setAttribute('hidden', '');
      document.getElementById('activeFileTitle').textContent = 'No file selected';
      document.getElementById('statusDot').classList.remove('active');
      document.getElementById('welcomeScreen').style.display = '';
      activeFile = null;
    } else if (data.active_file) {
      // Switch UI to new active file
      activeFile = data.active_file;
      markActiveFile(activeFile);
      updateHeader(activeFile);
      if (fileCache[activeFile]) updateDataInfo(fileCache[activeFile]);
    }

    showToast(`"${filename}" eliminado`, 'info');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function switchFile(filename) {
  if (filename === activeFile) return;
  try {
    await fetch(`${API}/api/switch-file/${encodeURIComponent(filename)}`, { method: 'POST' });
    activeFile = filename;
    markActiveFile(filename);
    if (fileCache[filename]) {
      updateDataInfo(fileCache[filename]);
      updateHeader(filename);
    }
    appendSystemMsg(`Switched to <strong>${escHtml(filename)}</strong>`);
  } catch (e) {
    showToast('Failed to switch file.', 'error');
  }
}

// ─────────────────────────────────────────────────────────
// CHAT
// ─────────────────────────────────────────────────────────
async function sendMessage() {
  if (isLoading) return;
  const input = document.getElementById('messageInput');
  const text  = input.value.trim();
  if (!text) return;
  if (!activeFile) { showToast('Upload a file first.', 'error'); return; }

  input.value = '';
  autoResize(input);
  hideWelcome();
  appendUserMsg(text);

  isLoading = true;
  setSendLoading(true);
  const thinking = appendThinking();

  try {
    const res  = await fetch(`${API}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, file: activeFile }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');

    thinking.remove();
    appendAIMsg(data);
  } catch (err) {
    thinking.remove();
    appendErrorMsg(err.message);
  } finally {
    isLoading = false;
    setSendLoading(false);
  }
}

// ─────────────────────────────────────────────────────────
// MESSAGE RENDERERS
// ─────────────────────────────────────────────────────────
function appendUserMsg(text) {
  const el = makeEl('div', 'message user-message');
  el.innerHTML = `
    <div class="message-bubble">
      <div class="message-text">${escHtml(text)}</div>
    </div>
    <div class="message-avatar user-avatar">You</div>
  `;
  getContainer().appendChild(el);
  scrollBottom();
}

function appendThinking() {
  const el = makeEl('div', 'message assistant-message');
  el.innerHTML = `
    <div class="message-avatar ai-avatar">AI</div>
    <div class="message-bubble">
      <div class="thinking-dots"><span></span><span></span><span></span></div>
      <span class="thinking-text">Analyzing…</span>
    </div>
  `;
  getContainer().appendChild(el);
  scrollBottom();
  return el;
}

function appendAIMsg(data) {
  const el = makeEl('div', 'message assistant-message');
  let inner = '';

  // Main content – prefer full_text if available, else render sections
  if (data.full_text) {
    inner += `<div class="message-text markdown-content">${renderMd(data.full_text)}</div>`;
  } else {
    inner += buildSections(data);
  }

  // Tables (derived tables produced by create_derived_table)
  if (data.tables && data.tables.length) {
    inner += '<div class="tables-container">';
    for (const tbl of data.tables) inner += renderTable(tbl);
    inner += '</div>';
  }

  // Charts
  if (data.charts && data.charts.length) {
    inner += '<div class="charts-container">';
    for (const fn of data.charts) {
      const src = `${API}/api/charts/${fn}`;
      inner += `
        <div class="chart-wrapper">
          <img src="${src}" alt="Chart" class="chart-image" onclick="openModal('${src}')" loading="lazy" />
          <div class="chart-actions">
            <a href="${src}" download="${fn}" class="btn-download">⬇ Download</a>
          </div>
        </div>`;
    }
    inner += '</div>';
  }

  el.innerHTML = `
    <div class="message-avatar ai-avatar">AI</div>
    <div class="message-bubble">${inner}</div>
  `;
  getContainer().appendChild(el);
  scrollBottom();
}

function appendErrorMsg(msg) {
  const el = makeEl('div', 'message assistant-message');
  el.innerHTML = `
    <div class="message-avatar ai-avatar error-avatar">!</div>
    <div class="message-bubble error"><strong>Error:</strong> ${escHtml(msg)}</div>
  `;
  getContainer().appendChild(el);
  scrollBottom();
}

function appendSystemMsg(html) {
  const el = makeEl('div', 'message system-message');
  el.innerHTML = `
    <div class="message-avatar ai-avatar" style="opacity:.5;font-size:10px">SYS</div>
    <div class="message-bubble system-message">${html}</div>
  `;
  getContainer().appendChild(el);
  scrollBottom();
}

// ─────────────────────────────────────────────────────────
// CONTENT BUILDERS
// ─────────────────────────────────────────────────────────
function buildSections(data) {
  let html = '<div class="response-sections">';
  if (data.answer) {
    html += `
      <div class="response-section answer-section">
        <div class="section-label">Answer</div>
        <div class="section-content markdown-content">${renderMd(data.answer)}</div>
      </div>`;
  }
  if (data.logic) {
    html += `
      <div class="response-section logic-section">
        <div class="section-label">Computation / Logic</div>
        <div class="section-content markdown-content">${renderMd(data.logic)}</div>
      </div>`;
  }
  if (data.insight) {
    html += `
      <div class="response-section insight-section">
        <div class="section-label">Insight</div>
        <div class="section-content markdown-content">${renderMd(data.insight)}</div>
      </div>`;
  }
  html += '</div>';
  return html;
}

function renderTable(tbl) {
  if (!tbl.columns || !tbl.data) return '';
  const headers = tbl.columns.map(c => `<th>${escHtml(String(c))}</th>`).join('');
  const rows = tbl.data.map(row =>
    '<tr>' + tbl.columns.map(c => `<td>${escHtml(String(row[c] ?? ''))}</td>`).join('') + '</tr>'
  ).join('');
  return `
    <div class="table-wrapper">
      <div class="table-header">
        <span class="table-name">${escHtml(tbl.name || 'Table')}</span>
        <span class="table-size">${tbl.data.length} rows · ${tbl.columns.length} cols</span>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead><tr>${headers}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
}

// ─────────────────────────────────────────────────────────
// MODAL
// ─────────────────────────────────────────────────────────
function openModal(src) {
  document.getElementById('modalImage').src = src;
  document.getElementById('imageModal').classList.add('active');
  document.body.style.overflow = 'hidden';
}
function closeModal() {
  document.getElementById('imageModal').classList.remove('active');
  document.body.style.overflow = '';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// ─────────────────────────────────────────────────────────
// PROGRESS
// ─────────────────────────────────────────────────────────
let _progressInterval = null;
function showProgress() {
  document.getElementById('uploadProgress').removeAttribute('hidden');
  let pct = 0;
  clearInterval(_progressInterval);
  const labels = ['Reading file…', 'Parsing schema…', 'Building index…', 'Finalising…'];
  _progressInterval = setInterval(() => {
    pct = Math.min(pct + Math.random() * 12, 90);
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressText').textContent = labels[Math.floor(pct / 25)] || 'Processing…';
  }, 250);
}
function hideProgress() {
  clearInterval(_progressInterval);
  document.getElementById('progressFill').style.width = '100%';
  setTimeout(() => document.getElementById('uploadProgress').setAttribute('hidden', ''), 600);
}

// ─────────────────────────────────────────────────────────
// TOAST
// ─────────────────────────────────────────────────────────
let _toastTimer = null;
function showToast(msg, type = 'info') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = `toast ${type} show`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove('show'), 3500);
}

// ─────────────────────────────────────────────────────────
// UTILS
// ─────────────────────────────────────────────────────────
function handleKeyDown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}
function scrollBottom() {
  const area = document.getElementById('chatArea');
  area.scrollTop = area.scrollHeight;
}
function hideWelcome() {
  const w = document.getElementById('welcomeScreen');
  if (w) w.style.display = 'none';
}
function setSendLoading(on) {
  const btn = document.getElementById('sendBtn');
  btn.disabled = on;
  document.getElementById('sendIcon').outerHTML = on
    ? '<div id="sendIcon" class="spinner"></div>'
    : '<span id="sendIcon">➤</span>';
}
function clearChat() {
  if (!confirm('Clear chat history?')) return;
  getContainer().innerHTML = '';
  if (activeFile && fileCache[activeFile]) appendSystemMsg(`Active file: <strong>${escHtml(activeFile)}</strong>`);
}
function useSuggestion(el) {
  const input = document.getElementById('messageInput');
  input.value = el.textContent;
  autoResize(input);
  input.focus();
}
function getContainer() { return document.getElementById('messagesContainer'); }
function makeEl(tag, cls) {
  const el = document.createElement(tag);
  el.className = cls;
  return el;
}
function escHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}
function renderMd(text) {
  if (!text) return '';
  let html;
  if (typeof marked !== 'undefined') {
    try { html = marked.parse(text); } catch(e) { /* fall through */ }
  }
  if (!html) {
    html = text
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/`(.*?)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br>');
  }
  // Strip any markdown-rendered tables — tables belong only in the Data Table tab
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  tmp.querySelectorAll('table').forEach(t => t.remove());
  return tmp.innerHTML;
}

// ─────────────────────────────────────────────────────────
// INIT – restore state if server already has files loaded
// ─────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  try {
    const res  = await fetch(`${API}/api/files`);
    const data = await res.json();
    if (data.files && data.files.length > 0) {
      for (const f of data.files) {
        fileCache[f.filename] = f;
        updateFilesList(f);
        if (f.active) {
          activeFile = f.filename;
          updateHeader(f.filename);
          const fake = { filename: f.filename, rows: f.rows, columns: Array(f.columns).fill(''), dtypes: {} };
          updateDataInfo({ ...fake, columns: Array(f.columns).fill('…') });
        }
      }
      if (activeFile) hideWelcome();
    }
  } catch (_) { /* server not ready yet */ }
});
