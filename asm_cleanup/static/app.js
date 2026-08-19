// Auth token storage key
const TOKEN_KEY = 'asm_cleanup_token';

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// State Management
const state = {
    targets: [],
    scans: [],
    selectedTargetId: null,
    selectedScanId: null,
    selectedScanTargetName: null,
    sqlScriptsByDatabase: {},
    activeSqlDatabase: null,
    pollIntervalId: null
};

// UI Elements
const els = {
    viewLogin: document.getElementById('view-login'),
    appContainer: document.getElementById('app-container'),
    loginForm: document.getElementById('login-form'),
    loginPassword: document.getElementById('login-password'),
    loginError: document.getElementById('login-error'),
    btnLogout: document.getElementById('btn-logout'),

    targetList: document.getElementById('target-list'),
    scanHistoryList: document.getElementById('scan-history-list'),
    btnAddTarget: document.getElementById('btn-add-target'),
    btnCancelForm: document.getElementById('btn-cancel-form'),
    btnDeleteTarget: document.getElementById('btn-delete-target'),
    btnCopySql: document.getElementById('btn-copy-sql'),
    btnDownloadSql: document.getElementById('btn-download-sql'),
    targetConfigForm: document.getElementById('target-config-form'),
    
    // Views
    viewWelcome: document.getElementById('view-welcome'),
    viewTargetForm: document.getElementById('view-target-form'),
    viewScanDetails: document.getElementById('view-scan-details'),
    
    // Form fields
    formTitle: document.getElementById('form-title'),
    formId: document.getElementById('form-target-id'),
    formName: document.getElementById('form-name'),
    formHost: document.getElementById('form-host'),
    formUser: document.getElementById('form-user'),
    formSshKeyPath: document.getElementById('form-ssh-key-path'),
    formSshKeyContent: document.getElementById('form-ssh-key-content'),
    formGridHome: document.getElementById('form-grid-home'),
    formOracleSid: document.getElementById('form-oracle-sid'),
    formDestDg: document.getElementById('form-dest-dg'),
    formMoveOnline: document.getElementById('form-move-online'),
    
    // Details view fields
    detailTargetName: document.getElementById('detail-target-name'),
    scanStatusBadge: document.getElementById('scan-status-badge'),
    progressBanner: document.getElementById('scan-progress-banner'),
    progressLabel: document.getElementById('scan-progress-label'),
    progressMessage: document.getElementById('scan-progress-message'),
    errorBanner: document.getElementById('scan-error-banner'),
    errorMessage: document.getElementById('scan-error-message'),
    
    // Details Metadata
    metaGridHome: document.getElementById('meta-grid-home'),
    metaDiskGroups: document.getElementById('meta-disk-groups'),
    metaDatabasesList: document.getElementById('meta-databases-list'),
    
    // Alias Table and SQL
    aliasCountBadge: document.getElementById('alias-count-badge'),
    aliasTableBody: document.getElementById('alias-table-body'),
    sqlDbTabs: document.getElementById('sql-db-tabs'),
    generatedSqlCode: document.getElementById('generated-sql-code'),
    
    toast: document.getElementById('toast'),

    // Modal Elements
    dbDetailsModal: document.getElementById('db-details-modal'),
    modalDbTitle: document.getElementById('modal-db-title'),
    modalDbSid: document.getElementById('modal-db-sid'),
    modalDbHome: document.getElementById('modal-db-home'),
    modalDbDest: document.getElementById('modal-db-dest'),
    modalDbRecovery: document.getElementById('modal-db-recovery'),
    modalPdbsList: document.getElementById('modal-pdbs-list'),
    btnCloseModal: document.getElementById('btn-close-modal')
};

// --- Auth helpers ---
function getToken() {
    return sessionStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
    sessionStorage.setItem(TOKEN_KEY, token);
}

function clearToken() {
    sessionStorage.removeItem(TOKEN_KEY);
}

function showLogin() {
    stopPolling();
    els.viewLogin.classList.remove('hide');
    els.appContainer.classList.add('hide');
    if (els.loginPassword) {
        els.loginPassword.value = '';
        els.loginPassword.focus();
    }
    if (els.loginError) {
        els.loginError.classList.add('hide');
        els.loginError.textContent = '';
    }
}

