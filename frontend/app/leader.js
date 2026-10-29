/* Sakhi Khata leader hub: core. One shared workflow, ten tabs that are views of it.
   Every tab, the bell, the tab badges and the order drawer read the same server snapshot (/api/leader/view/<tab>) and the same live event stream. */
const TABS=['overview','orders','team','quality','delivery','payouts','buyers','pricing','materials','alerts'];
const MOBILE=['overview','orders','team','payouts'];
const L={en:{overview:'Overview',orders:'Orders',team:'Team',quality:'Quality',delivery:'Delivery',payouts:'Payouts',buyers:'Buyers',pricing:'Pricing',materials:'Materials',alerts:'Alerts',more:'More',
 live:'Live',updated:'updated',h_on_track:'On track',h_at_risk:'At risk',h_late:'Late',h_blocked:'Blocked',h_done:'Delivered',h_cancelled:'Cancelled',needs:'Needs your decision',
 k_active:'Active orders',k_today:'Pieces finished today',k_track:'On track',k_wait:'Waiting for you',k_pay:'Payout awaiting approval',k_due:'Buyer money due',new_order:'New order',search:'Search order, buyer, member',
 profile:'Profile',logout:'Logout',privacy:'Privacy notice',lang:'Language',wa_alerts:'WhatsApp alerts to me',working:'Working',behind:'Behind',idle:'Idle'},
hi:{overview:'अवलोकन',orders:'ऑर्डर',team:'टीम',quality:'गुणवत्ता',delivery:'डिलीवरी',payouts:'भुगतान',buyers:'खरीदार',pricing:'दाम',materials:'सामग्री',alerts:'अलर्ट',more:'और',
 live:'लाइव',updated:'अपडेट',h_on_track:'ठीक चल रहा',h_at_risk:'जोखिम',h_late:'देर',h_blocked:'रुका हुआ',h_done:'पहुँच गया',h_cancelled:'रद्द',needs:'आपके फैसले का इंतज़ार',
 k_active:'चालू ऑर्डर',k_today:'आज तैयार पीस',k_track:'ठीक चल रहे',k_wait:'आपके इंतज़ार में',k_pay:'भुगतान मंज़ूरी बाकी',k_due:'खरीदार से लेना बाकी',new_order:'नया ऑर्डर',search:'ऑर्डर, खरीदार, सदस्य खोजें',
 profile:'प्रोफ़ाइल',logout:'लॉग आउट',privacy:'गोपनीयता सूचना',lang:'भाषा',wa_alerts:'मुझे WhatsApp अलर्ट',working:'काम पर',behind:'पीछे',idle:'खाली'},
hing:{overview:'Overview',orders:'Orders',team:'Team',quality:'Quality',delivery:'Delivery',payouts:'Payouts',buyers:'Buyers',pricing:'Daam',materials:'Samaan',alerts:'Alerts',more:'Aur',
 live:'Live',updated:'update',h_on_track:'Theek chal raha',h_at_risk:'Risk',h_late:'Der',h_blocked:'Ruka hua',h_done:'Pahunch gaya',h_cancelled:'Cancel',needs:'Aapke faisle ka intezaar',
 k_active:'Chalu orders',k_today:'Aaj tayyar piece',k_track:'Theek chal rahe',k_wait:'Aapke intezaar mein',k_pay:'Payout approval baaki',k_due:'Buyer se lena baaki',new_order:'Naya order',search:'Order, buyer, member khojein',
 profile:'Profile',logout:'Logout',privacy:'Privacy notice',lang:'Bhasha',wa_alerts:'Mujhe WhatsApp alerts',working:'Kaam par',behind:'Peeche',idle:'Khali'}};
