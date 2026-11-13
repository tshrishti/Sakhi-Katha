let memberData = null;
let memberLive = null;
let memberTabsReady = false;
let taskFilter = 'all';

const memberMoney = value => `₹${Number(value || 0).toLocaleString('en-IN')}`;
const memberDate = value => value ? new Date(value * 1000).toLocaleDateString('en-IN', {day: 'numeric', month: 'short'}) : 'Not set';
const memberTime = value => {
  if (!value) return 'No report yet';
  const minutes = Math.max(0, Math.floor(Date.now() / 1000 - value) / 60);
  return minutes < 1 ? 'just now' : `${Math.floor(minutes)} min ago`;
};
const taskQuality = task => task.quality?.status || '';

function memberUrgent(data) {
  const pendingAccept = data.tasks.find(task => ['planned', 'in_production'].includes(task.status) && !task.accepted);
  if (pendingAccept) return {task: pendingAccept, label: `Accept your ${pendingAccept.stage} task`, action: 'accept'};
  const qualityWaiting = data.tasks.find(task => task.status === 'in_production' && taskQuality(task) === 'pending');
  if (qualityWaiting) return {task: qualityWaiting, label: 'Your sample is waiting for the leader', action: 'waiting'};
  const sampleDue = data.tasks.find(task => task.status === 'in_production' && !task.quality);
  if (sampleDue) return {task: sampleDue, label: 'Send your first sample photo', action: 'sample'};
  const workDue = data.tasks.find(task => task.status === 'in_production' && task.done < task.units);
  if (workDue) return {task: workDue, label: 'Report the pieces you finished', action: 'report'};
  const payDue = data.earnings.find(entry => entry.status === 'payout_pending' && !entry.approved_by_me);
  if (payDue) return {task: payDue, label: 'Review your pay for this order', action: 'pay'};
  return null;
}

function memberShell() {
  if (document.getElementById('member-dashboard')) return;
  document.getElementById('m').innerHTML = `<section id="member-dashboard">
    <div class="dashboard-heading">
      <div><p class="muted">Your work and pay</p><h1>Member dashboard</h1></div>
      <span id="member-live" class="live-status">Connecting</span>
    </div>
    <div id="member-offline" class="offline-banner" hidden>Live updates are reconnecting.</div>
    <nav class="dashboard-tabs" role="tablist" aria-label="Member dashboard">
      <button role="tab" data-tab="home" aria-controls="member-panel">Home <span id="home-badge" class="tab-badge" hidden></span></button>
      <button role="tab" data-tab="tasks" aria-controls="member-panel">My tasks <span id="tasks-badge" class="tab-badge" hidden></span></button>
      <button role="tab" data-tab="earnings" aria-controls="member-panel">Earnings <span id="earnings-badge" class="tab-badge" hidden></span></button>
    </nav>
    <section id="member-panel" role="tabpanel" tabindex="0"></section>
  </section>`;
  document.getElementById('member-panel').addEventListener('click', event => {
    const reportButton = event.target.closest('[data-member-report]');
    if (reportButton) {
      memberReport(Number(reportButton.dataset.order), Number(reportButton.dataset.remaining), reportButton.dataset.stage);
    }
  });
  if (!memberTabsReady) {
    SakhiTabs.install(document.getElementById('member-dashboard'), renderMemberTab, 'home');
    memberTabsReady = true;
  }
}