function showApp() {
    els.viewLogin.classList.add('hide');
    els.appContainer.classList.remove('hide');
}

async function apiFetch(url, options = {}) {
    const token = getToken();
    const headers = new Headers(options.headers || {});
    if (token) {
        headers.set('Authorization', `Bearer ${token}`);
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) {
        clearToken();
        showLogin();
        throw new Error('Session expired. Please sign in again.');
    }
    return response;
}

async function handleLogin(e) {
    e.preventDefault();
    els.loginError.classList.add('hide');
    els.loginError.textContent = '';

    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: els.loginPassword.value })
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.detail || 'Invalid password');
        }
        const data = await response.json();
        setToken(data.access_token);
        showApp();
        await fetchTargets();
        await fetchScans();
    } catch (err) {
        els.loginError.textContent = err.message;
        els.loginError.classList.remove('hide');
    }
}

function handleLogout() {
    clearToken();
    showLogin();
}

// --- Notifications ---
function showToast(message, isError = false) {
    els.toast.textContent = message;
    els.toast.style.borderColor = isError ? 'var(--color-danger)' : 'var(--color-primary)';
    els.toast.style.boxShadow = isError ? '0 4px 20px rgba(255, 71, 87, 0.25)' : '0 4px 20px rgba(0, 198, 255, 0.25)';
    els.toast.classList.remove('hide');
    setTimeout(() => {
        els.toast.classList.add('hide');
    }, 4000);
}

// --- Views Routing ---
function showView(viewId) {
    els.viewWelcome.classList.remove('active');
    els.viewTargetForm.classList.remove('active');
    els.viewScanDetails.classList.remove('active');
    
    document.getElementById(viewId).classList.add('active');
}

// --- Load targets and scans lists ---
async function fetchTargets() {
    try {
        const response = await apiFetch('/api/targets');
        if (!response.ok) throw new Error("Failed to load targets.");
        state.targets = await response.json();
        renderTargetsList();
    } catch (err) {
        showToast(err.message, true);
    }
}

async function fetchScans() {
    try {
        const response = await apiFetch('/api/scans');
        if (!response.ok) throw new Error("Failed to load scan history.");
        state.scans = await response.json();
        renderScanHistory();
    } catch (err) {
        showToast(err.message, true);
    }
}

// --- Render Sidebar Items ---
function renderTargetsList() {
    els.targetList.innerHTML = '';
    state.targets.forEach(target => {
        const li = document.createElement('li');
        li.className = `target-item ${state.selectedTargetId === target.id ? 'active' : ''}`;
        
        li.innerHTML = `
            <div class="name">${escapeHtml(target.name)}</div>
            <div class="host">${escapeHtml(target.user)}@${escapeHtml(target.host)}</div>
        `;
        
        li.addEventListener('click', () => {
            selectTarget(target.id);
        });
        els.targetList.appendChild(li);
    });
}

function renderScanHistory() {
    els.scanHistoryList.innerHTML = '';
    state.scans.forEach(scan => {
        const li = document.createElement('li');
        li.className = `scan-item ${state.selectedScanId === scan.id ? 'active' : ''}`;
        
        const dateStr = new Date(scan.created_at).toLocaleString();
        const statusBadgeClass = `badge-${scan.status}`;
        const isActive = scan.status === 'pending' || scan.status === 'running';
        const spinner = isActive ? '<span class="scan-item-spinner" aria-hidden="true"></span>' : '';
        
        li.innerHTML = `
            <div class="target-name" style="font-weight:600;font-size:0.85rem;">${escapeHtml(scan.target_name)}</div>
            <div class="scan-meta">
                <span class="time">${escapeHtml(dateStr)}</span>
                <span class="badge ${statusBadgeClass}" style="padding:2px 6px; font-size:0.65rem;">${spinner}${escapeHtml(scan.status.toUpperCase())}</span>
            </div>
        `;
        
        li.addEventListener('click', () => {
            selectScan(scan.id);
        });
        els.scanHistoryList.appendChild(li);
    });
}

