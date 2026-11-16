let buyerData = null;
let buyerProducts = [];
let buyerLive = null;
let buyerTabsReady = false;

const buyerMoney = value => `₹${Number(value || 0).toLocaleString('en-IN')}`;
const buyerDate = value => value ? new Date(value * 1000).toLocaleDateString('en-IN', {day:'numeric', month:'short', year:'numeric'}) : 'Not set';
const buyerSafe = value => esc(value || '');

function buyerShell() {
  if (document.getElementById('buyer-dashboard')) return;
  document.getElementById('m').innerHTML = `<section id="buyer-dashboard">
    <div class="dashboard-heading"><div><p class="muted">Your orders, from request to delivery</p><h1>Buyer dashboard</h1></div><span id="buyer-live" class="live-status">Connecting</span></div>
    <div id="buyer-offline" class="offline-banner" hidden>Live updates are reconnecting.</div>
    <nav class="dashboard-tabs" role="tablist" aria-label="Buyer dashboard">
      <button role="tab" data-tab="home" aria-controls="buyer-panel">Home</button>
      <button role="tab" data-tab="orders" aria-controls="buyer-panel">Orders <span id="buyer-orders-badge" class="tab-badge" hidden></span></button>
      <button role="tab" data-tab="new" aria-controls="buyer-panel">New order</button>
      <button role="tab" data-tab="group" aria-controls="buyer-panel">Group</button>
      <button role="tab" data-tab="me" aria-controls="buyer-panel">Me</button>
    </nav>
    <section id="buyer-panel" role="tabpanel" tabindex="0"></section>
  </section>`;
  if (!buyerTabsReady) {
    SakhiTabs.install(document.getElementById('buyer-dashboard'), renderBuyerTab, 'home');
    buyerTabsReady = true;
  }
}

function buyerPending(order) {
  if (order.status === 'quoted') return 'Quote ready for your approval';
  if (order.status === 'dispatched') return 'Confirm what arrived';
  if (order.status === 'blocked') return 'Your group has reported a delivery hold';
  return '';
}

function buyerOrderCard(order) {
  const shipments = order.delivery?.shipments || [];
  const lastShipment = shipments[shipments.length - 1];
  const cancellable = ['submitted','quoted','accepted','planned'].includes(order.status);
  return `<article class="card buyer-order" data-order-id="${order.id}">
    <div class="dashboard-heading"><div><h3>Order #${order.id} · ${buyerSafe(order.order.product || 'Order')}</h3>
      <p>${Number(order.order.qty)} pieces · ${buyerSafe(order.group)}</p></div>${chip(order.status, order.status_label)}</div>
    <p>Delivery target: ${buyerDate(order.due_at)}${order.days_left == null ? '' : ` · ${order.days_left} day(s) left`}</p>
    <div class="buyer-progress" role="img" aria-label="${Number(order.progress_pct || 0)} percent of reported work complete"><span style="width:${Math.max(0,Math.min(100,Number(order.progress_pct||0)))}%"></span></div>
    <p>${Number(order.progress_pct || 0)}% work reported complete · progress is based on member reports</p>
    ${order.quote ? `<p>Your quote: ${buyerMoney(order.quote)} per piece</p>` : ''}
    ${order.buyer_payment?.status && order.buyer_payment.status !== 'not_recorded' ? `<p>Payment record: ${buyerSafe(order.buyer_payment.status)} · ${buyerMoney(order.buyer_payment.amount)}${order.buyer_payment.note ? ` · ${buyerSafe(order.buyer_payment.note)}` : ''}</p>` : ''}
    ${order.status === 'quoted' ? `<button onclick="buyerAccept(${order.id})">✓ Accept quote</button>` : ''}
    ${lastShipment && order.status === 'dispatched' ? `<button onclick="buyerSignoff(${order.id},${Number(lastShipment.qty)})">Confirm delivery</button>` : ''}
    ${cancellable ? `<button class="red" onclick="buyerCancel(${order.id})">Cancel before work starts</button>` : ''}
    ${order.status === 'blocked' ? `<p class="warn">On hold${order.delivery?.blocked?.reason ? `: ${buyerSafe(order.delivery.blocked.reason)}` : ''}</p>` : ''}
    ${shipments.map(shipment => `<p class="muted">Shipment ${Number(shipment.i)+1}: ${shipment.qty} pieces${shipment.courier ? ` via ${buyerSafe(shipment.courier)}` : ''}${shipment.tracking ? ` · ${buyerSafe(shipment.tracking)}` : ''}${shipment.received != null ? ` · You received ${Number(shipment.received)}` : ''}${shipment.has_photo ? ` · <a href="/api/buyer/orders/${order.id}/delivery/${Number(shipment.i)}/photo" target="_blank" rel="noopener">View delivery photo</a>` : ''}</p>`).join('')}
    ${order.status === 'closed' && !order.rating ? `<div class="row"><label for="rating-${order.id}">Rate your group</label><select id="rating-${order.id}"><option value="">Choose 1–5</option><option value="5">5 · Excellent</option><option value="4">4 · Good</option><option value="3">3 · Okay</option><option value="2">2 · Poor</option><option value="1">1 · Very poor</option></select><input id="rating-note-${order.id}" maxlength="300" placeholder="Optional comment"><button class="secondary" onclick="buyerRate(${order.id})">Submit rating</button></div>` : ''}
    ${order.rating ? `<p>Thank you for rating this group ${order.rating.stars}/5${order.rating.comment ? ` · ${buyerSafe(order.rating.comment)}` : ''}</p>` : ''}
  </article>`;
}

