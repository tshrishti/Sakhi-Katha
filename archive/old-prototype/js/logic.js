/* ===== Logic ===== */
function feas(o){const p=pr(o),av=M.filter(m=>m.on).length,acc=O.filter(x=>x.s=='acc'&&x.id!=o.id),commit=acc.reduce((a,x)=>a+x.q*(1-prog(x)),0);
 const fabNeed=o.q*p.fab,fabFree=S.stock-O.filter(x=>x.s=='acc').reduce((a,x)=>a+x.q*(1-prog(x))*pr(x).fab,0);
 const daysNeed=Math.ceil((o.q+commit)/(av*p.perDay)),wNeed=Math.ceil((o.q+commit)/(p.perDay*o.d));
 const ok={w:wNeed<=av,f:fabNeed<=fabFree,d:daysNeed<=o.d};return{av,wNeed,fabNeed:Math.round(fabNeed),fabFree:Math.round(fabFree),daysNeed,ok,all:ok.w&&ok.f&&ok.d}}
function toast(t){const e=document.getElementById('toast');e.textContent=t;e.classList.add('on');clearTimeout(toast.t);toast.t=setTimeout(()=>e.classList.remove('on'),3200)}
const me=()=>S.u.id?mem(S.u.id):null;
const act={
 role(r){S.lr=r;S.err='';draw()},ch(c){S.ch=c;draw()},tab(t){S.tab=t;draw()},
 logout(){S.u=null;draw()},
 demo(){S.err='';draw()},
 accept(id,c){const o=ord(id),f=feas(o);if(c)o.d=f.daysNeed;o.s='acc';alloc(o);const m=`Order #${id} accepted${c?' for '+dstr(o.d):''}. ${o.b} has been told by WhatsApp.`;W.unshift({o:1,t:m});if(o.b==BUYER)B.unshift({t:`Your order #${id} was accepted${c?'. New delivery date: '+dstr(o.d):', delivery by '+dstr(o.d)}.`});sync('decide',{id,status:'accepted',days:o.d});toast('Accepted. Tasks sent to members by skill.');draw()},
 decline(id){const o=ord(id);o.s='no';W.unshift({o:1,t:`Order #${id} declined. ${o.b} has been told by WhatsApp.`});if(o.b==BUYER)B.unshift({t:`Order #${id} could not be taken this time. Try a later date or a smaller quantity.`});sync('decide',{id,status:'declined'});draw()},
 deliver(id){const o=ord(id);o.s='done';W.unshift({o:1,t:`Order #${id} marked delivered.`});if(o.b==BUYER)B.unshift({t:`Order #${id} has been delivered. Please rate it in My orders.`});draw()},
 tacc(id){T.find(t=>t.id==id).s='ok';draw()},
 tno(id,why){const t=T.find(x=>x.id==id);t.s='no';t.why=why;W.unshift({o:0,t:`${mem(t.m).name} can’t take ${t.q} units of ${t.st}: ${why}`});sync('taskUpdate',{id,status:'declined',why});toast('Sent to your group leader.');draw()},
 log(id){const t=T.find(x=>x.id==id),n=Math.min(10,t.q-t.done);t.done+=n;mem(t.m).earn+=n*SR[t.st]*pr(ord(t.o)).f;sync('taskUpdate',{id,done:t.done});draw()},
 fin(id){const t=T.find(x=>x.id==id),n=t.q-t.done;t.done=t.q;mem(t.m).earn+=n*SR[t.st]*pr(ord(t.o)).f;draw()},
 reassign(id){const t=T.find(x=>x.id==id),c=M.filter(m=>m.on&&m.id!=t.m&&m.skills.includes(t.st))[0];if(!c)return toast('No one else has this skill free.');t.m=c.id;t.s='new';t.why='';toast('Reassigned to '+c.name);draw()},
 excuse(id){const t=T.find(x=>x.id==id);t.s='void';const rest=M.filter(m=>m.on&&m.id!=t.m&&m.skills.includes(t.st));rest.forEach((m,i)=>{const x=T.find(y=>y.o==t.o&&y.st==t.st&&y.m==m.id);if(x)x.q+=Math.floor(t.q/rest.length)+(i<t.q%rest.length?1:0)});toast('Accepted. The units were shared out again.');draw()},
 avail(id){const m=mem(id);m.on=!m.on;draw()},
 restock(){S.stock+=100;toast('Added 100 m of fabric to stock.');draw()},
 pay(id){mem(id).pay=1;sync('payout',{member:id});toast('Payout approved.');draw()},
 payall(){M.forEach(m=>m.pay=1);toast('All payouts approved.');draw()},
 leave(){W.unshift({o:0,t:`${me().name} asked for leave this week.`});toast('Leave request sent to your leader.')},
 ask(w){W.unshift({o:0,t:`${me().name} asked about her earnings for: ${w}`});toast('Your leader will explain this entry.')},
 cancel(id){const o=ord(id);o.s='no';W.unshift({o:0,t:`${o.b} cancelled order #${id}.`});draw()},
 reorder(id){const o=ord(id),n={...o,id:S.nid++,s:'new',d:Math.max(o.d,7),rate:undefined};O.push(n);W.unshift({o:0,t:`Repeat order #${n.id}: ${n.q} ${pr(n).n}s from ${n.b}.`});toast('Repeat order sent.');draw()},
 rate(id,n){ord(id).rate=n;draw()}
};