function updateProgressBanner(scan) {
    const isActive = scan.status === 'pending' || scan.status === 'running';
    if (!isActive) {
        els.progressBanner.classList.add('hide');
        return;
    }

    els.progressBanner.classList.remove('hide');
    els.errorBanner.classList.add('hide');
    els.progressLabel.textContent =
        scan.status === 'pending' ? 'Scan queued' : 'Scan in progress';
    els.progressMessage.textContent =
        scan.progress_message ||
        'Executing target discovery and ASM walks over SSH...';
}

// --- Target Management Actions ---
function selectTarget(targetId) {
    state.selectedTargetId = targetId;
    state.selectedScanId = null;
    renderTargetsList();
    renderScanHistory();
    
    // Stop any active scan polling
    stopPolling();
    
    // Find target
    const target = state.targets.find(t => t.id === targetId);
    if (!target) return;
    
    // Show Target view
    showView('view-scan-details');
    els.detailTargetName.textContent = target.name;
    els.scanStatusBadge.className = 'badge badge-pending';
    els.scanStatusBadge.textContent = 'READY TO DISCOVER';
    
    els.progressBanner.classList.add('hide');
    els.errorBanner.classList.add('hide');
    
    // Clear meta details
    els.metaGridHome.textContent = target.grid_home || 'Discovered automatically';
    els.metaDiskGroups.textContent = '--';
    els.metaDatabasesList.innerHTML = `
        <div class="text-center" style="color:var(--text-muted);font-size:0.85rem;padding:12px;">
            Target has not been scanned yet.
        </div>
    `;
    els.aliasCountBadge.textContent = '0 found';
    els.aliasTableBody.innerHTML = `
        <tr>
            <td colspan="5" class="text-center">Click the "Trigger Discovery Scan" button to gather target metadata.</td>
        </tr>
    `;
    els.generatedSqlCode.textContent = '-- SQL scripts will generate after scanning';

    // Add Trigger Discovery Scan actions
    // Check if target has previous scans to display
    const prevScan = state.scans.find(s => s.target_id === targetId);
    
    const detailHeader = els.detailTargetName.parentElement;
    // Clean up existing scan button
    const oldBtn = document.getElementById('btn-trigger-active-scan');
    const oldEditBtn = document.getElementById('btn-edit-active-target');
    if (oldBtn) oldBtn.remove();
    if (oldEditBtn) oldEditBtn.remove();
    
    const btnScan = document.createElement('button');
    btnScan.id = 'btn-trigger-active-scan';
    btnScan.className = 'btn btn-primary btn-sm';
    btnScan.style.marginTop = '8px';
    btnScan.textContent = '🔍 Trigger Discovery Scan';
    btnScan.addEventListener('click', () => triggerTargetScan(targetId));
    
    const btnEdit = document.createElement('button');
    btnEdit.id = 'btn-edit-active-target';
    btnEdit.className = 'btn btn-secondary btn-sm';
    btnEdit.style.marginTop = '8px';
    btnEdit.style.marginLeft = '8px';
    btnEdit.textContent = '⚙️ Edit Connection';
    btnEdit.addEventListener('click', () => openEditTargetForm(target));

    detailHeader.appendChild(btnScan);
    detailHeader.appendChild(btnEdit);
    
    if (prevScan) {
        // Load the latest scan details automatically
        selectScan(prevScan.id);
    }
}

function openAddTargetForm() {
    state.selectedTargetId = null;
    renderTargetsList();
    
    els.formTitle.textContent = 'Configure New Connection';
    els.formId.value = '';
    els.formName.value = '';
    els.formHost.value = '';
    els.formUser.value = 'oracle';
    els.formSshKeyPath.value = '';
    els.formSshKeyContent.value = '';
    els.formSshKeyContent.placeholder =
        'Paste -----BEGIN OPENSSH PRIVATE KEY----- block here...';
    els.formGridHome.value = '';
    els.formOracleSid.value = '';
    els.formDestDg.value = '+DATA';
    els.formMoveOnline.checked = false;
    
    els.btnDeleteTarget.classList.add('hide');
    showView('view-target-form');
}