let LANG=localStorage.getItem('sk_lang')||'en';
const t=k=>(L[LANG]&&L[LANG][k])||L.en[k]||k;
const S={tab:localStorage.getItem('sk_tab')||'overview',data:{},stale:new Set(),badges:{},cursor:null,last:Date.now(),ok:true,me:null,items:{},drawer:null,flash:new Set(),deferred:false,view:{orders:'list',sort:'due',f:{}},buyer:null,prefs:{whatsapp_alerts:true},act:{}};
const Rs=x=>'Rs '+Number(x).toLocaleString('en-IN',{maximumFractionDigits:2});
const ago=ts=>{if(!ts)return '-';const s=Math.max(0,Date.now()/1000-ts);return s<90?'just now':s<3600?Math.round(s/60)+' min ago':s<86400?Math.round(s/3600)+' h ago':Math.round(s/86400)+' d ago'};
const dshort=ts=>ts?new Date(ts*1000).toLocaleDateString(undefined,{day:'numeric',month:'short'}):'-';
const dtime=ts=>ts?new Date(ts*1000).toLocaleString(undefined,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'-';
const hchip=h=>`<span class="chip h-${esc(h)}">${esc(t('h_'+h))}</span>`;
const bar=p=>`<div class="bar" role="progressbar" aria-valuenow="${p}" aria-valuemin="0" aria-valuemax="100"><i style="width:${Math.min(100,Math.max(0,p))}%"></i></div>`;
const daysTxt=c=>c.days_left==null?'':c.days_left<0?`${-c.days_left} d late`:c.days_left===0?'due today':`${c.days_left} d left`;
const val=id=>{const e=document.getElementById(id);return e?e.value:''};
const num=id=>{const v=val(id);return v===''?null:+v};
const fld=(id,label,type='text',v='',extra='')=>`<label class="field">${esc(label)}<input id="${id}" type="${type}" value="${esc(v)}" ${extra}></label>`;
const sel=(id,label,opts,v='')=>`<label class="field">${esc(label)}<select id="${id}">${opts.map(o=>`<option value="${esc(o[0])}" ${String(o[0])===String(v)?'selected':''}>${esc(o[1])}</option>`).join('')}</select></label>`;
const empty=m=>`<p class="muted">${esc(m)}</p>`;
const wa=(w,l='WhatsApp')=>w?`<a class="btn btn-s" href="${esc(w)}" target="_blank" rel="noopener">${l}</a>`:'';
const tel=p=>p?`<a class="btn btn-s" href="tel:${esc(p)}">Call</a>`:'';

/* ---------- data, live updates ---------- */
async function loadTab(tab,quiet){
  try{const d=await api('/leader/view/'+tab);S.data[tab]=d;S.stale.delete(tab);S.badges=d.badges;S.last=Date.now();S.ok=true;if(S.cursor==null)S.cursor=d.cursor;
    if(tab===S.tab)render();renderChrome()}catch(e){if(!quiet){S.ok=false;renderChrome();toast(e.message,1)}}}
const busy=()=>[...document.querySelectorAll('#view input,#view textarea,#drawer input,#drawer textarea,#modal input,#modal textarea')].some(e=>e.type!=='checkbox'&&e.type!=='file'&&e.value&&e.value!==e.defaultValue);
async function refresh(){await Promise.all([loadTab(S.tab,true),S.drawer&&S.drawer.oid?loadDrawer(true):null])}
async function poll(){
  if(document.hidden||S.cursor==null)return;
  try{const e=await api('/leader/events?after='+S.cursor);S.ok=true;S.last=Date.now();
    if(e.events&&e.events.length){S.cursor=e.cursor;S.badges=e.badges||S.badges;e.tabs.forEach(x=>S.stale.add(x));S.flash=new Set(e.events.map(x=>x.order_id).filter(Boolean));
      if(e.tabs.includes(S.tab)){if(busy())S.deferred=true;else await loadTab(S.tab,true)}
      if(S.drawer&&S.drawer.oid&&!busy())loadDrawer(true);
      if(S.drawer&&S.drawer.mode==='alerts')openBell(true);renderChrome()}
    else S.cursor=Math.max(S.cursor,e.cursor);
    if(S.deferred&&!busy()){S.deferred=false;await loadTab(S.tab,true)}}
  catch(_){S.ok=false}
  renderLive()}
function renderLive(){const e=document.getElementById('live');if(!e)return;const s=Math.round((Date.now()-S.last)/1000);
  e.innerHTML=`<span class="dot ${S.ok&&s<30?'':'off'}"></span>${esc(t('live'))} · ${esc(t('updated'))} ${s}s`}

/* ---------- chrome: header, tabs, bottom bar ---------- */
const bd=(n,mut)=>n?`<span class="pill ${mut?'mut':''}">${n}</span>`:'';
function renderChrome(){
  const g=(S.data[S.tab]||{}).group||{};
  document.getElementById('top').innerHTML=`<span class="brand">SAKHI KHATA</span><span class="grp">${esc(g.name||'')}</span><span id="live" class="live"></span><span class="sp"></span>
   <div class="search"><input id="q" type="search" placeholder="${esc(t('search'))}" aria-label="${esc(t('search'))}" autocomplete="off"><div id="res" class="results" hidden></div></div>
   <select id="lang" aria-label="${esc(t('lang'))}">${[['en','English'],['hi','हिन्दी'],['hing','Hinglish']].map(x=>`<option value="${x[0]}" ${x[0]===LANG?'selected':''}>${x[1]}</option>`).join('')}</select>
   <button class="ibtn" data-a="bell" aria-label="Alerts">🔔${bd(S.badges.alerts)}</button>
   <button class="ibtn" data-a="profile" aria-label="${esc(t('profile'))}">👤</button>`;
  document.getElementById('tabs').innerHTML=TABS.map(k=>`<button data-a="go" data-t="${k}" class="${k===S.tab?'on':''}">${esc(t(k))}${bd(S.badges[k])}</button>`).join('');
  const rest=TABS.filter(k=>!MOBILE.includes(k)),mb=rest.reduce((a,k)=>a+(S.badges[k]||0),0);
  document.getElementById('bottom').innerHTML=MOBILE.map(k=>`<button data-a="go" data-t="${k}" class="${k===S.tab?'on':''}">${esc(t(k))}${bd(S.badges[k])}</button>`).join('')+`<button data-a="more" class="${rest.includes(S.tab)?'on':''}">${esc(t('more'))}${bd(mb)}</button>`;
  renderLive()}
function go(tab){S.tab=tab;localStorage.setItem('sk_tab',tab);document.getElementById('sheet').hidden=true;closeDrawer();if(S.data[tab])render();else document.getElementById('view').innerHTML='<div class="card muted">Loading...</div>';
  renderChrome();window.scrollTo(0,0);if(!S.data[tab]||S.stale.has(tab))loadTab(tab)}
function render(){
  const d=S.data[S.tab];if(!d)return;S.items={};const v=document.getElementById('view');
  const keep={x:window.scrollX,y:window.scrollY};v.innerHTML=R[S.tab](d);
  S.flash.forEach(id=>{const e=document.getElementById('o'+id);if(e)e.classList.add('flash')});S.flash=new Set();
  if(AFTER[S.tab])AFTER[S.tab](d);window.scrollTo(keep.x,keep.y)}

/* ---------- the shared action dispatcher: Overview, Alerts, the bell and order cards all use it ---------- */
const BTN={quote:a=>'Send quote'+(a.hint&&a.hint.counter?` Rs ${a.hint.counter}`:''),plan:()=>'Build plan',start:()=>'Approve & start',accept:()=>'Record accepted',approve_payout:()=>'Approve',unblock:()=>'Unblock',
 resolve_dispute:()=>'Resolve',resolve_issue:()=>'Mark sorted',review_sample:()=>'Pass',remind_buyer:()=>'Remind buyer',propose_payout:()=>'Open payout',count:()=>'Count',dispatch:()=>'Dispatch',message:()=>'Message members'};
function needRow(a){S.items[a.id]=a;const b=BTN[a.action];
  return `<div class="item"><div class="t"><b>${esc(a.title)}</b><small>${esc(a.detail||'')} ${a.detail?'· ':''}${ago(a.ts)}${a.cmd?` · WhatsApp: <code>${esc(a.cmd)}</code>`:''}</small></div>
   <span class="chip ${a.priority<=3?'red':a.priority<=5?'amb':''}">${esc(a.label)}</span>${a.escalated?'<span class="chip esc" title="Flagged to the mission officer">Escalated</span>':''}
   <div class="row nowrap">${b?`<button class="btn-s" data-a="do" data-i="${esc(a.id)}">${esc(b(a))}</button>`:''}${a.order_id?`<button class="btn-s ghost" data-a="open" data-id="${a.order_id}" ${a.action==='review_sample'?'data-sub="quality"':''}>Open</button>`:`<button class="btn-s ghost" data-a="go" data-t="${a.tab}">Open</button>`}</div></div>`}
async function post(path,body,msg,method='POST'){try{const r=await api(path,method,body||{});toast(msg||'Done');await refresh();return r}catch(e){toast(e.message,1);return null}}
async function doItem(a){const id=a.order_id,ref=a.id.split(':')[2];
  switch(a.action){
   case 'quote':return post(`/leader/orders/${id}/quote`,{counter:(a.hint||{}).counter},'Quote sent');
   case 'accept':return post(`/leader/orders/${id}/accept`,{},'Recorded');
   case 'plan':return post(`/leader/orders/${id}/plan`,{},'Plan ready for your approval');
   case 'start':return post(`/leader/orders/${id}/start`,{},'Production started. Members told.');
   case 'approve_payout':return post(`/leader/orders/${id}/payout/approve`,{},'Approved');
   case 'unblock':return post(`/leader/orders/${id}/unblock`,{},'Unblocked');
   case 'resolve_dispute':{const n=prompt('How was it resolved? (short note)');if(n===null)return;return post(`/leader/orders/${id}/disputes/${ref}/resolve`,{note:n},'Dispute resolved')}
   case 'resolve_issue':{const n=prompt('What did you do? (the member is told)');if(n===null)return;return post(`/leader/orders/${id}/issues/${ref}/resolve`,{note:n},'Marked sorted')}
   case 'review_sample':return post(`/leader/orders/${id}/quality`,{member:ref,decision:'pass'},'Sample passed');
   case 'remind_buyer':return post(`/leader/orders/${id}/money/remind`,{},'Reminder sent');
   case 'propose_payout':return go('payouts');
   case 'count':return openOrder(id,'delivery');
   case 'dispatch':return openOrder(id,'delivery');
   case 'message':S.act.focusMsg=1;return go('team');
   default:return id?openOrder(id):go(a.tab)}}

/* ---------- search, bell, profile ---------- */
let qT;document.addEventListener('input',e=>{if(e.target.id!=='q')return;clearTimeout(qT);const q=e.target.value.trim(),r=document.getElementById('res');if(!q){r.hidden=true;return}
  qT=setTimeout(async()=>{try{const rs=await api('/leader/search?q='+encodeURIComponent(q));r.innerHTML=rs.length?rs.map(x=>`<button data-a="goto" data-ty="${x.type}" data-id="${esc(x.id)}" data-t="${x.tab}">${esc(x.label)}${x.status?` <span class="chip">${esc(x.status)}</span>`:''}</button>`).join(''):'<div class="muted" style="padding:10px">No match</div>';r.hidden=false}catch(_){}},220)});
document.addEventListener('change',e=>{if(e.target.id==='lang'){LANG=e.target.value;localStorage.setItem('sk_lang',LANG);api('/me/lang','POST',{lang:LANG}).catch(()=>{});document.documentElement.lang=LANG==='hi'?'hi':'en';renderChrome();render()}});
async function openBell(quiet){const d=await api('/leader/view/alerts');S.badges=d.badges;S.items={};S.drawer={mode:'alerts'};const el=document.getElementById('drawer');
  el.innerHTML=`<div class="row"><h2 style="flex:1">Alerts <small>${d.alerts.filter(a=>a.owner==='leader').length} need you</small></h2><button class="ghost" data-a="close">Close</button></div>${d.alerts.filter(a=>a.owner==='leader').slice(0,30).map(needRow).join('')||empty('Nothing needs you.')}<p><button data-a="go" data-t="alerts">Open the Alerts tab</button></p>`;
  el.hidden=false;document.getElementById('scrim').hidden=false;renderChrome()}
async function openProfile(){
  const p=await api('/leader/prefs').catch(()=>S.prefs);S.prefs=p;
  modal(`<h2>${esc(t('profile'))}</h2><p>${esc(S.me.name)} <span class="muted">· ${esc(S.me.phone||'')}</span></p>
   <label class="field">${esc(t('lang'))}<select id="lang2">${[['en','English'],['hi','हिन्दी'],['hing','Hinglish']].map(x=>`<option value="${x[0]}" ${x[0]===LANG?'selected':''}>${x[1]}</option>`).join('')}</select></label>
   <p><label><input type="checkbox" id="wapref" ${p.whatsapp_alerts?'checked':''}> ${esc(t('wa_alerts'))}</label><br><span class="muted">Off: the dashboard still shows everything, WhatsApp stops pinging you.</span></p>
   <div class="row"><button data-a="privacy">${esc(t('privacy'))}</button><button class="red" data-a="logout">${esc(t('logout'))}</button><button class="ghost" data-a="mclose">Close</button></div>`)}
function modal(h){const m=document.getElementById('modal');m.innerHTML=`<div class="card" style="max-width:560px;width:94%;max-height:90vh;overflow:auto">${h}</div>`;m.hidden=false}
const mclose=()=>{document.getElementById('modal').hidden=true};

/* ---------- order drawer: seven sub-tabs on the same order ---------- */
const STEPS=[['submitted','New'],['quoted','Quoted'],['accepted','Accepted'],['planned','Planned'],['in_production','In production'],['payout_pending','Payout'],['settled','Settled'],['ready_for_delivery','Ready'],['dispatched','Dispatched'],['closed','Delivered']];
const SUBS=['overview','work','quality','money','delivery','notes','timeline'];
async function openOrder(id,sub){S.drawer={oid:id,sub:sub||'overview'};document.getElementById('scrim').hidden=false;await loadDrawer()}
async function loadDrawer(quiet){const o=S.drawer&&S.drawer.oid;if(!o)return;try{S.drawer.d=await api(`/leader/orders/${o}/detail`);if(S.drawer.sub==='timeline')S.drawer.tl=await api(`/leader/orders/${o}/timeline`);renderDrawer()}catch(e){if(!quiet)toast(e.message,1)}}
function closeDrawer(){S.drawer=null;document.getElementById('drawer').hidden=true;document.getElementById('scrim').hidden=true}
function renderDrawer(){const D=S.drawer&&S.drawer.d;if(!D)return;const c=D.card,sub=S.drawer.sub,el=document.getElementById('drawer');S.items={};
  const idx=STEPS.findIndex(s=>s[0]===(c.status==='payout_pending'?'payout_pending':c.status==='partially_delivered'?'settled':c.status));
  const body={overview:dOverview,work:dWork,quality:dQuality,money:dMoney,delivery:dDelivery,notes:dNotes,timeline:dTimeline}[sub](D);
  el.innerHTML=`<div class="row"><h2 style="flex:1">Order #${c.id} <small>${esc(c.product)} × ${c.qty} for ${esc(c.buyer.name)}</small></h2><button class="ghost" data-a="close">Close</button></div>
   <div class="row">${hchip(c.health)}<span class="chip">${esc(c.status_label)}</span><span class="muted">${esc(daysTxt(c))} ${c.due_estimated?'(estimated)':''}</span></div>
   <div class="steps">${STEPS.map((s,i)=>`<span class="${c.status==='blocked'||c.status==='cancelled'?'':i<idx?'done':i===idx?'cur':''}">${s[1]}</span>`).join('')}</div>
   <div class="tabs2">${SUBS.map(s=>`<button data-a="sub" data-s="${s}" class="${s===sub?'on':''}">${s[0].toUpperCase()+s.slice(1)}</button>`).join('')}</div>${body}`;el.hidden=false}
const nextBox=c=>c.next?`<div class="${c.next.owner==='leader'?'warnbox':'okbox'}"><b>Next:</b> ${esc(c.next.title)}<br><span class="muted">${esc(c.next.detail||'')}${c.next.cmd?` WhatsApp: <code>${esc(c.next.cmd)}</code>`:''}</span> ${c.next.owner==='leader'&&BTN[c.next.action]?(S.items[c.next.id]=c.next,`<button class="btn-s" data-a="do" data-i="${esc(c.next.id)}">${esc(BTN[c.next.action](c.next))}</button>`):''}</div>`:'<p class="muted">Nothing left to do.</p>';
function dOverview(D){const c=D.card,o=D.order,st=c.status;let act='';
  if(st==='submitted'||st==='quoted')act=`<div class="frm">${fld('dq','Counter price Rs/piece (blank = floor-based)','number','',`placeholder="${esc(c.floor)}"`)}<button data-a="dquote">${st==='quoted'?'Re-quote':'Send quote'}</button></div>`;
  if(st==='accepted')act=`<button data-a="dplan">Build fair plan</button>`;
  if(st==='planned')act=`<button data-a="dstart">Approve and start</button> <button class="ghost" data-a="dplan">Replan</button>`;
  if(['submitted','quoted','accepted','planned'].includes(st))act+=` <button class="red" data-a="dcancel">Cancel order</button>`;
  return `${nextBox(c)}<div class="grid g2"><div><h3>Order</h3><table><tr><td>Product</td><td>${esc(o.product||c.product)}</td></tr><tr><td>Quantity</td><td>${o.qty}</td></tr><tr><td>Offered</td><td>${Rs(o.offered_price)}/piece</td></tr><tr><td>Delivery</td><td>${o.delivery_date?esc(o.delivery_date)+' · ':''}${o.deadline_days} days</td></tr>
   ${['location','size','colour','specs','packaging'].filter(k=>o[k]).map(k=>`<tr><td>${k[0].toUpperCase()+k.slice(1)}</td><td>${esc(o[k])}</td></tr>`).join('')}</table></div>
   <div><h3>Progress</h3>${bar(c.progress.pct)}<p class="muted">${c.progress.finished} of ${c.progress.needed} finished through every stage</p>${c.progress.stages.map(s=>`<div class="muted">${esc(s.stage)} ${s.done}/${s.units}${bar(s.pct)}</div>`).join('')}
   ${c.health_reasons.length&&c.health!=='on_track'?`<div class="warnbox">${c.health_reasons.map(esc).join('<br>')}</div>`:''}</div></div>
   ${D.unclear.length?`<div class="warnbox">Check with the buyer: ${D.unclear.map(esc).join('; ')}</div>`:''}
   ${D.evidence.source&&D.evidence.source!=='leader'||D.evidence.has_photo?`<h3>How the order arrived (${esc(D.evidence.source||'')})</h3>${D.evidence.text?`<p class="muted">“${esc(D.evidence.text)}”</p>`:''}${D.evidence.has_photo?`<img class="photo" alt="Order photo" src="/api/leader/orders/${c.id}/evidence/photo">`:''}`:''}
   <div class="row">${act}</div>`}
function dWork(D){const w=D.work;
  return `${w.rows.length?`<div class="scroll"><table><tr><th>Member</th><th>Stage</th><th>Done</th><th>Last report</th><th>Pay</th><th></th></tr>${w.rows.map(r=>`<tr><td>${esc(r.name)}</td><td>${esc(r.stage)}</td><td>${r.done}/${r.units}${bar(100*r.done/r.units)}</td><td class="muted">${ago(r.last_report)}</td><td>${Rs(r.expected_pay)}${r.accepted?'':' <span class="muted">(not accepted)</span>'}</td>
    <td>${w.reassignable?`<button class="btn-s ghost" data-a="mvform" data-m="${r.member}" data-s="${esc(r.stage)}" data-max="${r.units-(S.drawer.d.card.status==='in_production'?r.done:0)}">Move pieces</button>`:''}</td></tr>`).join('')}</table></div>`:empty('No plan yet.')}
   <div id="mv"></div>${w.declined.length?`<div class="warnbox">Declined (no penalty): ${w.declined.map(esc).join(', ')}</div>`:''}
   ${w.plan_issues.length?`<div class="warnbox">${w.plan_issues.map(esc).join('<br>')}</div>`:''}${(w.options||[]).length?`<div class="warnbox"><b>Ways to make it work</b><ul>${w.options.map(x=>`<li>${esc(x.label)}</li>`).join('')}</ul></div>`:''}
   <h3>Problems reported by members</h3>${w.issues.length?w.issues.map(x=>`<div class="item"><div class="t"><b>${esc(x.name)}</b>: ${esc(x.text)}<small>${ago(x.ts)}${x.open?'':' · sorted'+(x.note?': '+esc(x.note):'')}</small></div>${x.open?`<button class="btn-s" data-a="issue" data-i="${x.i}">Mark sorted</button>`:''}</div>`).join(''):empty('None.')}
   ${['in_production','planned'].includes(D.card.status)?`<p><button data-a="risk" class="ghost">Check deadline</button> ${w.risk?`<span class="${w.risk.shortfall?'bad':''}">${w.risk.shortfall?`Short by ${w.risk.shortfall} pieces. You and the group decide: ${w.risk.options.map(esc).join('; ')}`:'On time'}</span>`:''}</p>`:''}`}
const flags=s=>{if(!s)return '';if(s.status!=='ok')return `<div class="ai"><b>AI pre-check</b> <span class="muted">(suggestion only, you decide)</span><p class="${s.status==='unavailable'?'warn':''}">${esc(s.summary)}</p></div>`;
  const f=s.items.filter(x=>x.result==='possible_defect');return `<div class="ai"><b>AI pre-check</b> <span class="muted">(suggestion only, you decide)</span>${f.length?`<ul>${f.map(x=>`<li><span class="conf ${esc(x.confidence)}">${esc(x.confidence)}</span> <b>${esc(x.criterion)}</b><br><span class="muted">${esc(x.note)}</span></li>`).join('')}</ul>`:'<p>No problems spotted in the photo. The AI can miss things.</p>'}</div>`};
function sampleCard(q,oid,deciding){return `<div class="card"><div class="row"><b>${esc(q.name)}</b> · ${esc(q.stage)} ${chip(q.status)} <span class="muted">${ago(q.ts)}</span></div>${q.note?`<p class="muted">“${esc(q.note)}”</p>`:''}
  ${(q.has_photo||q.photo_url)?`<a href="/api/leader/orders/${oid}/quality/${q.i}/photo" target="_blank"><img class="photo" alt="Sample photo" src="/api/leader/orders/${oid}/quality/${q.i}/photo"></a>`:'<p class="muted">No photo sent.</p>'}${flags(q.screening)}
  ${q.status==='pending'&&deciding?`<div class="row"><button data-a="pass" data-o="${oid}" data-m="${q.member}">Pass</button><button class="red" data-a="rework" data-o="${oid}" data-m="${q.member}">Needs rework</button><button class="ghost" data-a="rescreen" data-o="${oid}" data-i="${q.i}">Re-run AI check</button></div>`:q.status==='rework'?`<p class="muted">${q.rework} piece(s) need rework: ${esc(q.reason||'')}</p>`:''}</div>`}
const clBox=(oid,list)=>`<details class="cl"><summary>Buyer's quality checklist (${list.length})</summary><p class="muted">One point per line. The AI checks sample photos against it.</p><textarea id="cl${oid}">${list.map(c=>esc(c.text)).join('\n')}</textarea><button data-a="savecl" data-o="${oid}">Save checklist</button></details>`;
function dQuality(D){return (D.quality.samples.length?D.quality.samples.slice().reverse().map(q=>sampleCard(q,D.card.id,D.card.status==='in_production')).join(''):empty('No samples yet. Members send a photo on WhatsApp.'))+clBox(D.card.id,D.quality.checklist)}
function payLines(p){return p?`<table><tr><th>Member</th><th>Stage</th><th>Accepted</th><th>Rework</th><th>Rate</th><th class="right">Amount</th></tr>${p.lines.map(l=>`<tr><td>${esc(l.name)}</td><td>${esc(l.stage)}</td><td>${l.accepted}</td><td>${l.pending||0}</td><td>Rs ${esc(l.rate)}</td><td class="right">${Rs(l.paid)}${+l.pending_amount?` <span class="muted">+${Rs(l.pending_amount)} after rework</span>`:''}</td></tr>`).join('')}<tr><td colspan="5"><b>Total</b></td><td class="right"><b>${Rs(p.total)}</b></td></tr></table>`:'<p class="muted">No payout proposed yet.</p>'}
function buyerMoney(m,oid,full){return `<div class="grid g3"><div class="kpi"><b>${Rs(m.total)}</b><span>${m.estimated?'Estimated total (no quote yet)':'Quote total'}</span></div><div class="kpi"><b>${Rs(m.paid)}</b><span>Received${+m.advance?` (advance ${Rs(m.advance)})`:''}</span></div><div class="kpi ${m.overdue?'hot':''}"><b>${Rs(m.balance)}</b><span>Balance due${m.due_ts?' by '+dshort(m.due_ts):''}${m.overdue?` · ${m.days_overdue} d overdue`:''}</span></div></div>
  ${(m.payments||[]).length?`<table>${m.payments.map(p=>`<tr><td>${dshort(p.ts)}</td><td>${esc(p.kind)}</td><td>${Rs(p.amount)}</td><td class="muted">${esc(p.method||'')} ${esc(p.note||'')}</td></tr>`).join('')}</table>`:''}
  ${full&&+m.balance>0?`<div class="frm">${fld('pa'+oid,'Amount received (Rs)','number','','min="1"')}${sel('pk'+oid,'Type',[['','Auto'],['advance','Advance'],['payment','Payment']])}${fld('pm'+oid,'How (cash, UPI...)')}<button data-a="pay" data-o="${oid}">Record payment</button><button class="ghost" data-a="remindb" data-o="${oid}">Remind buyer</button></div>
   <div class="frm">${fld('pd'+oid,'Due date','date',m.due_ts?new Date(m.due_ts*1000).toISOString().slice(0,10):'')}<button class="ghost" data-a="due" data-o="${oid}">Set due date</button></div>`:''}`}
function dMoney(D){const m=D.money,r=m.margin;return `<div class="grid g2"><div><h3>Price check</h3><table><tr><td>Buyer offered</td><td>${Rs(m.offered)}</td></tr><tr><td>Floor price</td><td>${Rs(m.floor)}</td></tr><tr><td>Quote</td><td>${m.quote?Rs(m.quote):'-'}</td></tr>
   <tr><td>Women's effective pay at the offer</td><td class="${m.loss_making?'bad':''}">${Rs(m.effective_hourly_at_offer)}/hr vs target ${Rs(m.target_hourly)}/hr (${m.pay_gap_pct}% gap)</td></tr><tr><td>Rate card</td><td>${esc(m.rate_card.product)} v${m.rate_card.version} ${m.rate_card.source==='group'?'(group approved)':'(template default)'}</td></tr></table></div>
   <div><h3>Margin <small>estimate</small></h3><table><tr><td>Buyer revenue</td><td>${Rs(r.revenue)}</td></tr><tr><td>Member pay</td><td>${Rs(r.member_pay)}</td></tr><tr><td>Materials (${esc(r.materials_basis)})</td><td>${Rs(r.materials)}</td></tr><tr><td><b>Margin</b></td><td><b>${Rs(r.margin)}</b>${r.margin_pct!=null?` (${r.margin_pct}%)`:''}</td></tr></table></div></div>
   <h3>Member payout</h3>${payLines(m.payout)}${m.payout?`<p class="muted">Leader ${m.leader_approved?'approved':'not yet approved'} · members ${m.have} of ${m.needed}</p>`:''}<h3>Money from the buyer</h3>${buyerMoney(m.buyer,D.card.id,true)}`}
function dDelivery(D){const c=D.card,d=D.delivery,st=c.status;let a='';
  if(['settled','partially_delivered'].includes(st))a=`<p>Delivered ${d.delivered}/${d.ordered}. Count the packed pieces (still owed ${d.remaining}).</p><div class="frm">${fld('rc'+c.id,'Pieces packed','number',d.remaining,`min="1" max="${d.remaining}"`)}${fld('rn'+c.id,'Note')}<button data-a="ready" data-o="${c.id}">Mark ready for delivery</button></div>`;
  if(st==='ready_for_delivery')a=`<p>${d.qc.counted} of ${d.qc.owed} owed pieces counted ${d.qc.match?'':'<b class="warn">(partial delivery, said openly to the buyer)</b>'}</p><div class="frm">${fld('dc'+c.id,'Courier / bus / vehicle')}${fld('dt'+c.id,'Tracking / vehicle no.')}${fld('dd'+c.id,'Dispatch date','date')}<button data-a="dispatch" data-o="${c.id}">Dispatch</button></div>`;
  if(st==='dispatched'){const l=d.shipments[d.shipments.length-1];a=`<p>Shipment ${l.i+1}: ${l.qty} pcs via ${esc(l.courier)} ${l.tracking?'('+esc(l.tracking)+')':''}. Waiting for the buyer to sign off.</p>${l.has_photo?`<a href="/api/leader/orders/${c.id}/delivery/${l.i}/photo" target="_blank">Delivery photo</a>`:`<button data-a="dphoto" data-o="${c.id}">Add delivery photo</button>`}`}
  if(st==='blocked')a=`<div class="warnbox">Blocked: ${esc((d.blocked||{}).reason)}</div><button data-a="unblock" data-o="${c.id}">Unblock</button>`;
  if(['settled','ready_for_delivery','dispatched','partially_delivered'].includes(st))a+=` <button class="red" data-a="block" data-o="${c.id}">Block with a reason</button>`;
  return `${nextBox(c)}${a}<h3>Shipments</h3>${d.shipments.length?`<table><tr><th>#</th><th>Sent</th><th>Courier</th><th>Buyer received</th></tr>${d.shipments.map(x=>`<tr><td>${x.i+1}</td><td>${x.qty}</td><td>${esc(x.courier)} ${esc(x.tracking||'')}</td><td>${x.received==null?'<span class="muted">waiting</span>':`${x.received} of ${x.qty}${x.signoff&&x.signoff.short?` <span class="warn">(short ${x.signoff.short})</span>`:''}`}</td></tr>`).join('')}</table>`:empty('Nothing dispatched yet.')}
   ${D.rating?`<p>Buyer rated the group <b>${D.rating.stars}/5</b> ${esc(D.rating.comment||'')}</p>`:''}`}
function dNotes(D){return `<p class="muted">Private to you. Not shown to members or buyers, and not copied into the audit log.</p>${D.notes.map(n=>`<div class="item"><div class="t">${esc(n.text)}<small>${dtime(n.ts)}</small></div></div>`).join('')||empty('No notes yet.')}<label class="field">New note<textarea id="nt"></textarea></label><button data-a="note">Save note</button>`}
function dTimeline(D){const tl=S.drawer.tl;if(!tl)return empty('Loading...');return `<div class="row"><button data-a="verify">Verify audit ledger</button><span class="muted">${tl.ledger.valid?'Chain intact ('+tl.ledger.events+' events)':'TAMPERING at event '+tl.ledger.broken_at}</span></div>${tl.rows.slice().reverse().map(r=>`<div class="item"><div class="t">${esc(r.text)}<small>${dtime(r.ts)} · ${esc(r.who)} · #${r.i}</small></div></div>`).join('')}`}

/* ---------- click handling ---------- */
const H={
 go:e=>go(e.dataset.t),more:()=>{const s=document.getElementById('sheet');if(!s.hidden){s.hidden=true;return}s.innerHTML=TABS.filter(k=>!MOBILE.includes(k)).map(k=>`<button data-a="go" data-t="${k}">${esc(t(k))}${bd(S.badges[k])}</button>`).join('');s.hidden=false},
 open:e=>openOrder(+e.dataset.id,e.dataset.sub),close:closeDrawer,bell:()=>openBell(),profile:openProfile,mclose,
 sub:e=>{S.drawer.sub=e.dataset.s;if(e.dataset.s==='timeline')loadDrawer();else renderDrawer()},
 do:e=>{const a=S.items[e.dataset.i];if(a)doItem(a)},
 goto:e=>{document.getElementById('res').hidden=true;document.getElementById('q').value='';if(e.dataset.ty==='order')openOrder(+e.dataset.id);else{if(e.dataset.ty==='buyer')S.buyer=e.dataset.id;if(e.dataset.ty==='member')S.act.member=+e.dataset.id;go(e.dataset.t)}},
 logout:()=>logout(),privacy:async()=>{const p=await api('/privacy');modal(`<h2>${esc(t('privacy'))}</h2><p>${esc(p.text)}</p><button data-a="mclose">Close</button>`)},
 dquote:()=>post(`/leader/orders/${S.drawer.oid}/quote`,{counter:val('dq')},'Quote sent'),dplan:()=>post(`/leader/orders/${S.drawer.oid}/plan`,{},'Plan ready'),dstart:()=>post(`/leader/orders/${S.drawer.oid}/start`,{},'Production started'),
 dcancel:()=>{if(confirm('Cancel this order? Only possible before work starts.'))post(`/leader/orders/${S.drawer.oid}/cancel`,{reason:prompt('Reason (optional)')||''},'Cancelled')},
 mvform:e=>{const d=S.drawer.d,ms=d.work.members.filter(m=>m.id!==+e.dataset.m&&(m.skills||[]).includes(e.dataset.s));document.getElementById('mv').innerHTML=`<div class="card"><b>Move ${esc(e.dataset.s)} pieces</b><div class="frm">${sel('mvt','To',ms.map(m=>[m.id,m.name]))}${fld('mvn','Pieces','number',1,`min="1" max="${e.dataset.max}"`)}<button data-a="mv" data-m="${e.dataset.m}" data-s="${esc(e.dataset.s)}">Move</button></div>${ms.length?'':'<p class="warn">Nobody else has this skill.</p>'}</div>`},
 mv:e=>post(`/leader/orders/${S.drawer.oid}/reassign`,{from:+e.dataset.m,to:num('mvt'),stage:e.dataset.s,pieces:num('mvn')},'Moved. Both members told.'),
 issue:e=>{const n=prompt('What did you do? (the member is told)');if(n!==null)post(`/leader/orders/${S.drawer.oid}/issues/${e.dataset.i}/resolve`,{note:n},'Marked sorted')},
 risk:()=>{const d=prompt('Days left to the delivery date? (blank = use the real date)');if(d!==null)post(`/leader/orders/${S.drawer.oid}/risk`,{days_left:d},'Checked')},
 pass:e=>post(`/leader/orders/${e.dataset.o}/quality`,{member:e.dataset.m,decision:'pass'},'Sample passed'),
 rework:e=>{const k=prompt('How many pieces need rework?');if(k)post(`/leader/orders/${e.dataset.o}/quality`,{member:e.dataset.m,decision:'rework',rework:+k,reason:prompt('Reason')||''},'Sent back for rework')},
 rescreen:e=>post(`/leader/orders/${e.dataset.o}/quality/${e.dataset.i}/rescreen`,{},'AI check re-run'),
 savecl:e=>post(`/leader/orders/${e.dataset.o}/checklist`,{items:val('cl'+e.dataset.o).split('\n')},'Checklist saved','PUT'),
 pay:e=>post(`/leader/orders/${e.dataset.o}/money`,{amount:num('pa'+e.dataset.o),kind:val('pk'+e.dataset.o),method:val('pm'+e.dataset.o)},'Payment recorded'),
 remindb:e=>post(`/leader/orders/${e.dataset.o}/money/remind`,{},'Reminder sent'),due:e=>post(`/leader/orders/${e.dataset.o}/money/due`,{due_date:val('pd'+e.dataset.o)},'Due date set','PUT'),
 ready:e=>post(`/leader/orders/${e.dataset.o}/ready`,{counted:num('rc'+e.dataset.o),note:val('rn'+e.dataset.o)},'Count recorded'),
 dispatch:e=>post(`/leader/orders/${e.dataset.o}/dispatch`,{courier:val('dc'+e.dataset.o),tracking:val('dt'+e.dataset.o),date:val('dd'+e.dataset.o)},'Dispatched. Buyer told.'),
 unblock:e=>post(`/leader/orders/${e.dataset.o}/unblock`,{},'Unblocked'),block:e=>{const r=prompt('Why is the delivery blocked?');if(r)post(`/leader/orders/${e.dataset.o}/block`,{reason:r},'Blocked. Buyer told.')},
 dphoto:e=>pickPhoto(p=>post(`/leader/orders/${e.dataset.o}/delivery-photo`,{photo:p},'Photo added')),
 note:()=>post(`/leader/orders/${S.drawer.oid}/notes`,{text:val('nt')},'Note saved'),
 verify:async()=>{const v=await api('/ledger/verify');toast(v.valid?'Ledger intact ('+v.events+' events)':'TAMPERING at event '+v.broken_at,!v.valid)}};
function pickPhoto(cb){const f=document.createElement('input');f.type='file';f.accept='image/*';f.onchange=()=>{const file=f.files[0];if(!file)return;const img=new Image();img.onload=()=>{const k=Math.min(1,640/Math.max(img.width,img.height)),c=document.createElement('canvas');c.width=Math.round(img.width*k);c.height=Math.round(img.height*k);c.getContext('2d').drawImage(img,0,0,c.width,c.height);cb(c.toDataURL('image/jpeg',.7))};img.onerror=()=>toast('That file is not a photo',1);img.src=URL.createObjectURL(file)};f.click()}
document.addEventListener('click',e=>{const el=e.target.closest('[data-a]');if(el&&H[el.dataset.a]){e.preventDefault();H[el.dataset.a](el)}else if(!e.target.closest('.search'))document.getElementById('res')&&(document.getElementById('res').hidden=true)});
document.addEventListener('click',e=>{if(e.target.id==='scrim')closeDrawer()});
document.addEventListener('change',e=>{if(e.target.id==='lang2'){document.getElementById('lang').value=e.target.value;document.getElementById('lang').dispatchEvent(new Event('change',{bubbles:true}))}
  if(e.target.id==='wapref')api('/leader/prefs','PUT',{whatsapp_alerts:e.target.checked}).then(p=>{S.prefs=p;toast('Saved')}).catch(x=>toast(x.message,1))});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeDrawer();mclose()}});

async function start(){
  try{const u=await api('/me');if(u.role!=='leader'){location='/app/login.html';return}S.me=u;if(u.lang&&!localStorage.getItem('sk_lang'))LANG=u.lang}catch(_){return}
  if(!S.me.consented)modal(`<h2>Your data, your consent</h2><p>Sakhi Khata keeps your orders and earnings records so your group can price, plan and pay fairly. Members see only their own pay. Officers see only totals.</p><button id="agree">I agree</button> <button class="red" data-a="logout">No, log out</button>`),document.getElementById('agree').onclick=()=>api('/me/consent','POST').then(mclose);
  renderChrome();await loadTab(S.tab);if(S.tab!=='team')loadTab('team',true);          // the team list feeds search and the activity filter
  
  setInterval(poll,4000);setInterval(renderLive,1000);setInterval(()=>{if(!document.hidden&&!busy())loadTab(S.tab,true)},60000)}   // the 60s reload keeps "days left" and ages honest even when nothing happened
