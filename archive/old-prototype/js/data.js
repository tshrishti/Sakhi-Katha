/* ===== Demo data ===== */
const GROUP_WA='919999999999',BUYER='Jaipur Green Mart',GOAL=7000;
const P={bag:{n:'cloth bag',fab:.6,rate:38,perDay:10,f:1},shoe:{n:'jute shoe',fab:.9,rate:420,perDay:4,f:11}};
const SR={Cutting:4,Stitching:12,Finishing:6},STAGES=Object.keys(SR);
const M=[['Lakshmi Devi',['Stitching','Finishing']],['Radha Kumari',['Stitching']],['Meena Rao',['Cutting','Finishing']],['Savitri Bai',['Cutting']],['Kamla Devi',['Stitching']],['Pushpa Singh',['Finishing','Cutting']],['Sunita Verma',['Stitching']],['Geeta Nair',['Finishing']]].map((m,i)=>({id:i+1,name:m[0],skills:m[1],on:i!=2,earn:[6400,5200,3100,4100,5800,4600,6100,3900][i],done:[312,240,150,380,290,260,330,210][i],pay:0}));
const USERS=[{e:'kavita@sakhi.in',p:'leader123',r:'seller',n:'Kavita Sharma'},{e:'buyer@jaipurmart.in',p:'buyer123',r:'buyer',n:BUYER},...M.map(m=>({e:m.name.split(' ')[0].toLowerCase()+'@sakhi.in',p:'member123',r:'member',id:m.id,n:m.name}))];
const DEMO={seller:USERS[0],buyer:USERS[1],member:USERS[2]};
const ROLE={seller:'Group leader',buyer:'Buyer',member:'Member'};
const TABS={seller:[['ov','Overview'],['or','Orders'],['te','Team'],['iv','Inventory'],['po','Payouts']],member:[['ho','Home'],['ta','My tasks'],['ea','Earnings'],['no','Notices']],buyer:[['ne','New order'],['my','My orders'],['up','Updates']]};
let O=[],T=[],W=[],B=[],A=[{t:'Fabric for the new order arrives on Friday. Please finish pending cutting by Thursday.',d:'Today'}];
let S={u:null,tab:'',lr:'seller',ch:'web',stock:330,base:48200,nid:103,tn:1,err:''};
const prog=o=>{const t=T.filter(x=>x.o==o.id&&x.s!='void');return t.length?t.reduce((a,x)=>a+x.done,0)/(o.q*3):0};
const status=o=>o.s=='acc'&&prog(o)>=.999?'rdy':o.s;
const dstr=d=>new Date(Date.now()+d*864e5).toLocaleDateString('en-IN',{day:'numeric',month:'short'});
const inr=n=>'₹'+Math.round(n).toLocaleString('en-IN');
const mem=id=>M.find(m=>m.id==id),ord=id=>O.find(o=>o.id==id),pr=o=>P[o.p];
function alloc(o){STAGES.forEach(st=>{let pool=M.filter(m=>m.on&&m.skills.includes(st));if(!pool.length)pool=M.filter(m=>m.on);const b=Math.floor(o.q/pool.length);pool.forEach((m,i)=>T.push({id:S.tn++,o:o.id,st,m:m.id,q:b+(i<o.q-b*pool.length?1:0),done:0,s:'new',why:''}))})}
O.push({id:101,b:'Anand Gifts',p:'bag',q:200,d:4,ch:'web',s:'acc'},{id:102,b:BUYER,p:'bag',q:500,d:6,ch:'wa',s:'new'},{id:100,b:BUYER,p:'bag',q:150,d:-3,ch:'web',s:'done',rate:0});
alloc(O[0]);T.filter(t=>t.o==101).forEach(t=>{t.s='ok';t.done=Math.round(t.q*(t.st=='Cutting'?.9:t.st=='Stitching'?.55:.25))});
{const r=T.find(t=>t.o==101&&t.m==2);if(r){r.s='no';r.why='Hand injury, doctor advised rest for 5 days';r.done=0}}
W.push({o:0,t:'New order #102: 500 cloth bags from '+BUYER+', needed in 6 days. Reply ACCEPT or open the dashboard.'},{o:1,t:'Order #101 accepted. Anand Gifts has been told by WhatsApp.'});
B.push({t:'Order #100 was delivered. How was it? You can rate it in My orders.'});