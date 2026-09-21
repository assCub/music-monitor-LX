/* music-monitor 前端：零构建单页应用 */
'use strict';

const App = (() => {
  const state = {
    platforms: [],
    qualityLevels: [],
    chartGroups: [],
    chartIndex: {},
    chartVerify: {},
    activeChartPlatform: '',
    resolvedChartEntry: null,
    resolvedChartCount: 0,
    favorites: [],
    favPlatforms: [],
    editId: null,
    sourcesSel: new Set(),
    chartSel: new Set(),
    favSel: new Set(),
    user: null,
    needsSetup: false,
    platformLogin: null,
    platformAccounts: [],
    activePlatform: 'netease',
    activePlatformMethod: 'qr',
  };

  /* ------------------------------------------------------------ 基础工具 */
  async function api(path, options) {
    const res = await fetch('/api' + path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (!res.ok) {
      if (res.status === 401 || res.status === 428) showAuth(res.status === 428);
      const msg = (data && (data.detail || data.message || data.error)) || ('HTTP ' + res.status);
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  async function initAuth() {
    try {
      const res = await fetch('/api/auth/status');
      const data = await res.json();
      state.needsSetup = !!data.needs_setup;
      state.user = data.user || null;
      if (!data.authenticated) {
        showAuth(state.needsSetup);
        return false;
      }
      applyUserState();
      $('auth-screen').classList.add('hidden');
      return true;
    } catch (err) {
      showAuth(false);
      $('auth-result').textContent = err.message;
      return false;
    }
  }

  function showAuth(setup = false) {
    state.needsSetup = setup;
    $('auth-screen').classList.remove('hidden');
    $('auth-title').textContent = setup ? '创建首位管理员' : '登录';
    $('auth-hint').textContent = setup ? '首次使用，请创建管理员账号。现有订阅会迁移给该管理员。' : '请输入你的账号密码。';
    $('auth-display-row').style.display = setup ? '' : 'none';
    $('auth-submit').textContent = setup ? '创建并进入' : '登录';
  }

  async function submitAuth() {
    const payload = {
      username: $('auth-username').value.trim(),
      password: $('auth-password').value,
      display_name: $('auth-display-name').value.trim(),
    };
    $('auth-result').textContent = state.needsSetup ? '正在初始化…' : '正在登录…';
    try {
      const res = await fetch('/api/auth/' + (state.needsSetup ? 'setup' : 'login'), {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || '认证失败');
      state.user = data.user;
      $('auth-password').value = '';
      $('auth-screen').classList.add('hidden');
      applyUserState();
      await refreshAll();
    } catch (err) { $('auth-result').innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  function applyUserState() {
    if (!state.user) return;
    $('user-pill').style.display = '';
    $('logout-btn').style.display = '';
    $('user-text').textContent = `${state.user.display_name || state.user.username} · ${state.user.role}`;
    $('user-admin-card').style.display = state.user.role === 'admin' ? '' : 'none';
    document.querySelectorAll('#page-settings .admin-only').forEach((el) => { el.style.display = state.user.role === 'admin' ? '' : 'none'; });
  }

  async function logoutUser() {
    await fetch('/api/auth/logout', {method:'POST'});
    state.user = null;
    showAuth(false);
  }

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const $ = (id) => document.getElementById(id);

  let toastTimer = null;
  function toast(message) {
    const el = $('toast');
    el.textContent = message;
    el.style.display = 'inline-flex';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.style.display = 'none'; }, 3200);
  }

  function fmtTime(iso) {
    if (!iso) return '—';
    return iso.replace('T', ' ').slice(0, 19);
  }

  function fmtDuration(sec) {
    if (!sec) return '';
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  function parseLinkLines(text) {
    return String(text || '').split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
      const idx = line.indexOf('|');
      if (idx > 0) return { name: line.slice(0, idx).trim(), link: line.slice(idx + 1).trim() };
      return { name: '', link: line };
    }).filter((x) => x.link);
  }

  const sourceLabel = (key) => {
    const found = state.platforms.find((p) => p.key === key);
    return found ? found.label : key;
  };

  /* ------------------------------------------------------------ 标签页 */
  function initTabs() {
    document.querySelectorAll('nav.tabs button').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
  }

  function switchTab(name) {
    document.querySelectorAll('nav.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + name));
    if (name === 'records') { loadRuns(); loadLxDownloads(); }
    if (name === 'settings') loadSettings();
  }

  /* ------------------------------------------------------------ 顶部状态 */
  async function refreshAll() {
    await Promise.all([loadBase(), checkEngine()]);
    await Promise.all([loadMonitors(), loadRuns()]);
    if (document.querySelector('#page-charts.active')) renderCharts();
  }

  async function loadBase() {
    try {
      const data = await api('/platforms');
      state.platforms = data.items || [];
      state.qualityLevels = data.quality_levels || [];
      renderPlatformChips('m-sources', state.sourcesSel);
      renderPlatformChips('fav-platforms', new Set(state.favPlatforms), (key, on) => {
        state.favPlatforms = on
          ? [...state.favPlatforms, key]
          : state.favPlatforms.filter((k) => k !== key);
      });
      fillQualitySelects();
      renderQualityHelp();
    } catch (err) {
      toast('读取平台清单失败：' + err.message);
    }
  }

  async function checkEngine() {
    try {
      const data = await api('/health');
      const ok = data.discovery?.ok && data.lx_gateway?.ok;
      if ($('app-version')) $('app-version').textContent = '版本 v' + (data.version || '未知');
      $('engine-pill').className = 'pill ' + (ok ? 'ok' : 'bad');
      $('engine-text').textContent = ok ? '发现/LX 正常' : '服务异常';
      const s = data.scheduler || {};
      $('sched-pill').className = 'pill';
      $('sched-text').textContent = '调度 ' + (s.last_tick_ago == null ? '启动中' : s.last_tick_ago + 's 前') +
        ' · 运行中 ' + (s.running_monitors || []).length;
      return ok;
    } catch (err) {
      $('engine-pill').className = 'pill bad';
      $('engine-text').textContent = '后端异常';
      return false;
    }
  }

  function fillQualitySelects() {
    const opts = state.qualityLevels.map((q) => `<option value="${esc(q.key)}">${esc(q.label)}</option>`).join('');
    ['m-quality', 's-quality'].forEach((id) => {
      const el = $(id);
      if (!el) return;
      const cur = el.value;
      el.innerHTML = opts;
      if (cur) el.value = cur;
    });
  }

  function renderQualityHelp() {
    const rows = state.qualityLevels.map((q) => `<div><b>${esc(q.key)}</b><p class="muted small">${esc(q.label)}</p></div>`);
    $('quality-help').innerHTML = rows.join('');
  }

  function renderPlatformChips(containerId, sel, onChange) {
    const box = $(containerId);
    if (!box) return;
    box.innerHTML = state.platforms.map((p) => {
      const on = sel.has(p.key);
      const tags = [
        p.playlist ? '歌单' : '',
        p.user_playlist ? '个人歌单' : '',
      ].filter(Boolean).map((t) => `<span class="tag">${t}</span>`).join('');
      return `<label class="chip ${on ? 'on' : ''}" data-key="${esc(p.key)}" title="${esc(p.desc || '')}">
        ${esc(p.label)}${tags}</label>`;
    }).join('');
    box.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', (ev) => {
        ev.preventDefault();
        const key = chip.dataset.key;
        const on = !sel.has(key);
        if (on) sel.add(key); else sel.delete(key);
        chip.classList.toggle('on', on);
        if (onChange) onChange(key, on);
      });
    });
  }

  /* ------------------------------------------------------------ 监控列表 */
  async function loadMonitors() {
    let data;
    try { data = await api('/monitors'); } catch (err) { toast(err.message); return; }
    const items = data.items || [];
    const running = new Set(data.running || []);
    $('monitor-summary').textContent = `共 ${items.length} 个监控任务`;
    $('monitor-empty').style.display = items.length ? 'none' : 'block';

    $('monitor-list').innerHTML = items.map((m) => {
      const kindLabel = { chart: '热门榜单', playlist: '指定歌单', favorites: '个人收藏夹', artist: '歌手关注' }[m.kind] || m.kind;
      const isRunning = running.has(m.id);
      const srcs = (m.sources || []).map((s) => `<span class="tag">${esc(sourceLabel(s))}</span>`).join('');
      let targetDesc = '';
      if (m.kind === 'chart') {
        const names = (m.target.charts || []).map((c) => c.name || c.key || c.link || c.id);
        targetDesc = names.length ? names.join('、') : '未选择榜单';
      } else if (m.kind === 'playlist') {
        const names = (m.target.playlists || []).map((c) => c.name || c.link);
        targetDesc = names.length ? names.join('、') : '未配置歌单';
      } else if (m.kind === 'artist') {
        const names = (m.target.artists || []).map((c) => c.name || c.id).filter(Boolean);
        targetDesc = names.length ? '关注：' + names.join('、') : '未配置歌手';
      } else {
        const ids = m.target.playlist_ids || [];
        targetDesc = ids.length ? '已选 ' + ids.length + ' 个收藏夹' : '该平台全部收藏夹';
      }
      return `<div class="card monitor-card">
        <div class="monitor-head">
          <div style="flex:1">
            <div class="title">${esc(m.name)} ${isRunning ? '<span class="status running">执行中</span>' : ''}
              ${m.enabled ? '' : '<span class="status skipped">已停用</span>'}</div>
            <div class="sub">${esc(kindLabel)} · ${esc(targetDesc)}</div>
          </div>
        </div>
        <div class="kv">
          <span>音质：<b>${esc(m.quality)}</b></span>
          <span>策略：<b>${m.fallback === 'skip' ? '严格' : '取最高可用'}</b></span>
          <span>间隔：<b>${m.interval_minutes} 分钟</b></span>
          <span>上次：<b>${esc(fmtTime(m.last_run_at))}</b></span>
          <span>下次：<b>${esc(fmtTime(m.next_run_at))}</b></span>
          <span>自动下载：<b>${m.auto_download ? '开' : '关'}</b></span>
        </div>
        <div class="chips">${srcs || '<span class="muted small">未指定平台</span>'}</div>
        <div class="row tight">
          <button class="btn small primary" onclick="App.runMonitor(${m.id})">立即执行</button>
          <button class="btn small" onclick="App.previewMonitor(${m.id})">干跑预览</button>
          <button class="btn small" onclick="App.toggleMonitor(${m.id}, ${m.enabled ? 'false' : 'true'})">${m.enabled ? '停用' : '启用'}</button>
          <button class="btn small" onclick="App.editMonitor(${m.id})">编辑</button>
          <button class="btn small" onclick="App.showRuns(${m.id})">历史</button>
          <button class="btn small danger" onclick="App.deleteMonitor(${m.id})">删除</button>
        </div>
        <div id="monitor-preview-${m.id}" class="small muted"></div>
      </div>`;
    }).join('');
  }

  async function runMonitor(id) {
    try {
      const r = await api(`/monitors/${id}/run`, { method: 'POST' });
      toast(r.message || '已触发');
      setTimeout(loadMonitors, 1500);
    } catch (err) { toast('触发失败：' + err.message); }
  }

  async function previewMonitor(id) {
    const box = $('monitor-preview-' + id);
    box.innerHTML = '正在干跑预览…';
    try {
      const r = await api(`/monitors/${id}/preview`, { method: 'POST' });
      box.innerHTML = `可解析曲目 <b>${r.total}</b> 首，过滤后 <b>${r.filtered}</b> 首`
        + (r.warnings && r.warnings.length ? `<br><span style="color:var(--warn)">${r.warnings.map(esc).join('<br>')}</span>` : '');
    } catch (err) { box.innerHTML = '<span style="color:var(--err)">' + esc(err.message) + '</span>'; }
  }

  async function toggleMonitor(id, enabled) {
    try {
      await api(`/monitors/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !!enabled }) });
      loadMonitors();
    } catch (err) { toast(err.message); }
  }

  async function deleteMonitor(id) {
    if (!confirm('删除该监控？关联的曲目记录也会一起删除（已下载的音乐文件不受影响）。')) return;
    try { await api(`/monitors/${id}`, { method: 'DELETE' }); loadMonitors(); }
    catch (err) { toast(err.message); }
  }

  async function showRuns(id) {
    switchTab('records');
    $('track-monitor').value = String(id);
    await loadRuns(id);
    await loadTracks();
  }

  /* ------------------------------------------------------------ 榜单 */
  async function renderCharts() {
    if (!state.chartGroups.length) {
      const data = await api('/charts');
      state.chartGroups = data.groups || [];
      state.chartIndex = data.index || {};
      state.activeChartPlatform = state.chartGroups[0]?.platform || '';
    }
    const platforms = state.chartGroups.map((g) => g.platform);
    if (!platforms.includes(state.activeChartPlatform)) state.activeChartPlatform = platforms[0] || '';

    $('chart-platforms').innerHTML = state.chartGroups.map((g) => {
      const count = g.charts.filter((c) => state.chartVerify[c.key]?.ok).length;
      const badge = count ? `<span class="tag">${count} 可用</span>` : '';
      return `<label class="chip ${g.platform === state.activeChartPlatform ? 'on' : ''}" data-key="${esc(g.platform)}">
        ${esc(g.label)}<span class="tag">${esc(g.region)}</span>${badge}</label>`;
    }).join('');
    $('chart-platforms').querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', () => { state.activeChartPlatform = chip.dataset.key; renderCharts(); });
    });

    const group = state.chartGroups.find((g) => g.platform === state.activeChartPlatform);
    $('chart-list').innerHTML = (group?.charts || []).map((c) => {
      const v = state.chartVerify[c.key];
      const badge = v ? (v.ok
        ? `<span class="status downloaded">${v.count} 首</span>`
        : `<span class="status failed">不可用</span>`) : '';
      return `<div class="card">
        <div class="row tight"><b>${esc(c.name)}</b>${badge}</div>
        <div class="muted small mono" style="word-break:break-all;margin:6px 0">${esc(c.rank ? c.platform + ' 榜单 ' + c.rank : (c.id || c.link || ''))}</div>
        ${c.note ? `<div class="muted small">${esc(c.note)}</div>` : ''}
        ${v && !v.ok && v.message ? `<div class="small" style="color:var(--err)">${esc(v.message)}</div>` : ''}
        <div class="row tight" style="margin-top:8px">
          <button class="btn small" onclick="App.verifyChart('${esc(c.key)}')">校验</button>
          <button class="btn small" onclick="App.previewChart('${esc(c.key)}')">预览曲目</button>
          <button class="btn small primary" onclick="App.monitorFromChart('${esc(c.key)}')">创建监控</button>
        </div>
      </div>`;
    }).join('') || '<div class="empty">该平台暂无内置榜单，可用下方「自定义榜单」添加。</div>';
  }

  function currentChartKeys() {
    const group = state.chartGroups.find((g) => g.platform === state.activeChartPlatform);
    return (group?.charts || []).map((c) => c.key);
  }

  async function verifyChart(key) { await verifyCharts([key], true); }

  async function verifyCharts(keys, quiet) {
    const status = $('chart-verify-status');
    status.textContent = '校验中…';
    try {
      const r = await api('/charts/verify', { method: 'POST', body: JSON.stringify({ keys: keys || [] }) });
      (r.items || []).forEach((item) => {
        if (item.key) state.chartVerify[item.key] = item;
      });
      const ok = (r.items || []).filter((i) => i.ok).length;
      status.textContent = `校验完成：${ok}/${(r.items || []).length} 可用`;
      renderCharts();
      if (!quiet) toast(status.textContent);
    } catch (err) { status.textContent = '校验失败：' + err.message; }
  }

  async function previewChart(key) {
    const c = state.chartIndex[key];
    if (!c) return;
    showChartPreview(`正在解析「${c.name}」…`, '', []);
    try {
      const r = await api('/charts/resolve?key=' + encodeURIComponent(key));
      state.resolvedChartEntry = { platform: c.platform, id: c.id || '', link: c.link || '', name: c.name, key };
      state.resolvedChartCount = r.count;
      showChartPreview(`${c.name}（${r.count} 首）`, r.ok ? '' : r.message,
        r.songs.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.name)}</td><td>${esc(s.artist)}</td>
          <td>${esc(s.album)}</td><td>${esc(sourceLabel(s.source))}</td></tr>`));
    } catch (err) { showChartPreview(c.name, err.message, []); }
  }

  function showChartPreview(title, message, rows) {
    $('chart-preview-card').style.display = 'block';
    $('chart-preview-title').textContent = title;
    $('chart-preview-meta').innerHTML = message ? `<span style="color:var(--warn)">${esc(message)}</span>` : '';
    $('chart-preview-body').innerHTML = rows.join('') || '<tr><td colspan="5" class="empty">没有曲目</td></tr>';
  }

  function monitorFromChart(key) {
    const c = state.chartIndex[key];
    if (!c) return;
    state.chartSel = new Set([key]);
    openMonitorModal(null, { kind: 'chart', charts: [key] });
  }

  async function resolveCustomChart() {
    const name = $('custom-chart-name').value.trim();
    const link = $('custom-chart-link').value.trim();
    const box = $('custom-chart-result');
    if (!link) { box.textContent = '请填写歌单链接'; return; }
    box.textContent = '解析中…';
    try {
      const r = await api('/charts/resolve?link=' + encodeURIComponent(link) + '&limit=30');
      if (!r.ok) { box.innerHTML = `<span style="color:var(--err)">解析失败：${esc(r.message || '未知原因')}</span>`; return; }
      box.innerHTML = `解析成功，共 <b>${r.count}</b> 首。` +
        `<button class="btn small" style="margin-left:8px" onclick="App.monitorFromCustomChart()">创建监控</button>`;
      state.resolvedChartEntry = { platform: (r.resolved && r.resolved.source) || '', id: (r.resolved && r.resolved.id) || '', link, name: name || (r.resolved && r.resolved.name) || '自定义榜单' };
      state.resolvedChartCount = r.count;
      showChartPreview(state.resolvedChartEntry.name + `（${r.count} 首）`, '',
        r.songs.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.name)}</td><td>${esc(s.artist)}</td>
          <td>${esc(s.album)}</td><td>${esc(sourceLabel(s.source))}</td></tr>`));
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  function monitorFromCustomChart() {
    if (!state.resolvedChartEntry) return;
    openMonitorModal(null, { kind: 'chart', custom: [state.resolvedChartEntry] });
  }

  /* ------------------------------------------------------------ 歌单 / 收藏夹 */
  async function resolvePlaylist() {
    const link = $('playlist-link').value.trim();
    const box = $('playlist-result');
    if (!link) { box.textContent = '请填写歌单链接'; return; }
    box.textContent = '解析中…';
    try {
      const r = await api('/playlists/resolve?link=' + encodeURIComponent(link) + '&limit=40');
      if (!r.ok) { box.innerHTML = `<span style="color:var(--err)">${esc(r.message || '解析失败')}</span>`; return; }
      const picked = r.picked || {};
      box.innerHTML = `识别为「${esc(picked.name || '未命名歌单')}」（${esc(sourceLabel(picked.source))}），共 <b>${r.count}</b> 首。` +
        `<button class="btn small" style="margin-left:8px" onclick="App.monitorFromPlaylist()">创建监控</button>`;
      state.resolvedPlaylist = { id: picked.id, source: picked.source, link: picked.link || link, name: picked.name || '歌单' };
      showChartPreview((picked.name || '歌单') + `（${r.count} 首）`, '',
        r.songs.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.name)}</td><td>${esc(s.artist)}</td>
          <td>${esc(s.album)}</td><td>${esc(sourceLabel(s.source))}</td></tr>`));
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  function monitorFromPlaylist() {
    if (!state.resolvedPlaylist) return;
    openMonitorModal(null, { kind: 'playlist', playlists: [state.resolvedPlaylist] });
  }

  async function loadFavorites() {
    const box = $('fav-list');
    const status = $('fav-status');
    const sources = state.favPlatforms;
    status.textContent = '读取中…';
    box.innerHTML = '<div class="empty"><span class="spin">◐</span> 正在向各平台请求…</div>';
    try {
      const r = await api('/favorites/list', { method: 'POST', body: JSON.stringify({ sources }) });
      state.favorites = r.items || [];
      status.textContent = r.ok ? `读到 ${state.favorites.length} 个歌单/收藏夹` : (r.message || '没有数据');
      box.innerHTML = state.favorites.map((p) => `
        <label class="chip ${state.favSel.has(p.key) ? 'on' : ''}" data-key="${esc(p.key)}" style="margin:4px 6px 0 0">
          ${esc(p.name)}<span class="tag">${esc(sourceLabel(p.source))} · ${esc(p.track_count)} 首</span>
        </label>`).join('') || `<div class="empty">${esc(r.message || '没有读到个人歌单')}</div>`;
      box.querySelectorAll('.chip').forEach((chip) => {
        chip.addEventListener('click', (ev) => {
          ev.preventDefault();
          const key = chip.dataset.key;
          const on = !state.favSel.has(key);
          if (on) state.favSel.add(key); else state.favSel.delete(key);
          chip.classList.toggle('on', on);
        });
      });
    } catch (err) {
      status.textContent = '失败';
      box.innerHTML = `<div class="empty" style="color:var(--err)">${esc(err.message)}</div>`;
    }
  }

  function createFavoritesMonitor() {
    const ids = [...state.favSel];
    const sources = [...new Set(ids.map((k) => k.split(':')[0]))];
    openMonitorModal(null, { kind: 'favorites', playlist_ids: ids, sources: sources.length ? sources : state.favPlatforms });
  }

  /* ------------------------------------------------------------ 记录 */
  async function loadRuns(monitorId) {
    let data;
    try { data = await api('/runs?limit=50'); } catch (_) { return; }
    const nameOf = {};
    try {
      const m = await api('/monitors');
      (m.items || []).forEach((x) => { nameOf[x.id] = x.name; });
      const sel = $('track-monitor');
      const cur = sel.value;
      sel.innerHTML = '<option value="">全部监控</option>' +
        (m.items || []).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
      sel.value = cur || '';
    } catch (_) { /* ignore */ }

    $('run-body').innerHTML = (data.items || []).map((r) => `
      <tr>
        <td class="mono">${esc(fmtTime(r.started_at))}</td>
        <td>${esc(nameOf[r.monitor_id] || ('#' + r.monitor_id))}</td>
        <td><span class="status ${esc(r.status)}">${esc(r.status)}</span></td>
        <td>${r.found}</td><td>${r.new_items}</td><td>${r.downloaded}</td>
        <td class="muted" title="这些歌引擎库里已经有，没有产生新文件（所以和引擎的下载记录条数天生不等）">${r.engine_skipped != null ? r.engine_skipped : '—'}</td>
        <td>${r.skipped}</td><td>${r.failed}</td>
        <td class="small muted">${esc((r.message || '').slice(0, 80))}</td>
        <td><button class="btn small" onclick="App.showRunLog(${r.id})">日志</button></td>
      </tr>`).join('') || '<tr><td colspan="11" class="empty">暂无执行记录</td></tr>';
  }

  async function showRunLog(runId) {
    try {
      const r = await api('/runs?limit=200');
      const run = (r.items || []).find((x) => x.id === runId);
      if (!run) return;
      $('run-log-card').style.display = 'block';
      $('run-log-title').textContent = `运行日志 #${run.id}（${fmtTime(run.started_at)}）`;
      $('run-log').textContent = run.log || '（无日志）';
      $('run-log-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) { toast(err.message); }
  }

  async function loadTracks() {
    const mid = $('track-monitor').value;
    const status = $('track-status').value;
    const qs = [`limit=200`];
    if (mid) qs.push('monitor_id=' + mid);
    if (status) qs.push('status=' + status);
    let data;
    try { data = await api('/tracks?' + qs.join('&')); } catch (err) { toast(err.message); return; }
    $('track-summary').textContent = '统计：' + Object.entries(data.stats || {}).map(([k, v]) => `${k}=${v}`).join('  ');
    $('track-body').innerHTML = (data.items || []).map((t) => `
      <tr>
        <td>${esc(t.name)}</td>
        <td>${esc(t.artist)}</td>
        <td>${esc(sourceLabel(t.source))}</td>
        <td><span class="status ${esc(t.status)}">${esc(t.status)}</span></td>
        <td class="small">${esc(t.quality_actual || t.bitrate || '')}</td>
        <td class="mono small" title="${esc(t.file_path)}">${esc((t.file_path || '').split(/[\\/]/).pop())}</td>
        <td class="small muted" title="${esc(t.error)}">${esc((t.error || '').slice(0, 60))}</td>
        <td><button class="btn small" onclick="App.retryTracks([${t.id}])">重试</button></td>
      </tr>`).join('') || '<tr><td colspan="8" class="empty">暂无曲目记录</td></tr>';
  }

  async function retryTracks(ids) {
    if (!ids.length) return;
    toast('正在重试 ' + ids.length + ' 首…');
    try {
      const r = await api('/tracks/retry', { method: 'POST', body: JSON.stringify({ track_ids: ids }) });
      const ok = (r.items || []).filter((x) => x.ok).length;
      toast(`重试完成：成功 ${ok} / ${ids.length}`);
      loadTracks();
    } catch (err) { toast('重试失败：' + err.message); }
  }

  async function retryFailed() {
    const mid = $('track-monitor').value;
    const qs = ['status=failed', 'limit=20'];
    if (mid) qs.push('monitor_id=' + mid);
    const data = await api('/tracks?' + qs.join('&'));
    const ids = (data.items || []).map((t) => t.id);
    if (!ids.length) { toast('没有失败记录'); return; }
    retryTracks(ids);
  }

  function fmtBytes(value) {
    const n = Number(value) || 0;
    if (!n) return '—';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }

  async function loadLxDownloads() {
    const body = $('lx-download-body');
    if (!body) return;
    try {
      const data = await api('/lx/downloads?limit=100');
      body.innerHTML = (data.items || []).map((item) => {
        const progress = Math.max(0, Math.min(1, Number(item.progress) || 0));
        const percent = Math.round(progress * 100);
        const statusClass = item.status === 'completed' ? 'downloaded' : (item.status === 'failed' ? 'failed' : (item.status === 'skipped' ? 'skipped' : 'pending'));
        return `<tr>
          <td><b>${esc(item.name)}</b><div class="small muted">${esc(item.artist)}</div></td>
          <td><span class="status ${statusClass}">${esc(item.status)}</span></td>
          <td class="small">${esc(item.quality || item.requested_quality || '')} / ${esc(item.platform || '')}</td>
          <td style="min-width:140px"><div class="progress"><span style="width:${percent}%"></span></div><div class="small muted">${percent}%</div></td>
          <td class="small">${fmtBytes(item.downloaded_bytes)}${item.total_bytes ? ' / ' + fmtBytes(item.total_bytes) : ''}</td>
          <td class="small muted" title="${esc(item.path || item.error || '')}">${esc((item.path || item.error || '').slice(0, 80))}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="6" class="empty">暂无 LX 下载任务</td></tr>';
    } catch (err) {
      body.innerHTML = `<tr><td colspan="6" class="empty">${esc(err.message)}</td></tr>`;
    }
  }

  async function clearLxDownloads() {
    try { await api('/lx/downloads/completed', { method: 'DELETE' }); await loadLxDownloads(); }
    catch (err) { toast('清理失败：' + err.message); }
  }

  /* ------------------------------------------------------------ 设置 */
  async function loadSettings() {
    try {
      const r = await api('/settings');
      const s = r.settings || {};
      $('s-quality').value = s.default_quality || 'master';
      $('s-interval').value = s.default_interval_minutes || 360;
      $('s-max').value = s.default_max_downloads || 30;
      $('s-embed').value = String(s.default_embed != null ? s.default_embed : 1);
      fillLxRuntimeSettings(s);
      if (Object.keys(s).length) { /* noop */ }
    } catch (err) { toast(err.message); }
    await checkEngine();
    await loadPlatformAccounts();
    await loadLxSettings(false);
    if (state.user?.role === 'admin') await loadUsers();
  }

  function fillLxRuntimeSettings(s) {
    const set = (id, value) => { if ($(id) && value != null) $(id).value = value; };
    set('lx-tick-seconds', s.tick_seconds || 60);
    set('lx-global-max', s.max_downloads_per_run != null ? s.max_downloads_per_run : 0);
    set('lx-concurrency', s.download_concurrency || 1);
    set('lx-retries', s.download_retries != null ? s.download_retries : 2);
    set('lx-batch', s.download_batch || 8);
    set('lx-batch-gap', s.download_batch_gap != null ? s.download_batch_gap : 3);
    set('lx-timeout', s.download_timeout || 300);
    set('lx-download-subdir', s.lx_download_subdir || '');
    set('lx-filename-template', s.lx_filename_template || '{artist} - {name}');
    set('lx-artist-dir', s.lx_artist_dir === false ? '0' : '1');
    set('lx-priority', Array.isArray(s.lx_platform_priority) ? s.lx_platform_priority.join(',') : (s.lx_platform_priority || 'tx,kg,kw,mg,wy'));
    set('lx-quality-floor', s.lx_quality_floor || '128k');
    set('lx-search-limit', s.lx_search_limit || 30);
    set('lx-match-threshold', s.lx_match_threshold != null ? s.lx_match_threshold : 0.76);
  }

  async function loadLxSettings(loadSaved = true) {
    const pill = $('lx-status-pill'), text = $('lx-status-text');
    if (!pill || !text) return;
    text.textContent = '检测中…'; pill.classList.remove('ok', 'bad');
    try {
      if (loadSaved) {
        const saved = await api('/settings');
        fillLxRuntimeSettings(saved.settings || {});
      }
      const r = await api('/lx');
      const ok = r.health && r.health.ok;
      pill.classList.toggle('ok', !!ok); pill.classList.toggle('bad', !ok);
      text.textContent = ok
        ? `LX 正常 · ${(r.sources || []).filter((x) => x.enabled).length} 个已启用`
        : `LX 异常 · ${r.health?.detail || '未连接'}`;
      const root = r.config?.download_root || '/downloads';
      $('lx-path-hint').textContent = `Compose 授权的容器下载根目录：${root}。网页只能选择这个目录下的相对子目录；宿主机真实路径仍在 docker-compose.yml 中配置。`;
      renderLxSources(r.sources || []);
    } catch (err) {
      pill.classList.add('bad'); text.textContent = 'LX 连接失败';
      $('lx-source-list').innerHTML = `<div class="notice err">${esc(err.message)}</div>`;
    }
  }

  function renderLxSources(items) {
    const box = $('lx-source-list');
    if (!box) return;
    box.innerHTML = items.map((item) => {
      const platforms = Object.entries(item.sources || {}).map(([key, info]) => `${key}: ${(info.qualitys || []).join('/')}`).join(' · ');
      return `<div class="card monitor-card" style="margin:0">
        <div class="monitor-head"><div><div class="title">${esc(item.name || item.id)}</div><div class="sub mono">${esc(item.id)}</div></div><div class="spacer"></div>
          <span class="status ${item.enabled && item.loaded ? 'downloaded' : 'skipped'}">${item.enabled ? (item.loaded ? '已启用' : '加载失败') : '已停用'}</span></div>
        <div class="small ${item.error ? '' : 'muted'}" style="${item.error ? 'color:var(--err)' : ''}">${esc(item.error || platforms || '未声明可用平台')}</div>
        <div class="row">
          <button class="btn small" onclick="App.toggleLxSource('${esc(item.id)}', ${item.enabled ? 'false' : 'true'})">${item.enabled ? '停用' : '启用'}</button>
          <button class="btn small danger" onclick="App.deleteLxSource('${esc(item.id)}')">删除</button>
        </div></div>`;
    }).join('') || '<div class="empty">还没有音源。可从 URL、本地文件或脚本文本导入。</div>';
  }

  async function saveLxSettings() {
    try {
      const priority = $('lx-priority').value.split(',').map((x) => x.trim()).filter(Boolean);
      const payload = {
        lx_download_subdir: $('lx-download-subdir').value.trim(),
        lx_filename_template: $('lx-filename-template').value.trim() || '{artist} - {name}',
        lx_artist_dir: $('lx-artist-dir').value === '1',
      };
      if (state.user?.role === 'admin') Object.assign(payload, {
        tick_seconds: Number($('lx-tick-seconds').value) || 60,
        max_downloads_per_run: Number($('lx-global-max').value) || 0,
        download_concurrency: Number($('lx-concurrency').value) || 1,
        download_retries: Number($('lx-retries').value) || 0, download_batch: Number($('lx-batch').value) || 8,
        download_batch_gap: Number($('lx-batch-gap').value) || 0, download_timeout: Number($('lx-timeout').value) || 300,
        lx_platform_priority: priority, lx_quality_floor: $('lx-quality-floor').value,
        lx_search_limit: Number($('lx-search-limit').value) || 30,
        lx_match_threshold: Number($('lx-match-threshold').value) || 0.76,
      });
      await api('/settings', { method: 'POST', body: JSON.stringify(payload) });
      toast('LX 下载设置已保存，新任务立即生效');
      await loadLxSettings();
    } catch (err) { toast('保存失败：' + err.message); }
  }

  async function reloadLxSources() {
    try { await api('/lx/reload', { method: 'POST' }); toast('音源目录已重新加载'); await loadLxSettings(false); }
    catch (err) { toast('重载失败：' + err.message); }
  }

  async function importLxSource(payload) {
    const result = $('lx-source-result');
    result.textContent = '正在验证并导入音源…';
    try {
      const r = await api('/lx/sources', { method: 'POST', body: JSON.stringify({ name: $('lx-source-name').value.trim(), ...payload }) });
      result.innerHTML = `<span style="color:var(--ok)">导入成功：${esc(r.item?.name || r.item?.id || '')}</span>`;
      $('lx-source-url').value = ''; $('lx-source-script').value = ''; $('lx-source-file').value = '';
      await loadLxSettings(false);
    } catch (err) { result.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  function importLxSourceUrl() {
    const url = $('lx-source-url').value.trim();
    if (!url) return toast('请填写音源 URL');
    return importLxSource({ url });
  }

  function importLxSourceText() {
    const script = $('lx-source-script').value;
    if (!script.trim()) return toast('请粘贴音源脚本');
    return importLxSource({ script });
  }

  async function importLxSourceFile() {
    const file = $('lx-source-file').files?.[0];
    if (!file) return toast('请先选择音源脚本文件');
    if (file.size > 5 * 1024 * 1024) return toast('音源脚本不能超过 5MB');
    if (!$('lx-source-name').value.trim()) $('lx-source-name').value = file.name.replace(/\.[^.]+$/, '');
    return importLxSource({ script: await file.text() });
  }

  async function toggleLxSource(id, enabled) {
    try { await api(`/lx/sources/${encodeURIComponent(id)}/enable`, { method: 'POST', body: JSON.stringify({ enabled }) }); await loadLxSettings(false); }
    catch (err) { toast('修改音源状态失败：' + err.message); }
  }

  async function deleteLxSource(id) {
    if (!confirm(`确定删除音源 ${id}？对应脚本文件也会删除。`)) return;
    try { await api(`/lx/sources/${encodeURIComponent(id)}`, { method: 'DELETE' }); toast('音源已删除'); await loadLxSettings(false); }
    catch (err) { toast('删除失败：' + err.message); }
  }

  async function saveSettings() {
    try {
      await api('/settings', {
        method: 'POST',
        body: JSON.stringify({
          default_quality: $('s-quality').value,
          default_interval_minutes: Number($('s-interval').value) || 360,
          default_max_downloads: Number($('s-max').value) || 30,
          default_embed: Number($('s-embed').value),
        }),
      });
      toast('已保存默认参数');
    } catch (err) { toast(err.message); }
  }

  const PLATFORM_META = {
    netease: {label:'网易云音乐', short:'易', app:'网易云音乐 App', cookie:'MUSIC_U=...; __csrf=...'},
    qq: {label:'QQ 音乐', short:'Q', app:'QQ 或微信', cookie:'uin=...; qm_keyst=...; qqmusic_key=...'},
    kugou: {label:'酷狗音乐', short:'K', app:'酷狗音乐 App', cookie:'userid=...; token=...; KUGOU_API_MID=...'},
    bilibili: {label:'哔哩哔哩', short:'B', app:'哔哩哔哩 App', cookie:'SESSDATA=...; bili_jct=...; DedeUserID=...'},
    soda: {label:'汽水音乐', short:'汽', app:'抖音或汽水音乐 App', cookie:'sessionid=...; uid_tt=...'},
    kuwo: {label:'酷我音乐', short:'酷', app:'酷我音乐', cookie:'kw_token=...; userid=...'},
    migu: {label:'咪咕音乐', short:'咪', app:'咪咕音乐', cookie:'完整粘贴咪咕音乐网页 Cookie'},
  };

  const QR_LABELS = {
    netease:'生成网易云二维码', qq:'使用 QQ 扫码', qq_wx:'使用微信扫码',
    kugou:'生成酷狗二维码', bilibili:'生成哔哩哔哩二维码', soda:'生成汽水音乐二维码',
  };

  function platformMeta(source) {
    const account = state.platformAccounts.find((item) => item.source === source) || {};
    return {...(PLATFORM_META[source] || {label:source, short:String(source || '?').slice(0, 1).toUpperCase()}), ...account,
      cookie:account.cookie_hint || PLATFORM_META[source]?.cookie || '完整 Cookie'};
  }

  function accountStatus(account) {
    if (account?.valid && (account.qr_sources?.length || account.profile?.verified)) return {label:'已登录', cls:'online'};
    if (account?.valid) return {label:'已配置', cls:'online'};
    if (account?.configured) return {label:'登录已失效', cls:'expired'};
    return {label:'未登录', cls:'offline'};
  }

  function visiblePlatformAccounts() {
    return state.platformAccounts.filter((item) => item.fixed || item.configured);
  }

  function renderPlatformTabs() {
    const box = $('platform-switcher');
    if (!box) return;
    const tabs = visiblePlatformAccounts().map((item) => {
      const meta = platformMeta(item.source), status = accountStatus(item);
      return `<button class="platform-tab ${item.source === state.activePlatform ? 'active' : ''}" id="platform-tab-${esc(item.source)}" data-platform="${esc(item.source)}" role="tab" onclick="App.switchPlatformAccount('${esc(item.source)}')">
        <span class="platform-logo ${esc(item.source)}">${esc(meta.short)}</span><span><b>${esc(meta.label)}</b><small class="${status.cls}">${esc(status.label)}</small></span>
      </button>`;
    });
    tabs.push(`<button class="platform-tab platform-tab-other ${state.activePlatform === '__other__' ? 'active' : ''}" id="platform-tab-other" data-platform="other" role="tab" onclick="App.switchPlatformAccount('__other__')">
      <span class="platform-logo other">+</span><span><b>其他平台</b><small>JSON 配置</small></span>
    </button>`);
    box.innerHTML = tabs.join('');
  }

  async function loadPlatformAccounts() {
    const profile = $('platform-profile');
    if (!profile) return;
    profile.innerHTML = '<div class="platform-profile-loading"><span class="spin">◌</span> 正在读取账号状态…</div>';
    try {
      const r = await api('/platform-accounts');
      state.platformAccounts = r.items || [];
      if (state.activePlatform !== '__other__' && !visiblePlatformAccounts().some((item) => item.source === state.activePlatform)) {
        state.activePlatform = visiblePlatformAccounts()[0]?.source || '__other__';
      }
      renderPlatformTabs();
      renderPlatformAccount();
    } catch (err) {
      profile.innerHTML = `<div class="platform-profile-empty"><b>读取账号失败</b><span>${esc(err.message)}</span></div>`;
    }
  }

  function switchPlatformAccount(source) {
    if (source !== '__other__' && !visiblePlatformAccounts().some((item) => item.source === source)) return;
    state.activePlatform = source;
    if ($('platform-cookie-input')) $('platform-cookie-input').value = '';
    if ($('platform-account-result')) $('platform-account-result').textContent = '';
    renderPlatformTabs();
    renderPlatformAccount();
  }

  function switchPlatformMethod(method) {
    if (!['qr', 'cookie'].includes(method)) return;
    state.activePlatformMethod = method;
    document.querySelectorAll('.account-method-tab').forEach((el) => el.classList.toggle('active', el.id === `account-method-tab-${method}`));
    ['qr', 'cookie'].forEach((key) => $('account-method-' + key)?.classList.toggle('hidden', key !== method));
  }

  function renderPlatformAccount() {
    const source = state.activePlatform;
    const profileBox = $('platform-profile');
    const panel = profileBox.closest('.platform-account-panel');
    const loginBox = $('platform-login-box');
    const otherBox = $('platform-other-box');

    if (source === '__other__') {
      panel?.classList.remove('is-authenticated');
      panel?.classList.add('is-other');
      profileBox.style.display = 'none';
      loginBox.style.display = 'none';
      otherBox.style.display = '';
      otherBox.classList.remove('hidden');
      const supported = state.platformAccounts.filter((item) => !(item.qr_sources || []).length);
      $('other-platform-supported').innerHTML = supported.map((item) => `<span class="other-platform-chip ${item.configured ? 'configured' : ''}">
        <i class="platform-logo ${esc(item.source)}">${esc(platformMeta(item.source).short)}</i>${esc(item.label)}${item.configured ? '<b>已配置</b>' : ''}
      </span>`).join('');
      return;
    }

    panel?.classList.remove('is-other');
    otherBox.style.display = 'none';
    otherBox.classList.add('hidden');
    profileBox.style.display = '';
    const meta = platformMeta(source);
    const account = state.platformAccounts.find((item) => item.source === source) || {source, configured:false, valid:false, profile:{}};
    const profile = account.profile || {};
    const status = accountStatus(account);
    const methodLabels = {qr:'官方扫码', wechat_qr:'微信扫码', cookie:'Cookie'};

    const avatar = profile.avatar
      ? `<img class="platform-avatar" width="96" height="96" src="${esc(profile.avatar)}" alt="${esc(profile.nickname || meta.label)}头像">`
      : `<div class="platform-avatar platform-avatar-fallback ${esc(source)}">${esc(meta.short)}</div>`;
    const supportsQR = (account.qr_sources || []).length > 0;
    const showLogin = !account.valid && supportsQR;
    panel?.classList.toggle('is-authenticated', !!account.valid);
    loginBox?.classList.toggle('hidden', !showLogin);
    if (loginBox) loginBox.style.display = showLogin ? '' : 'none';
    profileBox.className = `platform-profile ${account.valid ? 'authenticated' : 'signed-out'} ${source}`;

    if (account.valid) {
      const vip = Number(profile.vip_type) > 0 ? '<span class="profile-vip">VIP</span>' : '';
      profileBox.innerHTML = `
        <div class="platform-profile-hero">
          <div class="platform-avatar-ring">${avatar}</div>
          <div class="platform-identity">
            <div class="platform-name-line"><h3>${esc(profile.nickname || `${meta.label}用户`)}</h3>${vip}</div>
            <div class="profile-id">${esc(meta.label)} · 用户 ID ${esc(profile.user_id || '—')}</div>
            <span class="account-state ${status.cls}"><i></i>${esc(status.label)}</span>
          </div>
        </div>
        <div class="platform-profile-stats">
          <div><span>当前平台</span><strong>${esc(meta.label)}</strong></div>
          <div><span>登录方式</span><strong>${esc(methodLabels[account.login_method] || '已保存凭据')}</strong></div>
          <div><span>凭据状态</span><strong>已加密保存</strong></div>
        </div>
        <div class="platform-profile-actions">
          <button class="btn account-btn danger platform-logout" onclick="App.logoutPlatformAccount()">退出登录</button>
        </div>`;
    } else {
      const emptyText = account.configured
        ? (supportsQR ? '当前凭据已失效，请在右侧重新扫码或更新 Cookie。' : '当前凭据已失效，请前往“其他平台”更新 JSON。')
        : '登录后即可读取你的个人歌单、收藏夹与关注内容。';
      profileBox.innerHTML = `
        <div class="signed-out-mark ${esc(source)}">${esc(meta.short)}</div>
        <span class="account-state ${status.cls}"><i></i>${esc(status.label)}</span>
        <h3>${esc(meta.label)}</h3>
        <p>${esc(emptyText)}</p>
        <div class="row signed-out-actions">
          ${!supportsQR ? '<button class="btn account-btn secondary" onclick="App.switchPlatformAccount(\'__other__\')">前往其他平台配置</button>' : ''}
          ${account.configured ? `<button class="btn account-btn danger" onclick="App.logoutPlatformAccount()">清除失效凭据</button>` : ''}
        </div>`;
    }

    const qrHint = $('platform-qr-hint');
    if (qrHint) qrHint.textContent = `请使用${meta.app}扫码；二维码只绑定当前站内用户和本次登录。`;
    const actions = $('platform-qr-actions');
    actions.innerHTML = (account.qr_sources || []).map((qrSource, index) =>
      `<button class="btn account-btn ${index === 0 ? 'primary' : 'secondary'} qr-login-btn" onclick="App.startPlatformLogin('${esc(qrSource)}')">${esc(QR_LABELS[qrSource] || '生成登录二维码')}</button>`
    ).join('');
    $('platform-cookie-input').placeholder = `${meta.label} Cookie，例如：${meta.cookie}`;
    switchPlatformMethod(state.activePlatformMethod);
  }

  async function saveOtherPlatformCookies() {
    const input = $('other-cookie-json'), result = $('other-cookie-result'), button = $('other-cookie-save');
    let cookies;
    try {
      cookies = JSON.parse(input.value || '{}');
      if (!cookies || Array.isArray(cookies) || typeof cookies !== 'object' || !Object.keys(cookies).length) throw new Error('请输入至少一个平台 Cookie');
    } catch (err) {
      result.innerHTML = `<span style="color:var(--err)">JSON 格式错误：${esc(err.message)}</span>`;
      return;
    }
    button.disabled = true;
    result.textContent = '正在验证并保存…';
    try {
      const response = await api('/platform-accounts/other-cookies', {method:'POST', body:JSON.stringify({cookies})});
      const savedLabels = (response.saved || []).map((key) => platformMeta(key).label);
      const errors = Object.entries(response.errors || {}).map(([key, value]) => `${platformMeta(key).label || key}：${value}`);
      if (savedLabels.length) input.value = '';
      result.innerHTML = [
        savedLabels.length ? `<span style="color:var(--ok)">已保存：${esc(savedLabels.join('、'))}</span>` : '',
        errors.length ? `<span style="color:var(--err)">${esc(errors.join('；'))}</span>` : '',
      ].filter(Boolean).join('<br>');
      await loadPlatformAccounts();
    } catch (err) { result.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
    finally { button.disabled = false; }
  }

  async function savePlatformCookie() {
    const source = state.activePlatform;
    const input = $('platform-cookie-input');
    const result = $('platform-account-result');
    const button = $('platform-cookie-save');
    const cookie = input.value.trim();
    if (!cookie) { result.innerHTML = '<span style="color:var(--warn)">请先粘贴完整 Cookie。</span>'; return; }
    button.disabled = true;
    result.textContent = '正在验证账号…';
    try {
      await api(`/platform-accounts/${source}/cookie`, {method:'POST', body:JSON.stringify({cookie})});
      input.value = '';
      result.innerHTML = '<span style="color:var(--ok)">验证成功，账号凭据已加密保存。</span>';
      await loadPlatformAccounts();
    } catch (err) {
      result.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`;
    } finally { button.disabled = false; }
  }

  async function logoutPlatformAccount() {
    const source = state.activePlatform;
    const label = platformMeta(source).label;
    if (!confirm(`确定退出 ${label}？该操作只清除当前用户的 ${label} 凭据，不会删除已有订阅和音乐。`)) return;
    try {
      await api(`/platform-accounts/${source}`, {method:'DELETE'});
      await loadPlatformAccounts();
      toast(`已退出 ${label}`);
    } catch (err) { $('platform-account-result').innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  async function startPlatformLogin(source) {
    closePlatformLogin();
    const modal = $('platform-login-modal');
    modal.classList.add('show');
    $('platform-login-title').textContent = ({
      netease:'网易云音乐', qq:'QQ 音乐', qq_wx:'微信登录 QQ 音乐', kugou:'酷狗音乐',
      bilibili:'哔哩哔哩', soda:'汽水音乐',
    }[source] || source) + '扫码登录';
    $('platform-login-loading').style.display = '';
    $('platform-login-image').style.display = 'none';
    $('platform-login-mfa').style.display = 'none';
    $('platform-login-code').value = '';
    $('platform-login-status').className = 'notice info';
    $('platform-login-status').textContent = '正在生成二维码…';
    try {
      const session = await api(`/platform-login/${source}/start`, {method:'POST'});
      state.platformLogin = {source, key:session.key, timer:null};
      $('platform-login-loading').style.display = 'none';
      if (session.image_url) { $('platform-login-image').src=session.image_url; $('platform-login-image').style.display='block'; }
      $('platform-login-status').textContent = '请使用对应官方 App 扫码并在手机上确认';
      state.platformLogin.timer = setInterval(pollPlatformLogin, 2500);
      pollPlatformLogin();
    } catch (err) {
      $('platform-login-loading').style.display = 'none';
      $('platform-login-status').className = 'notice err';
      $('platform-login-status').textContent = err.message;
    }
  }

  async function pollPlatformLogin() {
    const stateNow = state.platformLogin;
    if (!stateNow || stateNow.busy) return;
    stateNow.busy = true;
    try {
      const r = await api(`/platform-login/${stateNow.source}/check`, {method:'POST',body:JSON.stringify({key:stateNow.key})});
      await handlePlatformLoginResult(r);
    } catch (err) {
      $('platform-login-status').textContent = err.message;
      $('platform-login-status').className = 'notice err';
      clearInterval(stateNow.timer); stateNow.timer=null;
    } finally { stateNow.busy=false; }
  }

  async function handlePlatformLoginResult(r) {
    const stateNow = state.platformLogin;
    if (!stateNow) return;
    const labels = {waiting:'等待扫码', scanned:'已扫码，请在手机确认', success:'登录成功，Cookie 已加密保存', expired:'二维码已过期', failed:'登录失败'};
    const message = String(r.message || '').trim();
    $('platform-login-status').textContent = message && message.toLowerCase() !== 'success' ? message : (labels[r.status] || r.status || '请稍候');
    $('platform-login-status').className = 'notice ' + (r.status === 'success' ? '' : (r.status === 'failed' || r.status === 'expired' ? 'err' : 'info'));

    const extra = r.extra || {};
    if (extra.need_sms === 'true' || extra.need_sms === true) {
      if (stateNow.timer) { clearInterval(stateNow.timer); stateNow.timer = null; }
      stateNow.mfa = extra;
      renderSodaMfa(extra);
      return;
    }
    if (['success','expired','failed'].includes(r.status)) {
      if (stateNow.timer) { clearInterval(stateNow.timer); stateNow.timer = null; }
      if (r.status === 'success') {
        $('platform-login-mfa').style.display = 'none';
        await loadPlatformAccounts();
        $('platform-account-result').innerHTML = '<span style="color:var(--ok)">扫码登录成功，账号凭据已加密保存。</span>';
      }
    }
  }

  function renderSodaMfa(extra) {
    const box = $('platform-login-mfa');
    box.style.display = '';
    $('platform-login-mfa-text').textContent = extra.need_sms_code === 'true'
      ? `验证码已发送${extra.mobile ? `至 ${extra.mobile}` : ''}，请输入后完成登录。`
      : '手机确认已完成，还需要通过短信完成账号安全验证。';
    const hasUpSms = !!(extra.up_sms_mobile || extra.up_sms_content);
    const preferUpSms = extra.sms_mode === 'up' || extra.need_user_sms === 'true';
    $('platform-login-up-sms').style.display = hasUpSms ? '' : 'none';
    $('platform-login-sms-mobile').textContent = extra.up_sms_mobile || '—';
    $('platform-login-sms-content').textContent = extra.up_sms_content || '—';
    $('platform-login-code-box').style.display = preferUpSms && extra.can_up_sms !== 'true' ? 'none' : '';
    $('platform-login-code-row').style.display = extra.need_sms_code === 'true' ? 'grid' : 'none';
    $('platform-login-send-code').style.display = extra.need_sms_code === 'true' ? 'none' : '';
  }

  async function platformLoginAction(action) {
    const stateNow = state.platformLogin;
    if (!stateNow || stateNow.busy) return;
    const code = $('platform-login-code').value.trim();
    if (action === 'validate' && !/^\d{4,8}$/.test(code)) {
      $('platform-login-status').className = 'notice err';
      $('platform-login-status').textContent = '请输入 4–8 位数字验证码';
      return;
    }
    stateNow.busy = true;
    document.querySelectorAll('#platform-login-mfa button').forEach((button) => { button.disabled = true; });
    $('platform-login-status').className = 'notice info';
    $('platform-login-status').textContent = action === 'send_code' ? '正在发送验证码…' : '正在验证…';
    try {
      const r = await api(`/platform-login/${stateNow.source}/action`, {
        method:'POST', body:JSON.stringify({key:stateNow.key, action, code}),
      });
      await handlePlatformLoginResult(r);
    } catch (err) {
      $('platform-login-status').className = 'notice err';
      $('platform-login-status').textContent = err.message;
    } finally {
      stateNow.busy = false;
      document.querySelectorAll('#platform-login-mfa button').forEach((button) => { button.disabled = false; });
    }
  }

  function closePlatformLogin() {
    if (state.platformLogin?.timer) clearInterval(state.platformLogin.timer);
    state.platformLogin = null;
    if ($('platform-login-mfa')) $('platform-login-mfa').style.display = 'none';
    $('platform-login-modal')?.classList.remove('show');
  }

  async function loadUsers() {
    if (state.user?.role !== 'admin') return;
    try {
      const r = await api('/auth/users');
      $('user-list-body').innerHTML = (r.items || []).map((u) => `<tr>
        <td><b>${esc(u.display_name || u.username)}</b><div class="small muted mono">${esc(u.username)} · ${esc(u.id)}</div></td>
        <td>${esc(u.role)}</td><td><span class="status ${u.enabled ? 'downloaded':'skipped'}">${u.enabled?'启用':'停用'}</span></td>
        <td class="small">${esc(u.created_at)}</td>
        <td><button class="btn small" onclick="App.toggleUser('${esc(u.id)}', ${u.enabled?'false':'true'})">${u.enabled?'停用':'启用'}</button>
        <button class="btn small" onclick="App.resetUserPassword('${esc(u.id)}')">重置密码</button></td></tr>`).join('');
    } catch (err) { toast(err.message); }
  }

  async function createUser() {
    try {
      await api('/auth/users', {method:'POST', body:JSON.stringify({
        username:$('new-user-name').value.trim(), display_name:$('new-user-display').value.trim(),
        role:$('new-user-role').value, password:$('new-user-password').value,
      })});
      $('new-user-name').value=''; $('new-user-display').value=''; $('new-user-password').value='';
      toast('用户已创建'); await loadUsers();
    } catch (err) { toast(err.message); }
  }

  async function toggleUser(id, enabled) {
    try { await api('/auth/users/'+id,{method:'PATCH',body:JSON.stringify({enabled})}); await loadUsers(); }
    catch(err){toast(err.message);}
  }

  async function resetUserPassword(id) {
    const password = prompt('输入新密码（至少 8 位）');
    if (!password) return;
    try { await api('/auth/users/'+id,{method:'PATCH',body:JSON.stringify({password})}); toast('密码已重置'); }
    catch(err){toast(err.message);}
  }

  /* ------------------------------------------------------------ 监控弹窗 */
  function openMonitorModal(monitor, preset) {
    state.editId = monitor ? monitor.id : null;
    $('monitor-modal-title').textContent = monitor ? '编辑监控' : '新建监控';
    $('monitor-modal').classList.add('show');

    const kind = monitor ? monitor.kind : (preset?.kind || 'chart');
    $('m-kind').value = kind;
    $('m-name').value = monitor ? monitor.name : '';
    $('m-quality').value = monitor ? monitor.quality : ($('s-quality').value || 'master');
    $('m-fallback').value = monitor ? monitor.fallback : 'best_effort';
    $('m-interval').value = monitor ? monitor.interval_minutes : ($('s-interval').value || 360);
    $('m-max').value = monitor ? monitor.max_downloads : ($('s-max').value || 30);
    $('m-include').value = monitor ? monitor.include_kw : '';
    $('m-exclude').value = monitor ? monitor.exclude_kw : '';
    $('m-enabled').checked = monitor ? monitor.enabled : true;
    $('m-auto').checked = monitor ? monitor.auto_download : true;
    $('m-embed').checked = monitor ? monitor.embed : true;
    $('m-preview').innerHTML = '';

    // 榜单选择
    state.chartSel = new Set();
    const custom = [];
    if (monitor && monitor.kind === 'chart') {
      (monitor.target.charts || []).forEach((c) => {
        if (c.key && state.chartIndex[c.key]) state.chartSel.add(c.key);
        else custom.push(c);
      });
    } else if (preset?.charts) {
      preset.charts.forEach((k) => state.chartSel.add(k));
    } else if (preset?.custom) {
      custom.push(...preset.custom);
    }
    renderChartPicker();
    $('m-chart-links').value = custom.map((c) => (c.name ? c.name + '|' : '') + (c.link || (c.platform + ':' + c.id))).join('\n');

    // 歌单
    const playlistLinks = monitor && monitor.kind === 'playlist' ? (monitor.target.playlists || []) :
      (preset?.playlists || []);
    $('m-playlist-links').value = playlistLinks.map((p) => (p.name ? p.name + '|' : '') + (p.link || (p.source + ':' + p.id))).join('\n');

    // 收藏夹
    state.favSel = new Set(monitor && monitor.kind === 'favorites' ? (monitor.target.playlist_ids || []) : (preset?.playlist_ids || []));
    renderFavPicker();

    // 歌手关注
    const artists = monitor && monitor.kind === 'artist' ? (monitor.target.artists || []) :
      (preset?.artists || []);
    $('m-artist-names').value = artists
      .map((a) => (a.name || '') + (a.sources && a.sources.length ? '|' + a.sources.join(',') : ''))
      .join('\n');
    $('m-artist-page-size').value = monitor && monitor.kind === 'artist'
      ? (monitor.target.page_size || 100)
      : (preset?.page_size || 100);

    // 平台
    const sources = monitor ? monitor.sources : (preset?.sources || state.platforms.map((p) => p.key));
    state.sourcesSel = new Set(sources && sources.length ? sources : state.platforms.map((p) => p.key));
    renderPlatformChips('m-sources', state.sourcesSel);

    onKindChange();
  }

  function renderChartPicker() {
    const box = $('m-chart-picker');
    box.innerHTML = state.chartGroups.map((g) => `
      <div style="margin-bottom:8px">
        <div class="muted small" style="margin-bottom:4px">${esc(g.label)}</div>
        <div class="chips">${g.charts.map((c) => `
          <label class="chip ${state.chartSel.has(c.key) ? 'on' : ''}" data-key="${esc(c.key)}">${esc(c.name)}</label>
        `).join('')}</div>
      </div>`).join('');
    box.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', (ev) => {
        ev.preventDefault();
        const key = chip.dataset.key;
        const on = !state.chartSel.has(key);
        if (on) state.chartSel.add(key); else state.chartSel.delete(key);
        chip.classList.toggle('on', on);
      });
    });
  }

  function renderFavPicker() {
    const box = $('m-fav-picker');
    if (!state.favorites.length) {
      box.className = 'muted small';
      box.textContent = '先到「歌单 / 收藏夹」页面点「读取我的收藏」，这里会出现可勾选项。';
      return;
    }
    box.className = 'chips';
    box.innerHTML = state.favorites.map((p) => `
      <label class="chip ${state.favSel.has(p.key) ? 'on' : ''}" data-key="${esc(p.key)}">
        ${esc(p.name)}<span class="tag">${esc(sourceLabel(p.source))}</span></label>`).join('');
    box.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', (ev) => {
        ev.preventDefault();
        const key = chip.dataset.key;
        const on = !state.favSel.has(key);
        if (on) state.favSel.add(key); else state.favSel.delete(key);
        chip.classList.toggle('on', on);
      });
    });
  }

  function onKindChange() {
    const kind = $('m-kind').value;
    $('m-block-chart').style.display = kind === 'chart' ? 'block' : 'none';
    $('m-block-playlist').style.display = kind === 'playlist' ? 'block' : 'none';
    $('m-block-favorites').style.display = kind === 'favorites' ? 'block' : 'none';
    $('m-block-artist').style.display = kind === 'artist' ? 'block' : 'none';
    syncChip('m-enabled-chip', 'm-enabled');
    syncChip('m-auto-chip', 'm-auto');
    syncChip('m-embed-chip', 'm-embed');
  }

  function syncChip(chipId, inputId) {
    const chip = $(chipId);
    const input = $(inputId);
    if (!chip || !input) return;
    chip.classList.toggle('on', input.checked);
    chip.onclick = (ev) => {
      ev.preventDefault();
      input.checked = !input.checked;
      chip.classList.toggle('on', input.checked);
    };
  }

  function buildDraft() {
    const kind = $('m-kind').value;
    const sources = [...state.sourcesSel];
    const target = {};
    if (kind === 'chart') {
      const charts = [...state.chartSel].map((key) => {
        const c = state.chartIndex[key];
        return { key, name: c.name, platform: c.platform, id: c.id || '', link: c.link || '', rank: c.rank || '' };
      });
      parseLinkLines($('m-chart-links').value).forEach((x) => charts.push({ name: x.name, link: x.link }));
      target.charts = charts;
    } else if (kind === 'playlist') {
      target.playlists = parseLinkLines($('m-playlist-links').value).map((x) => {
        if (/^(https?:)?\/\//.test(x.link)) return x;
        const [source, id] = x.link.split(':');
        return { name: x.name, source, id, link: '' };
      });
    } else if (kind === 'artist') {
      // 每行「歌手名」或「歌手名|平台1,平台2」；不指定平台就用监控级的平台列表。
      target.artists = parseLinkLines($('m-artist-names').value).map((x) => {
        const name = (x.name || x.link || '').trim();
        const srcs = x.name ? (x.link || '').split(',').map((s) => s.trim()).filter(Boolean) : [];
        return { name, sources: srcs };
      }).filter((a) => a.name);
      target.page_size = Math.max(30, Math.min(1000, Number($('m-artist-page-size').value) || 100));
    } else {
      target.playlist_ids = [...state.favSel];
    }
    return {
      name: $('m-name').value.trim(),
      kind,
      enabled: $('m-enabled').checked,
      sources,
      target,
      quality: $('m-quality').value,
      fallback: $('m-fallback').value,
      auto_download: $('m-auto').checked,
      embed: $('m-embed').checked,
      interval_minutes: Number($('m-interval').value) || 360,
      max_downloads: Number($('m-max').value) || 30,
      include_kw: $('m-include').value,
      exclude_kw: $('m-exclude').value,
    };
  }

  async function previewDraft() {
    const draft = buildDraft();
    const box = $('m-preview');
    box.innerHTML = '正在干跑…';
    try {
      const r = await api('/preview', {
        method: 'POST',
        body: JSON.stringify({
          kind: draft.kind, sources: draft.sources, target: draft.target,
          include_kw: draft.include_kw, exclude_kw: draft.exclude_kw,
        }),
      });
      box.innerHTML = `共解析 <b>${r.total}</b> 首，过滤后 <b>${r.filtered}</b> 首。` +
        (r.warnings?.length ? `<br><span style="color:var(--warn)">${r.warnings.map(esc).join('<br>')}</span>` : '') +
        (r.songs?.length ? `<br><span class="muted">前几首：${r.songs.slice(0, 8).map((s) => esc(s.name)).join('、')}</span>` : '');
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  async function saveMonitor() {
    const draft = buildDraft();
    if (!draft.name) { toast('请填写监控名称'); return; }
    try {
      if (state.editId) {
        await api(`/monitors/${state.editId}`, { method: 'PATCH', body: JSON.stringify(draft) });
        toast('已更新');
      } else {
        const r = await api('/monitors', { method: 'POST', body: JSON.stringify(draft) });
        toast('已创建，将在下一轮调度时执行');
        if (draft.enabled && r.id) setTimeout(() => runMonitor(r.id), 400);
      }
      closeModal();
      loadMonitors();
    } catch (err) { toast('保存失败：' + err.message); }
  }

  function closeModal() { $('monitor-modal').classList.remove('show'); state.editId = null; }

  async function editMonitor(id) {
    try {
      const r = await api('/monitors/' + id);
      openMonitorModal(r.monitor);
    } catch (err) { toast(err.message); }
  }

  /* ------------------------------------------------------------ 启动 */
  async function init() {
    initTabs();
    document.querySelectorAll('nav.tabs button').forEach((b) => {
      if (b.dataset.tab === 'charts') b.addEventListener('click', renderCharts);
      if (b.dataset.tab === 'playlists') b.addEventListener('click', () => {
        renderPlatformChips('fav-platforms', new Set(state.favPlatforms), (key, on) => {
          state.favPlatforms = on ? [...state.favPlatforms, key] : state.favPlatforms.filter((k) => k !== key);
        });
      });
    });
    $('monitor-modal').addEventListener('click', (ev) => {
      if (ev.target.id === 'monitor-modal') closeModal();
    });
    $('chart-preview-create')?.addEventListener('click', () => {
      if (state.resolvedChartEntry) {
        openMonitorModal(null, { kind: 'chart', custom: [state.resolvedChartEntry] });
      }
    });
    if (await initAuth()) loadBase().then(() => { checkEngine(); loadMonitors(); });
    setInterval(() => {
      if (document.querySelector('#page-records.active')) loadLxDownloads();
    }, 2000);
  }

  document.addEventListener('DOMContentLoaded', init);

  return {
    refreshAll, loadMonitors, runMonitor, previewMonitor, toggleMonitor, deleteMonitor, showRuns,
    verifyCharts, verifyChart, previewChart, monitorFromChart, resolveCustomChart, monitorFromCustomChart,
    resolvePlaylist, monitorFromPlaylist, loadFavorites, createFavoritesMonitor,
    loadRuns, loadTracks, showRunLog, retryTracks, retryFailed, loadLxDownloads, clearLxDownloads,
    loadSettings, saveSettings, checkEngine,
    loadLxSettings, saveLxSettings, reloadLxSources, importLxSourceUrl, importLxSourceText,
    importLxSourceFile, toggleLxSource, deleteLxSource,
    openMonitorModal, editMonitor, closeModal, saveMonitor, previewDraft, onKindChange,
    currentChartKeys,
    submitAuth, logoutUser, loadUsers, createUser, toggleUser, resetUserPassword,
    loadPlatformAccounts, switchPlatformAccount, switchPlatformMethod, savePlatformCookie, saveOtherPlatformCookies,
    logoutPlatformAccount, startPlatformLogin, platformLoginAction, closePlatformLogin,
  };
})();