function renderBuyerTab(view = SakhiTabs.current('home')) {
  if (!buyerData) return;
  const tabs = [...document.querySelectorAll('#buyer-dashboard [role="tab"]')];
  const selected = ['home','orders','new','group','me'].includes(view.tab) ? view.tab : 'home';
  tabs.forEach(tab => {
    const active = tab.dataset.tab === selected;
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  const needs = buyerData.orders.filter(order => buyerPending(order)).length;
  const badge = document.getElementById('buyer-orders-badge');
  badge.textContent = needs || '';
  badge.hidden = !needs;
  const panel = document.getElementById('buyer-panel');
  panel.className = `buyer-panel buyer-${selected}-panel`;
  if (selected === 'home') renderBuyerHome();
  else if (selected === 'orders') renderBuyerOrders();
  else if (selected === 'new') renderBuyerNewOrder();
  else if (selected === 'group') renderBuyerGroups();
  else renderBuyerMe();
}

function renderBuyerHome() {
  const active = buyerData.orders.filter(order => !['closed','cancelled'].includes(order.status));
  const needs = buyerData.orders.filter(order => buyerPending(order));
  const activity = buyerData.activity || [];
  document.getElementById('buyer-panel').innerHTML = `<section class="buyer-kpis">
    <article class="kpi"><strong>${active.length}</strong><span>Active orders</span></article>
    <article class="kpi"><strong>${needs.length}</strong><span>Needs your attention</span></article>
    <article class="kpi"><strong>${buyerData.orders.filter(order => order.status === 'closed').length}</strong><span>Delivered and closed</span></article>
  </section><section class="card"><h2>Needs you</h2>${needs.length ? needs.map(order => `<p class="decision-row"><span>${buyerPending(order)} · Order #${order.id}</span><button class="secondary" onclick="location.hash='#orders'">Open</button></p>`).join('') : '<p class="muted">Nothing needs your attention right now.</p>'}</section>
  <section class="card"><h2>Active orders</h2>${active.length ? active.slice(0,4).map(buyerOrderCard).join('') : '<p class="muted">No active orders. Start a new order when you are ready.</p>'}<button class="secondary" onclick="location.hash='#orders'">View all orders</button></section>
  <section class="card"><h2>Recent updates</h2>${activity.length ? activity.map(item => `<p class="member-update">${buyerSafe(item.summary)}${item.order_id ? ` · Order #${item.order_id}` : ''} <span class="muted">${buyerDate(item.ts)}</span></p>`).join('') : '<p class="muted">Your order updates will appear here.</p>'}</section>`;
}

function renderBuyerOrders() {
  const orders = buyerData.orders;
  document.getElementById('buyer-panel').innerHTML = `<section class="card"><div class="dashboard-heading"><div><h2>My orders</h2><p class="muted">Only your order and delivery information is shown here.</p></div>
    <select id="buyer-status-filter" aria-label="Filter orders" onchange="buyerFilterOrders(this.value)"><option value="">All statuses</option>${[...new Map(orders.map(order=>[order.status,order.status_label||order.status.replaceAll('_',' ')]))].map(([status,label])=>`<option value="${esc(status)}">${esc(label)}</option>`).join('')}</select>
  </div><div id="buyer-order-list" class="buyer-order-list">${orders.length ? orders.map(buyerOrderCard).join('') : '<p class="muted">No orders yet.</p>'}</div></section>`;
}

function buyerFilterOrders(status) {
  document.querySelectorAll('#buyer-order-list [data-order-id]').forEach(card => {
    const order = buyerData.orders.find(item => String(item.id) === card.dataset.orderId);
    card.hidden = Boolean(status && order?.status !== status);
  });
}

function renderBuyerNewOrder() {
  const groups = buyerData.groups || [];
  const groupOptions = groups.map(group => `<option value="${group.id}" ${Number(group.id) === Number(buyerData.preferred_group_id) ? 'selected' : ''}>${buyerSafe(group.name)} · ${buyerSafe(group.village)}</option>`).join('');
  const productOptions = buyerProducts.map(product => `<option value="${esc(product.key)}">${buyerSafe(product.label)}</option>`).join('');
  document.getElementById('buyer-panel').innerHTML = `<section class="card buyer-form">
    <div class="dashboard-heading"><div><h2>Place a new order</h2><p class="muted">The group sees the contact number on your account so it can confirm details.</p></div>
      <button class="secondary" onclick="buyerRepeatLast()">Repeat last order</button></div>
    <label class="field">Choose a group<select id="buyer-order-group" required>${groupOptions}</select></label>
    <div class="buyer-form-grid"><label class="field">Product<select id="buyer-product">${productOptions}</select></label>
      <label class="field">Quantity<input id="buyer-qty" type="number" min="1" max="100000" required></label>
      <label class="field">Your offer per piece (₹)<input id="buyer-offer" type="number" min="0.01" step="0.01" required></label>
      <label class="field">Delivery in days<input id="buyer-days" type="number" min="1" max="365" required></label>
      <label class="field">Colour<input id="buyer-colour" maxlength="80"></label>
      <label class="field">Delivery address<input id="buyer-address" maxlength="300"></label>
    </div>
    <label class="field">Specifications<textarea id="buyer-specs" maxlength="1000" placeholder="Size, packaging, or other requirements"></textarea></label>
    <button onclick="buyerPlaceOrder()">Send order</button>
  </section>`;
}

function renderBuyerGroups() {
  const groups = buyerData.groups || [];
  document.getElementById('buyer-panel').innerHTML = `<section class="card"><h2>Choose your group</h2><p class="muted">Supplier information uses recorded orders only. It is not a credit score. Groups with fewer than three deliveries are not rated.</p>
    <div class="buyer-group-list">${groups.map(group => `<article class="card buyer-group">
      <h3>${buyerSafe(group.name)}</h3><p>${buyerSafe(group.village)}</p>
      <p>${group.orders_delivered < 3 ? 'Too few orders to rate' : `${group.orders_delivered} recorded deliveries`}</p>
      ${group.orders_delivered >= 3 ? `<p>On-time delivery: ${group.on_time_pct == null ? 'Not enough due-date data' : `${group.on_time_pct}%`}</p><p>First-pass quality: ${group.first_pass_quality_pct == null ? 'Not recorded' : `${group.first_pass_quality_pct}%`}</p><p>Average buyer rating: ${group.average_rating == null ? 'No ratings yet' : `${group.average_rating}/5`}</p>` : ''}
      ${Number(group.id) === Number(buyerData.preferred_group_id) ? '<span class="chip approved">Selected</span>' : `<button class="secondary" onclick="buyerChooseGroup(${Number(group.id)})">Choose this group</button>`}
      ${group.passport_note ? `<p class="muted">${buyerSafe(group.passport_note)}</p>` : ''}
    </article>`).join('') || '<p class="muted">No groups are available.</p>'}</div>
  </section>`;
}

function renderBuyerMe() {
  const profile = buyerData.profile || {};
  document.getElementById('buyer-panel').innerHTML = `<section class="card"><h2>My account</h2>
    <p>Name: <b>${buyerSafe(profile.name)}</b></p><p>Language: ${buyerSafe(profile.language || 'English')}</p>
    <p>Consent record: ${profile.consented ? 'Recorded' : 'Not recorded'}</p>
    <p class="muted">The group fulfilling an order can see your contact number to confirm the order and delivery. Other groups cannot see your order details.</p>
    ${!profile.consented ? `<button onclick="api('/me/consent','POST').then(loadBuyer).catch(e=>toast(e.message,1))">Record consent</button>` : ''}
    <p class="muted">Change language from the menu in the top bar. English, हिन्दी and Hinglish are currently available.</p>
  </section>`;
}

async function buyerAccept(id) {
  try { await api(`/buyer/orders/${id}/accept`, 'POST'); toast('Quote accepted'); await loadBuyer(); }
  catch (error) { toast(error.message, 1); }
}

async function buyerCancel(id) {
  const reason = prompt('Optional reason for cancelling this order:');
  if (reason === null) return;
  try { await api(`/buyer/orders/${id}/cancel`, 'POST', {reason}); toast('Order cancelled'); await loadBuyer(); }
  catch (error) { toast(error.message, 1); }
}

async function buyerSignoff(id, sent) {
  const received = prompt(`How many pieces arrived? The group sent ${sent}.`, String(sent));
  if (received === null) return;
  if (!/^\d+$/.test(received) || Number(received) > sent) { toast(`Enter a whole number from 0 to ${sent}.`, 1); return; }
  const short = Number(received) < sent;
  const final = short && confirm('Accept the shortfall and close this order? Choose Cancel to ask the group to send the remaining pieces.');
  try {
    const result = await api(`/buyer/orders/${id}/signoff`, 'POST', {received:Number(received), final, note:''});
    toast(result.status === 'closed' ? 'Delivery confirmed. Please rate your group.' : 'Delivery recorded; the remaining pieces are still expected.');
    await loadBuyer();
  } catch (error) { toast(error.message, 1); }
}

async function buyerRate(id) {
  const stars = document.getElementById(`rating-${id}`).value;
  if (!stars) { toast('Choose a rating from 1 to 5.', 1); return; }
  const comment = document.getElementById(`rating-note-${id}`).value;
  try { await api(`/buyer/orders/${id}/rating`, 'POST', {stars:Number(stars), comment}); toast('Thank you for your feedback'); await loadBuyer(); }
  catch (error) { toast(error.message, 1); }
}

async function buyerChooseGroup(id) {
  try { await api('/buyer/group', 'POST', {group_id:id}); await loadBuyer(); toast('Group preference saved'); }
  catch (error) { toast(error.message, 1); }
}

async function buyerPlaceOrder() {
  const details = {
    group_id:Number(document.getElementById('buyer-order-group').value),
    product:document.getElementById('buyer-product').value,
    qty:document.getElementById('buyer-qty').value,
    offered_price:document.getElementById('buyer-offer').value,
    deadline_days:document.getElementById('buyer-days').value,
    colour:document.getElementById('buyer-colour').value,
    delivery_address:document.getElementById('buyer-address').value,
    specifications:document.getElementById('buyer-specs').value
  };
  try { const order = await api('/buyer/orders/form', 'POST', details); toast(`Order #${order.id} sent to ${order.group}`); location.hash = '#orders'; await loadBuyer(); }
  catch (error) { toast(error.message, 1); }
}

async function buyerRepeatLast() {
  try {
    const last = await api('/buyer/last-order');
    if (!last?.order) { toast('There is no previous order to repeat.'); return; }
    const order = last.order;
    if (buyerData.preferred_group_id == null) buyerData.preferred_group_id = last.group_id;
    document.getElementById('buyer-order-group').value = String(last.group_id);
    document.getElementById('buyer-product').value = order.product_key || order.product || buyerProducts[0]?.key || '';
    document.getElementById('buyer-qty').value = order.qty || '';
    document.getElementById('buyer-offer').value = order.offered_price || '';
    document.getElementById('buyer-days').value = order.deadline_days || '';
    document.getElementById('buyer-colour').value = order.colour || '';
    document.getElementById('buyer-address').value = order.delivery_address || '';
    document.getElementById('buyer-specs').value = order.specifications || '';
    toast('Previous order details copied. Check them before sending.');
  } catch (error) { toast(error.message, 1); }
}

async function loadBuyer() {
  try {
    const [data, products] = await Promise.all([api('/buyer/home'), buyerProducts.length ? Promise.resolve(buyerProducts) : api('/buyer/products')]);
    buyerData = data;
    buyerProducts = products;
    buyerShell();
    renderBuyerTab(SakhiTabs.current('home'));
    if (!buyerLive && window.SakhiLive) {
      buyerLive = SakhiLive.start({
        scope:['orders','activity'],
        onEvent: batch => {
          if (batch.some(item => item.type === 'resync' || item.type === 'poll' || item.refresh?.some(scope => ['orders','activity'].includes(scope)))) loadBuyer();
        },
        onState: info => {
          const label = document.getElementById('buyer-live');
          const banner = document.getElementById('buyer-offline');
          if (!label || !banner) return;
          label.textContent = `${info.state === 'live' ? 'Live' : info.state === 'polling' ? 'Reconnecting' : 'Offline'} · updated ${info.secondsAgo}s ago`;
          label.className = `live-status ${info.state === 'live' ? '' : 'reconnecting'}`;
          banner.hidden = info.state === 'live';
        }
      });
    }
  } catch (error) { toast(error.message, 1); }
}

boot('buyer', 'Buyer').then(loadBuyer);
