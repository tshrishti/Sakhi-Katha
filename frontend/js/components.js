/* ===== Components ===== */
const pill=(t,c='')=>`<span class="pill ${c}">${t}</span>`;
const bar=(p,c='')=>`<div class="bar"><i class="${c}" style="width:${Math.min(100,Math.round(p*100))}%"></i></div>`;
const stat=(l,v,h='')=>`<div class="card stat ${h}"><span class="sub">${l}</span><div class="num">${v}</div></div>`;

function checks(o){
  const f=feas(o),row=(n,t,ok,r)=>`<div><span>${n}</span>${bar(r,ok?'':r>1.5?'b':'w')}${pill(t,ok?'ok':'bad')}</div>`;
  return`<div class="chk">${row('Workers',`need ${f.wNeed}, have ${f.av}`,f.ok.w,f.wNeed/f.av)}${row('Fabric',`need ${f.fabNeed} m, free ${f.fabFree} m`,f.ok.f,f.fabNeed/Math.max(1,f.fabFree))}${row('Deadline',`needs ${f.daysNeed} days, asked ${o.d}`,f.ok.d,f.daysNeed/o.d)}</div>`;
}

function pendingCard(o){
  const f=feas(o);
  return`<article class="stitch"><div class="row"><div><h3>#${o.id} · ${o.q} ${pr(o).n}s</h3><span class="sub">${o.b} · due ${dstr(o.d)} · via ${o.ch=='wa'?'WhatsApp':'website'}</span></div>${f.all?pill('Can deliver','ok'):pill('Not feasible as asked','bad')}</div>${checks(o)}
 <div class="row"><span class="sub">${f.all?'Everything checks out.':`Earliest safe date is ${dstr(f.daysNeed)}.`}</span><div class="chips"><button class="btn r sm" data-a="decline" data-v="${o.id}">Decline</button>${f.all?'':`<button class="btn sm" data-a="accept" data-v="${o.id}" data-w="c">Accept for ${dstr(f.daysNeed)}</button>`}<button class="btn ${f.all?'g':''} sm" data-a="accept" data-v="${o.id}">${f.all?'Accept order':'Accept anyway'}</button></div></div></article>`;
}

const declCard=t=>`<article class="card"><div class="row"><h3>${mem(t.m).name} can’t do ${t.st}</h3>${pill(t.q+' units','warn')}</div><p class="sub" style="margin:6px 0 12px">“${t.why}” · Order #${t.o}</p><div class="chips"><button class="btn sm" data-a="excuse" data-v="${t.id}">Accept and share the work</button><button class="btn sm" data-a="reassign" data-v="${t.id}">Give to someone else</button></div></article>`;

const wa=()=>`<div class="card"><h2>WhatsApp messages</h2>${W.map(w=>`<div class="msg ${w.o?'out':''}"><small>${w.o?'Sent':'Received'}</small>${w.t}</div>`).join('')}</div>`;

const SL={new:['Waiting for you',''],acc:['In production','warn'],rdy:['Ready to deliver','ok'],done:['Delivered','ok'],no:['Closed','bad']};

