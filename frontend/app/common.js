const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(p,m='GET',b){
  const r=await fetch('/api'+p,{method:m,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:b!==undefined?JSON.stringify(b):undefined});
  if(r.status===401&&!p.startsWith('/auth')){location='/app/login.html';throw new Error('login')}
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||('Error '+r.status));return j}
function toast(t,bad){const e=$('#toast')||document.body.appendChild(Object.assign(document.createElement('div'),{id:'toast'}));
  e.textContent=t;e.style.display='block';e.style.borderColor=bad?'#ff5d73':'#5dffc4';clearTimeout(e.t);e.t=setTimeout(()=>e.style.display='none',3500)}
async function act(path,method='POST',body={},reload){try{await api(path,method,body);toast('Done');if(reload)await reload()}catch(e){toast(e.message,1)}}
const chip=s=>`<span class="chip ${esc(s)}">${esc(String(s).replace('_',' '))}</span>`;
async function logout(){await api('/auth/logout','POST');location='/app/login.html'}
async function boot(role,title){
  const u=await api('/me');if(u.role!==role){location='/app/login.html';throw new Error('role')}
  document.body.insertAdjacentHTML('afterbegin',`<header><b>SAKHI KHATA</b><span>${esc(title)} · ${esc(u.name)}</span><a class="btn" href="/">Home</a><button onclick="logout()">Logout</button></header>`);
  if(!u.consented)document.body.insertAdjacentHTML('beforeend',`<div class="modal" id="cm"><div class="card"><h2>Your data, your consent</h2>
   <p>Sakhi Khata keeps your orders and earnings records so your group can price, plan and pay fairly. Members see only their own pay. Officers see only totals. You can ask your group leader to correct or remove your records.</p>
   <button onclick="api('/me/consent','POST').then(()=>$('#cm').remove())">I agree</button> <button class="red" onclick="logout()">No, log out</button></div></div>`);
  return u}