function openEditTargetForm(target) {
    els.formTitle.textContent = `Edit Connection Profile: ${target.name}`;
    els.formId.value = target.id;
    els.formName.value = target.name;
    els.formHost.value = target.host;
    els.formUser.value = target.user;
    els.formSshKeyPath.value = target.ssh_key_path || '';
    // Never echo stored private key material; leave blank to keep existing.
    els.formSshKeyContent.value = '';
    els.formSshKeyContent.placeholder = target.has_ssh_key
        ? 'Key on file — leave blank to keep, or paste a new key to replace'
        : 'Paste -----BEGIN OPENSSH PRIVATE KEY----- block here...';
    els.formGridHome.value = target.grid_home || '';
    els.formOracleSid.value = target.oracle_sid || '';
    els.formDestDg.value = target.destination_disk_group || '+DATA';
    els.formMoveOnline.checked = Boolean(target.move_online);
    
    els.btnDeleteTarget.classList.remove('hide');
    showView('view-target-form');
}

// --- Form submission handler ---
async function saveTargetForm(e) {
    e.preventDefault();
    const id = els.formId.value;
    
    const payload = {
        name: els.formName.value,
        host: els.formHost.value,
        user: els.formUser.value,
        ssh_key_path: els.formSshKeyPath.value || null,
        ssh_key_content: els.formSshKeyContent.value || null,
        grid_home: els.formGridHome.value || null,
        oracle_sid: els.formOracleSid.value || null,
        destination_disk_group: els.formDestDg.value,
        move_online: els.formMoveOnline.checked
    };
    
    try {
        let response;
        if (id) {
            // Edit
            response = await apiFetch(`/api/targets/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } else {
            // Create
            response = await apiFetch('/api/targets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Failed to save profile.");
        }
        
        showToast("Connection profile saved successfully.");
        await fetchTargets();
        
        if (id) {
            selectTarget(parseInt(id));
        } else {
            // Select newly added target
            const newTarget = state.targets.find(t => t.name === payload.name);
            if (newTarget) selectTarget(newTarget.id);
            else showView('view-welcome');
        }
    } catch (err) {
        showToast(err.message, true);
    }
}

async function deleteSelectedTarget() {
    const id = els.formId.value;
    if (!id || !confirm("Are you sure you want to delete this connection profile? Any scan history will also be deleted.")) return;
    
    try {
        const response = await apiFetch(`/api/targets/${id}`, {
            method: 'DELETE'
        });
        if (!response.ok) throw new Error("Failed to delete profile.");
        
        showToast("Profile deleted.");
        state.selectedTargetId = null;
        await fetchTargets();
        await fetchScans();
        showView('view-welcome');
    } catch (err) {
        showToast(err.message, true);
    }
}

// --- Discovery Scan Operations ---
async function triggerTargetScan(targetId) {
    try {
        updateProgressBanner({
            status: 'pending',
            progress_message: 'Queuing discovery scan...'
        });
        
        const response = await apiFetch(`/api/targets/${targetId}/scan`, {
            method: 'POST'
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.detail || "Failed to queue discovery scan.");
        }
        
        const data = await response.json();
        showToast("Discovery scan successfully queued.");
        
        // Refresh lists
        await fetchScans();
        selectScan(data.scan_id);
    } catch (err) {
        els.progressBanner.classList.add('hide');
        showToast(err.message, true);
    }
}

function selectScan(scanId) {
    state.selectedScanId = scanId;
    renderScanHistory();
    
    stopPolling();
    loadScanDetails(scanId);
}

// --- Fetch and Render Scan Details ---
async function loadScanDetails(scanId) {
    try {
        const response = await apiFetch(`/api/scans/${scanId}`);
        if (!response.ok) throw new Error("Failed to fetch scan details.");
        
        const scan = await response.json();
        
        // Update view title and select target (without triggering detail refresh)
        state.selectedTargetId = scan.target_id;
        state.selectedScanTargetName = scan.target_name;
        renderTargetsList();
        
        showView('view-scan-details');
        els.detailTargetName.textContent = scan.target_name;
        
        // Update header scan triggers
        const detailHeader = els.detailTargetName.parentElement;
        const oldBtn = document.getElementById('btn-trigger-active-scan');
        const oldEditBtn = document.getElementById('btn-edit-active-target');
        if (oldBtn) oldBtn.remove();
        if (oldEditBtn) oldEditBtn.remove();
        
        const btnScan = document.createElement('button');
        btnScan.id = 'btn-trigger-active-scan';
        btnScan.className = 'btn btn-primary btn-sm';
        btnScan.style.marginTop = '8px';
        btnScan.textContent = '🔍 Trigger Discovery Scan';
        btnScan.addEventListener('click', () => triggerTargetScan(scan.target_id));
        
        const target = state.targets.find(t => t.id === scan.target_id);
        const btnEdit = document.createElement('button');
        btnEdit.id = 'btn-edit-active-target';
        btnEdit.className = 'btn btn-secondary btn-sm';
        btnEdit.style.marginTop = '8px';
        btnEdit.style.marginLeft = '8px';
        btnEdit.textContent = '⚙️ Edit Connection';
        if (target) {
            btnEdit.addEventListener('click', () => openEditTargetForm(target));
        } else {
            btnEdit.disabled = true;
        }

        detailHeader.appendChild(btnScan);
        detailHeader.appendChild(btnEdit);
        
        // Status badge
        els.scanStatusBadge.className = `badge badge-${scan.status}`;
        els.scanStatusBadge.textContent = scan.status.toUpperCase();
        
        // Handle pending / running status (Poll details)
        updateProgressBanner(scan);
        if (scan.status === 'pending' || scan.status === 'running') {
            startPolling(scanId);
        }
        
        if (scan.status === 'failed') {
            els.errorBanner.classList.remove('hide');
            els.errorMessage.textContent = scan.error_message || 'Unknown runner error.';
        } else if (scan.status !== 'pending' && scan.status !== 'running') {
            els.errorBanner.classList.add('hide');
        }
        
        // Render discovered metadata
        els.metaGridHome.textContent = scan.grid_home || '--';
        els.metaDiskGroups.textContent = scan.disk_groups.length > 0 ? scan.disk_groups.join(', ') : '--';
        
        // Render database properties
        renderDiscoveredDatabases(scan.databases);
        
        // Render alias list
        renderDiscoveredAliases(scan.aliases);
        
        // Render generated SQL code (per-database tabs when multiple DBs)
        renderGeneratedSql(scan);
        
    } catch (err) {
        showToast(err.message, true);
    }
}

function renderDiscoveredDatabases(dbs) {
    els.metaDatabasesList.innerHTML = '';
    const dbNames = Object.keys(dbs);
    
    if (dbNames.length === 0) {
        els.metaDatabasesList.innerHTML = `<div style="color:var(--text-muted);font-size:0.85rem;">No active database instances discovered.</div>`;
        return;
    }
    
    dbNames.forEach(name => {
        const db = dbs[name];
        const item = document.createElement('div');
        item.className = 'meta-db-item interactive';
        
        const params = db.parameters || {};
        const dest = params.db_create_file_dest || '--';
        const recovery = params.db_recovery_file_dest || '--';
        
        item.innerHTML = `
            <div class="db-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;">
                <span>🖴 ${escapeHtml(name.toUpperCase())} (SID: ${escapeHtml(db.oracle_sid)})</span>
                <span style="font-size:0.7rem; color:var(--color-primary); font-weight:600; text-transform:uppercase; letter-spacing:0.05em;">Details ➜</span>
            </div>
            <div style="color:var(--text-secondary);font-size:0.75rem;margin-bottom:4px;">Home: <span style="font-family:monospace;color:var(--text-primary);">${escapeHtml(db.oracle_home)}</span></div>
            <div style="display:flex;flex-direction:column;gap:2px;font-size:0.7rem;color:var(--text-muted);">
                <div>OMF Dest: <span style="font-family:monospace;color:var(--text-secondary);">${escapeHtml(dest)}</span></div>
                <div>FRA Dest: <span style="font-family:monospace;color:var(--text-secondary);">${escapeHtml(recovery)}</span></div>
                <div>PDBs Discovered: <span style="font-family:monospace;color:var(--text-secondary);">${escapeHtml(db.pdb_count || 0)}</span></div>
            </div>
        `;
        
        item.addEventListener('click', () => {
            showDatabaseDetailsModal(name, db);
        });
        
        els.metaDatabasesList.appendChild(item);
    });
}

function showDatabaseDetailsModal(name, db) {
    els.modalDbTitle.textContent = `Database: ${name.toUpperCase()}`;
    els.modalDbSid.textContent = db.oracle_sid || '--';
    els.modalDbHome.textContent = db.oracle_home || '--';
    
    const params = db.parameters || {};
    els.modalDbDest.textContent = params.db_create_file_dest || '--';
    els.modalDbRecovery.textContent = params.db_recovery_file_dest || '--';
    
    els.modalPdbsList.innerHTML = '';
    
    const pdbs = db.pdbs || [];
    if (pdbs.length === 0) {
        els.modalPdbsList.innerHTML = `<li style="color:var(--text-muted);font-size:0.85rem;padding:8px 0;">No Pluggable Databases (PDBs) found.</li>`;
    } else {
        pdbs.forEach(pdb => {
            const li = document.createElement('li');
            li.className = 'modal-pdb-item';
            li.innerHTML = `
                <div class="pdb-name">📦 ${escapeHtml(pdb.name)}</div>
                <div class="pdb-guid">GUID: ${escapeHtml(pdb.guid)}</div>
            `;
            els.modalPdbsList.appendChild(li);
        });
    }
    
    els.dbDetailsModal.showModal();
}


function renderDiscoveredAliases(aliases) {
    els.aliasCountBadge.textContent = `${aliases.length} found`;
    els.aliasTableBody.innerHTML = '';
    
    if (aliases.length === 0) {
        els.aliasTableBody.innerHTML = `
            <tr>
                <td colspan="5" class="text-center">No ASM alias files found requiring OMF moves. All storage paths look clean.</td>
            </tr>
        `;
        return;
    }
    
    aliases.forEach(alias => {
        const tr = document.createElement('tr');
        
        // Highlight PDB vs CDB
        const isPdb = alias.container_name !== 'CDB$ROOT';
        const conLabel = isPdb 
            ? `<span style="color:var(--color-primary);font-weight:600;">📦 ${escapeHtml(alias.container_name)}</span>`
            : `<span style="color:var(--text-secondary);">⚙️ ${escapeHtml(alias.container_name)}</span>`;
            
        tr.innerHTML = `
            <td><strong style="color:var(--text-primary);">${escapeHtml(alias.database_name.toUpperCase())}</strong></td>
            <td>${conLabel}</td>
            <td><span class="count-badge">${escapeHtml(alias.file_type)}</span></td>
            <td style="font-family:monospace;color:var(--color-accent);font-size:0.75rem;word-break:break-all;">${escapeHtml(alias.source_path)}</td>
            <td style="font-family:monospace;color:var(--text-secondary);font-size:0.75rem;word-break:break-all;">${escapeHtml(alias.target_path)}</td>
        `;
        els.aliasTableBody.appendChild(tr);
    });
}

// --- Polling Helpers ---
function startPolling(scanId) {
    stopPolling();
    state.pollIntervalId = setInterval(async () => {
        try {
            const response = await apiFetch(`/api/scans/${scanId}`);
            if (!response.ok) return;
            const scan = await response.json();

            els.scanStatusBadge.className = `badge badge-${scan.status}`;
            els.scanStatusBadge.textContent = scan.status.toUpperCase();
            updateProgressBanner(scan);

            // Keep sidebar status badges fresh while the scan runs
            const listed = state.scans.find(s => s.id === scanId);
            if (listed && listed.status !== scan.status) {
                listed.status = scan.status;
                listed.progress_message = scan.progress_message;
                renderScanHistory();
            } else if (listed) {
                listed.progress_message = scan.progress_message;
            }
            
            // Check status update
            if (scan.status === 'completed' || scan.status === 'failed') {
                stopPolling();
                showToast(`Scan execution run ${scan.status}!`);
                await fetchScans();
                loadScanDetails(scanId);
            }
        } catch (err) {
            console.error(err);
        }
    }, 1500);
}

function stopPolling() {
    if (state.pollIntervalId) {
        clearInterval(state.pollIntervalId);
        state.pollIntervalId = null;
    }
}

// --- Copy / Download SQL Utilities ---
function sanitizeFilenamePart(value) {
    const safe = String(value || '')
        .trim()
        .replace(/[^\w.\-]+/g, '_')
        .replace(/^_+|_+$/g, '');
    return safe;
}

function sqlDownloadFilename(connectionName, databaseName) {
    const connection = sanitizeFilenamePart(connectionName) || 'connection';
    const database = sanitizeFilenamePart(databaseName);
    if (database) {
        return `${connection}_${database}_asm_cleanup.sql`;
    }
    return `${connection}_asm_cleanup.sql`;
}

function activeGeneratedSql() {
    const byDb = state.sqlScriptsByDatabase || {};
    const names = Object.keys(byDb);
    if (state.activeSqlDatabase && byDb[state.activeSqlDatabase]) {
        return byDb[state.activeSqlDatabase];
    }
    if (names.length === 1) {
        return byDb[names[0]];
    }
    return els.generatedSqlCode.textContent || '';
}

function renderGeneratedSql(scan) {
    const byDatabase = scan.generated_sql_by_database || {};
    const dbNames = Object.keys(byDatabase);
    state.sqlScriptsByDatabase = byDatabase;

    if (dbNames.length === 0) {
        state.activeSqlDatabase = null;
        els.sqlDbTabs.classList.add('hide');
        els.sqlDbTabs.innerHTML = '';
        els.generatedSqlCode.textContent =
            scan.generated_sql || '-- SQL emitter returned empty script.';
        return;
    }

    if (!state.activeSqlDatabase || !byDatabase[state.activeSqlDatabase]) {
        state.activeSqlDatabase = dbNames[0];
    }

    if (dbNames.length > 1) {
        els.sqlDbTabs.classList.remove('hide');
        els.sqlDbTabs.innerHTML = '';
        dbNames.forEach((name) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = `sql-db-tab${name === state.activeSqlDatabase ? ' active' : ''}`;
            btn.setAttribute('role', 'tab');
            btn.setAttribute('aria-selected', name === state.activeSqlDatabase ? 'true' : 'false');
            btn.textContent = name.toUpperCase();
            btn.addEventListener('click', () => {
                state.activeSqlDatabase = name;
                renderGeneratedSql(scan);
            });
            els.sqlDbTabs.appendChild(btn);
        });
    } else {
        els.sqlDbTabs.classList.add('hide');
        els.sqlDbTabs.innerHTML = '';
    }

    els.generatedSqlCode.textContent = byDatabase[state.activeSqlDatabase];
}

function copySqlToClipboard() {
    const code = activeGeneratedSql();
    navigator.clipboard.writeText(code).then(() => {
        showToast("SQL fix script copied to clipboard!");
    }).catch(err => {
        showToast("Failed to copy script.", true);
    });
}

function downloadGeneratedSql() {
    const code = activeGeneratedSql();
    const filename = sqlDownloadFilename(
        state.selectedScanTargetName,
        state.activeSqlDatabase
    );
    const blob = new Blob([code], { type: 'application/sql;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    showToast(`Downloaded ${filename}`);
}

// --- Event Listeners ---
els.loginForm.addEventListener('submit', handleLogin);
els.btnLogout.addEventListener('click', handleLogout);
els.btnAddTarget.addEventListener('click', openAddTargetForm);
els.btnCancelForm.addEventListener('click', () => showView('view-welcome'));
els.btnDeleteTarget.addEventListener('click', deleteSelectedTarget);
els.btnCopySql.addEventListener('click', copySqlToClipboard);
els.btnDownloadSql.addEventListener('click', downloadGeneratedSql);
els.targetConfigForm.addEventListener('submit', saveTargetForm);

// Modal close listener
if (els.btnCloseModal && els.dbDetailsModal) {
    els.btnCloseModal.addEventListener('click', () => {
        els.dbDetailsModal.close();
    });
}

// Fallback for backdrop click on older browsers / Safari without native closedby support
if (els.dbDetailsModal && !('closedBy' in HTMLDialogElement.prototype)) {
    els.dbDetailsModal.addEventListener('click', (event) => {
        if (event.target !== els.dbDetailsModal) return;
        const rect = els.dbDetailsModal.getBoundingClientRect();
        const isDialogContent = (
            rect.top <= event.clientY &&
            event.clientY <= rect.top + rect.height &&
            rect.left <= event.clientX &&
            event.clientX <= rect.left + rect.width
        );
        if (isDialogContent) return;
        els.dbDetailsModal.close();
    });
}


// Initial Load
document.addEventListener('DOMContentLoaded', async () => {
    if (!getToken()) {
        showLogin();
        return;
    }
    showApp();
    await fetchTargets();
    await fetchScans();
});