/* ===== Leader ===== */
const V={seller:{
  ov(){
    const ac=O.filter(o=>o.s=='acc'),pend=O.filter(o=>o.s=='new'),dec=T.filter(t=>t.s=='no'),av=M.filter(m=>m.on).length,mx=Math.max(...M.map(m=>m.earn));
    const profit=S.base+ac.reduce((a,o)=>a+prog(o)*o.q*(pr(o).rate-24*pr(o).f),0);
    return`<div><h1>Good morning, Kavita</h1><p class="sub">Group view · ${M.length} members</p></div>
  <section class="grid">${stat('Group profit this month',inr(profit),'hero')}${stat('Orders in progress',ac.length)}${stat('Members available',av+' of '+M.length)}${stat('Waiting for you',pend.length+dec.length)}</section>
  <section><h2>Needs your decision</h2><div class="grid wide">${pend.map(pendingCard).join('')}${dec.map(declCard).join('')}${pend.length+dec.length?'':'<p class="sub">Nothing waiting. New orders and member requests will show up here.</p>'}</div></section>
  <section class="grid wide"><div class="card"><h2>Earnings by member</h2>${[...M].sort((a,b)=>b.earn-a.earn).map(m=>`<div class="row" style="margin-bottom:8px"><span style="width:120px">${m.name.split(' ')[0]}</span><div style="flex:1">${bar(m.earn/mx)}</div><span style="width:70px;text-align:right">${inr(m.earn)}</span></div>`).join('')}</div>${wa()}</section>
  <section class="card"><h2>Send a notice to the group</h2><form id="af"><textarea name="t" rows="2" placeholder="For example: Meeting at 5 pm on Saturday" required></textarea><button class="btn p" style="justify-self:start">Send to all members</button></form></section>`;
  },
  or(){
    const g=s=>O.filter(o=>status(o)==s);
    const line=o=>{const p=prog(o),s=status(o);return`<div class="row" style="padding:12px 0;border-bottom:1px solid var(--ln)"><div style="min-width:200px"><b>#${o.id} · ${o.q} ${pr(o).n}s</b><div class="sub">${o.b} · due ${dstr(o.d)}</div></div><div style="flex:1;min-width:120px">${bar(p)}</div>${pill(SL[s][0],SL[s][1])}<span class="sub">Profit ${inr(o.q*(pr(o).rate-24*pr(o).f))}</span>${s=='rdy'?`<button class="btn g sm" data-a="deliver" data-v="${o.id}">Mark delivered</button>`:''}</div>`};
    return`<h1>Orders</h1><section><h2>Waiting for a decision</h2><div class="grid wide">${g('new').map(pendingCard).join('')||'<p class="sub">No new orders.</p>'}</div></section>
  <section class="card"><h2>In production and ready</h2>${[...g('acc'),...g('rdy')].map(line).join('')||'<p class="sub">Nothing in production.</p>'}</section>
  <section class="card"><h2>Delivered and closed</h2>${[...g('done'),...g('no')].map(line).join('')||'<p class="sub">No history yet.</p>'}</section>`;
  },
  te(){
    return`<h1>Your team</h1><p class="sub">Skills, workload and availability. Tasks are divided by skill among members who are available.</p><section class="card scroll"><table><tr><th>Name</th><th>Skills</th><th>Open units</th><th>Earned</th><th>Total units</th><th></th></tr>${M.map(m=>{const open=T.filter(t=>t.m==m.id&&t.s=='ok').reduce((a,t)=>a+t.q-t.done,0);return`<tr><td>${m.name}</td><td>${m.skills.map(s=>pill(s)).join(' ')}</td><td style="min-width:110px">${bar(open/120,open>100?'b':'')}<span class="sub">${open}</span></td><td>${inr(m.earn)}</td><td>${m.done}</td><td><button class="btn sm" data-a="avail" data-v="${m.id}">${m.on?'Available':'On leave'}</button></td></tr>`}).join('')}</table></section>
  <section class="card scroll"><h2>Who is doing what</h2><table><tr><th>Member</th><th>Task</th><th>Order</th><th>Progress</th></tr>${T.filter(t=>t.s!='void'&&ord(t.o).s=='acc').map(t=>`<tr><td>${mem(t.m).name}</td><td>${t.st} · ${t.q}</td><td>#${t.o}</td><td style="min-width:120px">${t.s=='no'?pill('Declined','bad'):t.s=='new'?pill('Not yet accepted','warn'):bar(t.done/t.q)}</td></tr>`).join('')}</table></section>`;
  },
  iv(){
    const used=O.filter(x=>x.s=='acc').reduce((a,x)=>a+x.q*(1-prog(x))*pr(x).fab,0),free=S.stock-used;
    return`<h1>Inventory</h1><section class="grid">${stat('Fabric in stock',Math.round(S.stock)+' m')}${stat('Set aside for orders',Math.round(used)+' m')}${stat('Free to use',Math.round(free)+' m','hero')}</section>
  <section class="card"><div class="row"><h2>Fabric</h2>${free<150?pill('Running low','bad'):pill('Healthy','ok')}</div>${bar(free/S.stock,free<150?'b':'')}<p class="sub" style="margin:10px 0 14px">This is what the feasibility check uses before you accept an order.</p><button class="btn p" data-a="restock">Add 100 m of fabric</button></section>`;
  },
  po(){
    const tot=M.filter(m=>!m.pay).reduce((a,m)=>a+m.earn,0);
    return`<h1>Payouts</h1><section class="grid">${stat('Waiting for approval',inr(tot),'hero')}${stat('Approved',M.filter(m=>m.pay).length+' of '+M.length)}</section>
  <section class="card scroll"><div class="row"><h2>This month</h2><button class="btn g sm" data-a="payall">Approve all</button></div><table><tr><th>Member</th><th>Units</th><th>Amount</th><th>Status</th><th></th></tr>${M.map(m=>`<tr><td>${m.name}</td><td>${m.done}</td><td>${inr(m.earn)}</td><td>${m.pay?pill('Approved','ok'):pill('Waiting','warn')}</td><td>${m.pay?'':`<button class="btn sm" data-a="pay" data-v="${m.id}">Approve</button>`}</td></tr>`).join('')}</table></section>`;
  }
},

/* ===== Member ===== */
member:{
  ho(){
    const m=me(),mine=T.filter(t=>t.m==m.id&&t.s!='void'&&ord(t.o).s=='acc'),nw=mine.filter(t=>t.s=='new'),cur=mine.filter(t=>t.s=='ok'),tot=cur.reduce((a,t)=>a+t.q,0),dn=cur.reduce((a,t)=>a+t.done,0),pc=tot?dn/tot:0,gp=Math.min(1,m.earn/GOAL),R=34,C=2*Math.PI*R,why=['Not well','Family work','Hand or eye strain','Away this week'];
    const ring=(p,l,v)=>`<div class="card row"><div><span class="sub">${l}</span><div class="num" style="font-size:24px">${v}</div></div><div class="ring"><svg viewBox="0 0 84 84"><circle cx="42" cy="42" r="${R}" fill="none" stroke="#ffffff18" stroke-width="9"/><circle cx="42" cy="42" r="${R}" fill="none" stroke="var(--sg)" stroke-width="9" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C*(1-p)}" transform="rotate(-90 42 42)"/></svg><b>${Math.round(p*100)}%</b></div></div>`;
    return`<div><h1>Namaste, ${m.name.split(' ')[0]}</h1><p class="sub">Only your own work and earnings are shown here.</p></div>
  <section class="grid">${stat('You earned this month',inr(m.earn),'hero')}${stat('Units completed',m.done+dn)}${ring(pc,'Current work',dn+' of '+tot)}${ring(gp,'Monthly goal',inr(GOAL))}</section>
  <section><h2>New work for you</h2><div class="grid wide">${nw.map(t=>`<article class="stitch"><h3>${t.st} · ${t.q} units</h3><p class="sub">Order #${t.o} · ${inr(t.q*SR[t.st]*pr(ord(t.o)).f)} for this task</p><div class="chips" style="margin-top:10px"><button class="btn g sm" data-a="tacc" data-v="${t.id}">Accept</button>${why.map(r=>`<button class="btn r sm" data-a="tno" data-v="${t.id}" data-w="${r}">Can’t: ${r}</button>`).join('')}</div></article>`).join('')||'<p class="sub">No new work right now.</p>'}</div></section>`;
  },
  ta(){
    const m=me(),mine=T.filter(t=>t.m==m.id&&t.s=='ok'&&ord(t.o).s!='no');
    return`<h1>My tasks</h1><section class="card">${mine.map(t=>`<div class="row" style="padding:12px 0;border-bottom:1px solid var(--ln)"><div style="min-width:170px"><b>${t.st}</b><div class="sub">Order #${t.o} · ${t.done} of ${t.q} · due ${dstr(ord(t.o).d)}</div></div><div style="flex:1;min-width:120px">${bar(t.done/t.q)}</div><div class="chips"><button class="btn sm" data-a="log" data-v="${t.id}" ${t.done>=t.q?'disabled':''}>Add 10</button><button class="btn g sm" data-a="fin" data-v="${t.id}" ${t.done>=t.q?'disabled':''}>${t.done>=t.q?'Done':'Mark all done'}</button></div></div>`).join('')||'<p class="sub">Accepted tasks will appear here.</p>'}</section>`;
  },
  ea(){
    const m=me(),L=T.filter(t=>t.m==m.id&&t.done>0);
    return`<h1>My earnings</h1><section class="grid">${stat('This month',inr(m.earn),'hero')}<div class="card stat"><span class="sub">Payout status</span><div class="num">${m.pay?'Approved':'Waiting'}</div></div></section>
  <section class="card scroll"><h2>How your pay adds up on current orders</h2><table><tr><th>Task</th><th>Units</th><th>Rate</th><th>Amount</th><th></th></tr>${L.map(t=>{const r=SR[t.st]*pr(ord(t.o)).f;return`<tr><td>${t.st} · order #${t.o}</td><td>${t.done}</td><td>${inr(r)}</td><td>${inr(t.done*r)}</td><td><button class="btn sm" data-a="ask" data-v="${t.st} on order #${t.o}">Why this amount?</button></td></tr>`}).join('')||'<tr><td class="sub">Log some units and they will show here.</td></tr>'}</table></section>`;
  },
  no(){
    return`<h1>Notices</h1><section class="card">${A.map(a=>`<div class="msg"><small>From Kavita Sharma · ${a.d}</small>${a.t}</div>`).join('')}</section><section class="card"><h2>Need time off?</h2><p class="sub" style="margin-bottom:12px">Your leader will see this and plan the work around it.</p><button class="btn p" data-a="leave">Ask for leave this week</button></section>`;
  }
},

/* ===== Buyer ===== */
buyer:{
  ne(){
    const mine=O.filter(o=>o.b==BUYER);
    return`<div><h1>Order from our group</h1><p class="sub">Tell us what you need and when. The group confirms whether it can deliver.</p></div>
  <section class="grid wide"><form class="card" id="of"><label>Product<select name="p" id="pp"><option value="bag">Cloth bag · ₹38 each</option><option value="shoe">Jute shoes · ₹420 a pair</option></select></label>
  <label>Quantity<input name="q" id="pq" type="number" min="1" value="200" required></label>
  <label>Needed by<input name="d" type="date" required value="${new Date(Date.now()+8*864e5).toISOString().slice(0,10)}"></label>
  <label>Notes for the group (optional)<input name="n" placeholder="Colour, print, packing"></label>
  <div><span style="font-weight:500">Send order through</span><div class="seg" style="margin-top:4px"><button type="button" data-a="ch" data-v="web" aria-pressed="${S.ch=='web'}">Website</button><button type="button" data-a="ch" data-v="wa" aria-pressed="${S.ch=='wa'}">WhatsApp</button></div></div>
  <div class="row"><span class="sub">Estimated total</span><b class="num" id="est"></b></div>
  <button class="btn p" type="submit">${S.ch=='wa'?'Open WhatsApp to send':'Place order'}</button></form>
  <div class="grid" style="align-content:start">${stat('Orders placed',mine.length)}${stat('Spent so far',inr(mine.filter(o=>o.s=='done').reduce((a,o)=>a+o.q*pr(o).rate,0)))}</div></section>`;
  },
  my(){
    const mine=O.filter(o=>o.b==BUYER).sort((a,b)=>b.id-a.id),st=o=>({new:0,acc:prog(o)>0?2:1,rdy:3,done:4,no:-1})[status(o)];
    return`<h1>My orders</h1><section class="grid wide">${mine.map(o=>{const s=st(o),k=status(o);return`<article class="card"><div class="row"><b>#${o.id} · ${o.q} ${pr(o).n}s</b>${pill(SL[k][0],SL[k][1])}</div><span class="sub">${inr(o.q*pr(o).rate)} · due ${dstr(o.d)}</span>${s<0?'':`<div class="steps">${[0,1,2,3,4].map(i=>`<i class="${i<=s?'on':''}"></i>`).join('')}</div>`}
  <div class="chips" style="margin-top:12px">${k=='new'?`<button class="btn r sm" data-a="cancel" data-v="${o.id}">Cancel order</button>`:''}${k=='done'||k=='no'?`<button class="btn sm" data-a="reorder" data-v="${o.id}">Order again</button>`:''}${k=='done'?[1,2,3,4,5].map(n=>`<button class="star ${(o.rate||0)>=n?'on':''}" data-a="rate" data-v="${o.id}" data-w="${n}" aria-label="${n} stars">★</button>`).join(''):''}</div></article>`}).join('')}</section>`;
  },
  up(){
    return`<h1>Updates</h1><section class="card">${B.map(b=>`<div class="msg out"><small>Sakhi Khata</small>${b.t}</div>`).join('')||'<p class="sub">No updates yet.</p>'}</section>`;
  }
}};

