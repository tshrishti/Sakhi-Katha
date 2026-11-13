/* ===== Backend hook: set your routes here. These paths are placeholders until you share your API. ===== */
const API_BASE='https://sakhi-katha-1.onrender.com';
const ROUTES={login:'/auth/login',placeOrder:'/orders',decide:'/orders/decision',taskUpdate:'/tasks/update',payout:'/payouts/approve',notice:'/notices'};
async function sync(r,b){try{const x=await fetch(API_BASE+ROUTES[r],{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return x.ok?await x.json().catch(()=>({})):null}catch(e){return null}}
