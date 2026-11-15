const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const UI_COPY={
  hi:{Home:'होम',Overview:'अवलोकन',Orders:'ऑर्डर',Team:'टीम',Quality:'गुणवत्ता',Delivery:'डिलीवरी',Payouts:'भुगतान',Pricing:'दरें',Alerts:'अलर्ट',Notices:'सूचनाएँ','My tasks':'मेरे काम',Earnings:'कमाई','New order':'नया ऑर्डर',Group:'समूह',Me:'मेरी जानकारी',Logout:'लॉग आउट',Live:'लाइव',Connecting:'जुड़ रहा है',Search:'खोजें','My active work':'मेरा सक्रिय काम','Latest activity':'हाल की गतिविधि','Latest updates':'नए अपडेट','Needs your decision':'आपके फैसले की ज़रूरत','Live orders':'चल रहे ऑर्डर','See all orders':'सभी ऑर्डर देखें','Nothing waiting for you.':'अभी आपके लिए कुछ बाकी नहीं है।','No recent updates.':'कोई नया अपडेट नहीं।','Light mode':'हल्की थीम','Dark mode':'गहरी थीम'},
  hing:{Home:'Home',Overview:'Overview',Orders:'Orders',Team:'Team',Quality:'Quality',Delivery:'Delivery',Payouts:'Payouts',Pricing:'Rates',Alerts:'Alerts',Notices:'Messages','My tasks':'Mere tasks',Earnings:'Kamai','New order':'Naya order',Group:'Group',Me:'Meri jaankari',Logout:'Logout',Live:'Live',Connecting:'Connect ho raha hai','My active work':'Mera chal raha kaam','Latest activity':'Recent activity','Latest updates':'Naye updates','Needs your decision':'Aapke decision ka wait','Live orders':'Chalte orders','See all orders':'Saare orders dekhein','Nothing waiting for you.':'Abhi kuch pending nahi hai.','No recent updates.':'Koi naya update nahi.','Light mode':'Light mode','Dark mode':'Dark mode'}
};
function uiTranslate(root=document.body){
  const lang=document.documentElement.dataset.language||'en',dict=UI_COPY[lang];
  if(!dict||!root)return;
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  while(walker.nextNode()){
    const node=walker.currentNode;
    if(node.__sakhiSource===undefined)node.__sakhiSource=node.nodeValue;
    const source=node.__sakhiSource, trimmed=source.trim();
    node.nodeValue=dict[trimmed]?source.replace(trimmed,dict[trimmed]):source;
  }
}
window.SakhiI18n={apply:uiTranslate};
new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{if(node.nodeType===Node.ELEMENT_NODE)uiTranslate(node)}))).observe(document.documentElement,{childList:true,subtree:true});
async function api(p,m='GET',b){
  const r=await fetch('/api'+p,{method:m,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:b!==undefined?JSON.stringify(b):undefined});
  if(r.status===401&&!p.startsWith('/auth')){location='/app/login.html?v=phase3';throw new Error('login')}
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||('Error '+r.status));return j}
function toast(t,bad){const e=$('#toast')||document.body.appendChild(Object.assign(document.createElement('div'),{id:'toast'}));
  e.textContent=t;e.style.display='block';e.style.borderColor=bad?'#ff5d73':'#5dffc4';clearTimeout(e.t);e.t=setTimeout(()=>e.style.display='none',3500)}
async function act(path,method='POST',body={},reload){try{await api(path,method,body);toast('Done');if(reload)await reload()}catch(e){toast(e.message,1)}}
const chip=s=>`<span class="chip ${esc(s)}">${esc(String(s).replace('_',' '))}</span>`;
async function logout(){await api('/auth/logout','POST');location='/app/login.html?v=phase3'}
function applyTheme(){
  const theme=localStorage.getItem('sk_theme')==='dark'?'dark':'light';
  document.documentElement.dataset.theme=theme;
  document.body.dataset.theme=theme;
  document.querySelectorAll('[data-theme-toggle]').forEach(button=>{
    const label=theme==='dark'?'Light mode':'Dark mode';
    button.textContent=label;
    button.setAttribute('aria-label',label);
    button.title=label;
  });
}
function toggleTheme(){
  localStorage.setItem('sk_theme',document.documentElement.dataset.theme==='dark'?'light':'dark');
  applyTheme();
}
document.addEventListener('click',event=>{
  if(event.target.closest('[data-theme-toggle]'))toggleTheme();
});
applyTheme();
async function setDashboardLanguage(lang){
  try{
    const result=await api('/me/lang','POST',{lang});
    document.documentElement.dataset.language=result.lang;
    document.documentElement.lang=result.lang==='hi'?'hi':'en';
    uiTranslate();
    toast('Language preference saved');
  }catch(error){toast(error.message,1)}
}
async function boot(role,title){
  const u=await api('/me');if(u.role!==role){location='/app/login.html';throw new Error('role')}
  document.documentElement.dataset.language=u.lang||'en';
  document.documentElement.lang=u.lang==='hi'?'hi':'en';
  document.body.insertAdjacentHTML('afterbegin',`<header><b>SAKHI KHATA</b><span>${esc(title)} · ${esc(u.name)}</span><a class="btn secondary" href="/">Home</a><label class="language-switch">Language <select id="ui-language" aria-label="Dashboard language" onchange="setDashboardLanguage(this.value)"><option value="en" ${u.lang==='en'?'selected':''}>English</option><option value="hi" ${u.lang==='hi'?'selected':''}>हिन्दी</option><option value="hing" ${u.lang==='hing'?'selected':''}>Hinglish</option></select></label><button class="secondary" type="button" data-theme-toggle>Dark mode</button><button onclick="logout()">Logout</button></header>`);
  uiTranslate(document.body);
  applyTheme();
  if(!u.consented)document.body.insertAdjacentHTML('beforeend',`<div class="modal" id="cm"><div class="card"><h2>Your data, your consent</h2>
   <p>Sakhi Khata keeps your orders and earnings records so your group can price, plan and pay fairly. Members see only their own pay. Officers see only totals. You can ask your group leader to correct or remove your records.</p>
   <button onclick="api('/me/consent','POST').then(()=>$('#cm').remove())">I agree</button> <button class="red" onclick="logout()">No, log out</button></div></div>`);
  return u}