/* ===== Shell ===== */
function loginView(){
  const d=DEMO[S.lr],info={seller:['Group leader','Approve orders after a feasibility check, divide work by skill, approve payouts.'],member:['Member','See your own tasks, earnings and notices. Accept or decline work with a reason.'],buyer:['Buyer','Place orders on the website or WhatsApp and follow each one to delivery.']};
  return`<div class="login"><section><div class="brand" style="font-size:26px">Sakhi Khata</div><h1>Every order, every hand, every rupee explained.</h1><p class="sub">Sign in to your own dashboard. What you see depends on who you are.</p><div class="rc">${Object.keys(info).map(k=>`<div><b>${info[k][0]}</b><div class="sub">${info[k][1]}</div></div>`).join('')}</div></section>
 <form class="card" id="lf"><h2>Sign in</h2><div class="seg">${Object.keys(ROLE).map(k=>`<button type="button" data-a="role" data-v="${k}" aria-pressed="${S.lr==k}">${ROLE[k]}</button>`).join('')}</div>
 <label>Email<input name="e" type="email" required autocomplete="username" value="${d.e}"></label><label>Password<input name="p" type="password" required autocomplete="current-password" value="${d.p}"></label>
 <div class="err" role="alert">${S.err}</div><button class="btn p">Sign in as ${ROLE[S.lr].toLowerCase()}</button><p class="sub">Demo accounts are filled in for you. Other members sign in with their first name, such as radha@sakhi.in and member123.</p></form></div>`;
}