function renderMemberTab(view = SakhiTabs.current('home')) {
  if (!memberData) return;
  const tabs = [...document.querySelectorAll('#member-dashboard [role="tab"]')];
  const selected = ['home', 'tasks', 'earnings'].includes(view.tab) ? view.tab : 'home';
  const panel = document.getElementById('member-panel');
  panel.classList.toggle('member-home-panel', selected === 'home');
  panel.classList.toggle('member-tasks-panel', selected === 'tasks');
  panel.classList.toggle('member-earnings-panel', selected === 'earnings');
  tabs.forEach(tab => {
    const active = tab.dataset.tab === selected;
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  const waitingTasks = memberData.tasks.filter(task => task.status === 'planned' && !task.accepted).length;
  const waitingPay = memberData.earnings.filter(entry => entry.status === 'payout_pending' && !entry.approved_by_me).length;
  setBadge('tasks-badge', waitingTasks);
  setBadge('earnings-badge', waitingPay);
  if (selected === 'home') renderMemberHome();
  else if (selected === 'tasks') renderMemberTasks();
  else renderMemberEarnings();
}

function setBadge(id, count) {
  const element = document.getElementById(id);
  element.textContent = count || '';
  element.hidden = !count;
}

function renderMemberHome() {
  const data = memberData;
  const urgent = memberUrgent(data);
  const active = data.tasks.filter(task => ['planned', 'in_production'].includes(task.status));
  const waiting = data.earnings.filter(entry => entry.status === 'payout_pending');
  const nowCard = !urgent ? `<article class="card"><h2>Do this now</h2><p>Nothing needs your attention right now.</p></article>`
    : `<article class="card urgent-card"><h2>Do this now</h2><p><b>${esc(urgent.label)}</b> · Order #${urgent.task.order_id}</p>
      ${urgent.action === 'accept' ? `<button onclick="memberAction(${urgent.task.order_id},'accept')">✓ Accept task</button>` : ''}
      ${urgent.action === 'sample' ? `<button onclick="memberSample(${urgent.task.order_id})">📷 Send sample</button>` : ''}
      ${urgent.action === 'report' ? `<button data-member-report data-order="${urgent.task.order_id}" data-remaining="${urgent.task.units-urgent.task.done}" data-stage="${esc(urgent.task.stage)}">＋ Report pieces</button>` : ''}
      ${urgent.action === 'pay' ? `<button onclick="location.hash='#earnings'">₹ Review earnings</button>` : ''}
    </article>`;
  document.getElementById('member-panel').innerHTML = `
    <section class="member-kpis">
      <article class="kpi"><strong>${active.length}</strong><span>Active tasks</span></article>
      <article class="kpi"><strong>${data.pieces_this_week}</strong><span>Pieces reported this week</span></article>
      <article class="kpi"><strong>${memberMoney(data.pay_waiting)}</strong><span>Pay waiting for approval</span></article>
    </section>
    ${nowCard}
    <section class="card"><h2>My active work</h2>
      ${active.length ? active.map(task => `<article class="member-task" data-order-id="${task.order_id}">
        <div class="dashboard-heading"><div><h3>Order #${task.order_id} · ${esc(task.product)} × ${task.quantity}</h3>
          <p>${esc(task.stage)} · ${task.done} of ${task.units} pieces · Due ${memberDate(task.due_at)}${task.days_left == null ? '' : ` · ${task.days_left} day(s) left`}</p>
        </div><span class="member-progress" role="img" aria-label="${task.units ? Math.round(task.done * 100 / task.units) : 0}% complete">${task.units ? Math.round(task.done * 100 / task.units) : 0}%</span></div>
        ${(task.journey || []).map(stage => `<div class="stage-line"><span>${esc(stage.name)}</span><div class="stage-bar"><span style="width:${stage.pct}%"></span></div><span>${stage.pct}%</span></div>`).join('')}
        ${task.quality ? `<p>Sample: ${esc(task.quality.status)}${task.quality.reason ? ` · ${esc(task.quality.reason)}` : ''}</p>` : ''}
        <div class="row">${!task.accepted ? `<button onclick="memberAction(${task.order_id},'accept')">✓ Accept</button>` : ''}
          <button data-member-report data-order="${task.order_id}" data-remaining="${task.units-task.done}" data-stage="${esc(task.stage)}">＋ Report pieces</button>
          <button class="secondary" onclick="memberSample(${task.order_id})">📷 Send photo</button></div>
      </article>`).join('') : '<p class="muted">No active work is assigned to you.</p>'}
    </section>
    <section class="card"><h2>Latest updates</h2>${data.latest_updates.length
      ? data.latest_updates.map(event => `<p class="member-update">${esc(event.summary)}${event.order_id ? ` · Order #${event.order_id}` : ''} <span class="muted">${memberTime(event.ts)}</span></p>`).join('')
      : '<p class="muted">Your latest work updates will appear here.</p>'}</section>
    ${data.leader.length ? `<section class="card"><h2>Quick help</h2>${data.leader.map(leader => `<p>Contact your group leader: <b>${esc(leader.name)}</b></p>
      <div class="row"><a class="btn" href="tel:${encodeURIComponent(leader.phone)}">☎ Call</a>
      <a class="btn secondary" href="https://wa.me/${encodeURIComponent(leader.phone.replace('+',''))}" target="_blank" rel="noopener">WhatsApp</a></div>`).join('')}` : ''}`;
}

function renderMemberTasks() {
  const options = [['all', 'All'], ['accept', 'To accept'], ['progress', 'In progress'], ['check', 'Waiting for check'], ['rework', 'Needs rework'], ['done', 'Done']];
  const filtered = memberData.tasks.filter(task => taskMatchesFilter(task, taskFilter));
  document.getElementById('member-panel').innerHTML = `<section class="card">
    <div class="dashboard-heading"><div><h2>My tasks</h2><p class="muted">Only your tasks and your own pay are shown here.</p></div></div>
    <div class="member-filters" role="group" aria-label="Filter tasks">${options.map(([key, label]) => `<button class="secondary" aria-pressed="${taskFilter === key}" onclick="taskFilter='${key}';renderMemberTasks()">${label}</button>`).join('')}</div>
    <div class="order-list">${filtered.length ? filtered.map(task => `<article class="card" data-order-id="${task.order_id}">
      <div class="dashboard-heading"><div><h3>Order #${task.order_id} · ${esc(task.product)} × ${task.quantity}</h3>
        <p>${esc(task.stage)} · ${task.done}/${task.units} pieces · ${memberDate(task.due_at)}${task.days_left == null ? '' : ` · ${task.days_left} day(s) left`}</p></div>
        ${chip(task.quality?.status || task.status)}</div>
      <p>Rate: ${memberMoney(task.rate)} per piece · Expected pay: ${memberMoney(task.expected_pay)}</p>
      <p><b>Why you got this task:</b> ${esc(task.why)}</p>
      ${task.checklist?.length ? `<details><summary>What a good piece looks like</summary><ul>${task.checklist.map(item => `<li>${esc(item)}</li>`).join('')}</ul></details>` : ''}
      ${task.quality?.reason ? `<p class="warn">Leader's reason: ${esc(task.quality.reason)} · ${task.quality.rework || 0} piece(s) need rework</p>` : ''}
      <div class="row">      ${['planned','in_production'].includes(task.status) && !task.accepted ? `<button onclick="memberAction(${task.order_id},'accept')">✓ Accept</button>` : ''}
        ${task.status === 'planned' && task.accepted ? '<span class="muted">You accepted this task.</span>' : ''}
      ${task.status === 'in_production' ? `<button data-member-report data-order="${task.order_id}" data-remaining="${task.units-task.done}" data-stage="${esc(task.stage)}">＋ Report pieces</button><button class="secondary" onclick="memberSample(${task.order_id})">📷 Send sample photo</button>` : ''}
      ${['planned','accepted'].includes(task.status) ? `<button class="red" onclick="memberAction(${task.order_id},'decline')">✕ Can't do this</button>` : ''}
        ${task.status === 'in_production' ? `<button class="secondary" onclick="memberIssue(${task.order_id})">⚑ Report a problem</button>` : ''}</div>
    </article>`).join('') : '<p class="muted">No tasks match this filter.</p>'}</div>
  </section>`;
}

function taskMatchesFilter(task, filter) {
  if (filter === 'accept') return task.status === 'planned' && !task.accepted;
  if (filter === 'progress') return task.status === 'in_production' && taskQuality(task) !== 'pending' && taskQuality(task) !== 'rework';
  if (filter === 'check') return taskQuality(task) === 'pending';
  if (filter === 'rework') return taskQuality(task) === 'rework';
  if (filter === 'done') return task.done >= task.units || ['settled', 'closed'].includes(task.status);
  return true;
}

function renderMemberEarnings() {
  const pays = memberData.earnings;
  const waiting = pays.filter(entry => entry.status === 'payout_pending');
  const approved = pays.filter(entry => ['settled', 'ready_for_delivery', 'dispatched', 'partially_delivered', 'closed'].includes(entry.status));
  document.getElementById('member-panel').innerHTML = `<section class="member-kpis">
    <article class="kpi"><strong>${memberMoney(waiting.reduce((sum, entry) => sum + Number(entry.line.total_expected), 0))}</strong><span>Waiting for approval</span></article>
    <article class="kpi"><strong>${memberMoney(approved.reduce((sum, entry) => sum + Number(entry.line.total_expected), 0))}</strong><span>Approved</span></article>
    <article class="kpi"><strong>${approved.length}</strong><span>Approved order(s)</span></article>
  </section><section class="card"><h2>My pay slips</h2>
    ${pays.length ? pays.map(entry => `<article class="card pay-slip" data-order-id="${entry.order_id}">
      <div class="dashboard-heading"><h3>Order #${entry.order_id} · ${esc(entry.line.stage)}</h3>${chip(entry.status)}</div>
      <p>${entry.line.accepted} accepted × ${memberMoney(entry.line.rate)} = <b>${memberMoney(entry.line.paid)}</b></p>
      <p>${entry.line.pending} piece(s) waiting for rework approval · ${memberMoney(entry.line.pending_amount)}</p>
      <p><b>Total expected: ${memberMoney(entry.line.total_expected)}</b></p><p>${esc(entry.explain)}</p>
      <div class="pay-progress" aria-label="Pay approval progress"><span class="${entry.status === 'settled' ? 'complete' : ''}">Work done</span><i></i><span class="${entry.line.pending === 0 && ['payout_pending','settled'].includes(entry.status) ? 'complete' : ''}">Quality passed</span><i></i><span class="${['payout_pending','settled'].includes(entry.status) ? 'complete' : ''}">Proposed</span><i></i><span class="${entry.status === 'settled' ? 'complete' : ''}">Group approved</span></div>
      ${entry.status === 'payout_pending' ? `<div class="row">${entry.approved_by_me ? '<span class="muted">You approved this pay.</span>' : `<button onclick="memberAction(${entry.order_id},'approve')">✓ This is correct</button>`}
        <button class="red" onclick="memberDispute(${entry.order_id})">I disagree</button></div>` : ''}
      ${(entry.disputes || []).map(dispute => `<p class="${dispute.open ? 'warn' : ''}">Dispute ${dispute.open ? 'waiting for the leader' : 'resolved'}: ${esc(dispute.reason)}${dispute.note ? ` · Leader's note: ${esc(dispute.note)}` : ''}</p>`).join('')}
    </article>`).join('') : '<p class="muted">No earnings have been recorded for you yet.</p>'}
  </section>`;
}

async function memberAction(orderId, action) {
  if (action === 'decline' && !confirm('Tell your leader you cannot do this task? There is no penalty.')) return;
  const routes = {accept: `/seller/orders/${orderId}/accept`, decline: `/seller/orders/${orderId}/decline`, approve: `/seller/orders/${orderId}/payout/approve`};
  await act(routes[action], 'POST', {}, loadMember);
}

function memberReport(orderId, remaining, stage) {
  const amount = prompt(`How many more pieces did you finish? You can report up to ${remaining}.`, String(Math.min(remaining, 1)));
  if (amount === null) return;
  const count = Number(amount);
  if (!Number.isInteger(count) || count < 1 || count > remaining) {
    toast(`Enter a whole number from 1 to ${remaining}.`, 1);
    return;
  }
  act(`/seller/orders/${orderId}/done`, 'POST', {n: count, stage}, loadMember);
}

function memberIssue(orderId) {
  const text = prompt('Describe the problem, delay or materials you need.');
  if (text?.trim()) act(`/seller/orders/${orderId}/issue`, 'POST', {text}, loadMember);
}

function memberDispute(orderId) {
  const reason = prompt('Tell your leader what is wrong with this pay entry.');
  if (reason?.trim()) act(`/seller/orders/${orderId}/dispute`, 'POST', {reason}, loadMember);
}

function memberSample(orderId) {
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = 'image/*';
  fileInput.onchange = () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    const image = new Image();
    image.onload = () => {
      const scale = Math.min(1, 640 / Math.max(image.width, image.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(image.width * scale);
      canvas.height = Math.round(image.height * scale);
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
      const note = prompt('Add a short note (optional).') || '';
      act(`/seller/orders/${orderId}/sample`, 'POST', {note, photo: canvas.toDataURL('image/jpeg', 0.7)}, loadMember);
    };
    image.onerror = () => toast('That file is not a readable photo.', 1);
    image.src = URL.createObjectURL(file);
  };
  fileInput.click();
}

async function loadMember() {
  try {
    memberData = await api('/seller/home');
    memberShell();
    renderMemberTab(SakhiTabs.current('home'));
    if (!memberLive && window.SakhiLive) {
      memberLive = SakhiLive.start({
        scope: ['tasks', 'earnings', 'activity'],
        since: memberData.last_seq,
        onEvent: batch => {
          if (batch.some(event => event.type === 'resync' || event.type === 'poll' || event.refresh?.some(scope => ['tasks', 'earnings'].includes(scope)))) {
            loadMember();
          }
        },
        onState: info => {
          const label = document.getElementById('member-live');
          const banner = document.getElementById('member-offline');
          if (!label || !banner) return;
          label.textContent = `${info.state === 'live' ? 'Live' : info.state === 'polling' ? 'Reconnecting' : 'Offline'} · updated ${info.secondsAgo}s ago`;
          label.className = `live-status ${info.state === 'live' ? '' : 'reconnecting'}`;
          banner.hidden = info.state === 'live';
        }
      });
    }
  } catch (error) {
    toast(error.message, 1);
  }
}

boot('seller', 'Member').then(loadMember);
