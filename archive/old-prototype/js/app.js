document.addEventListener('click',e=>{const b=e.target.closest('[data-a]');if(!b)return;const v=b.dataset.v;act[b.dataset.a](v===undefined||isNaN(v)?v:+v,b.dataset.w=='c'?true:(b.dataset.w&&!isNaN(b.dataset.w)?+b.dataset.w:b.dataset.w))});

function draw(){
  const r=document.getElementById('root');
  if(!S.u){
    r.innerHTML=loginView();
    document.getElementById('lf').onsubmit=async e=>{
      e.preventDefault();
      const f=new FormData(e.target),u=USERS.find(x=>x.e==f.get('e').trim().toLowerCase()&&x.p==f.get('p')&&x.r==S.lr);
      if(!u){S.err='Email, password or role does not match.';return draw()}
      sync('login',{email:u.e});S.u=u;S.tab=TABS[u.r][0][0];draw();
    };
    return;
  }
  const u=S.u,ini=u.n.split(' ').map(w=>w[0]).slice(0,2).join('');
  r.innerHTML=`<header class="top"><div class="brand">Sakhi Khata<small>${ROLE[u.r]}</small></div><nav class="tabs" aria-label="Sections">${TABS[u.r].map(t=>`<button data-a="tab" data-v="${t[0]}" ${S.tab==t[0]?'aria-current="page"':''}>${t[1]}</button>`).join('')}</nav><div class="me"><span class="av">${ini}</span><span>${u.n}</span><button class="btn sm" data-a="logout">Sign out</button></div></header><main>${V[u.r][S.tab]()}</main>`;

  const f=document.getElementById('of');
  if(f){
    const est=()=>{const p=P[f.p.value];document.getElementById('est').textContent=inr((+f.q.value||0)*p.rate)};
    f.p.oninput=f.q.oninput=est;est();
    f.onsubmit=e=>{
      e.preventDefault();
      const d=new FormData(f),days=Math.max(1,Math.ceil((new Date(d.get('d'))-Date.now())/864e5)),o={id:S.nid++,b:BUYER,p:d.get('p'),q:+d.get('q'),d:days,ch:S.ch,s:'new'};
      O.push(o);
      W.unshift({o:0,t:`New order #${o.id}: ${o.q} ${pr(o).n}s from ${BUYER}, needed in ${days} days.${d.get('n')?' Note: '+d.get('n'):''}`});
      B.unshift({t:`We received order #${o.id}. The group will confirm shortly.`});
      sync('placeOrder',o);
      if(S.ch=='wa')window.open(`https://wa.me/${GROUP_WA}?text=${encodeURIComponent(`Hello, I’d like to order ${o.q} ${pr(o).n}s by ${dstr(days)}. – ${BUYER}`)}`,'_blank');
      toast('Order sent. The group leader will confirm shortly.');S.tab='my';draw();
    };
  }
  const a=document.getElementById('af');
  if(a)a.onsubmit=e=>{e.preventDefault();A.unshift({t:a.t.value,d:'Today'});sync('notice',{text:a.t.value});toast('Notice sent to all members.');draw()};
}

draw();