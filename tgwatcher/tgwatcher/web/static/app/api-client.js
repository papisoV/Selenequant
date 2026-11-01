// api-client.js — fetch wrapper (Phase 3B)
// Extracted from app.js. Non-module: `api` is global.
// Dependencies (resolved at call time, not load time):
//   - authToken (let in app.js, legacy global)
//   - API (const in app.js, legacy global)
//   - showToast (function in render.js)
// We do NOT redeclare authToken/API here — that would cause
// "Identifier has already been declared" SyntaxError across scripts.

async function api(path,opts={}){
  const headers={'Content-Type':'application/json',...opts.headers};
  if(authToken)headers['Authorization']='Bearer '+authToken;
  try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),15000);
    const r=await fetch(API+path,{headers,...opts,signal:opts.signal||ctrl.signal});clearTimeout(timer);
    if(r.status===401){authToken='';localStorage.removeItem('tgwatcher_token');location.reload();return null}
    if(!r.ok){const err=await r.json().catch(()=>({}));showToast(err.error||('HTTP '+r.status),'error');return null}
    return await r.json()}catch(e){if(e.name==='AbortError'&&!opts.signal)showToast('请求超时 — 请检查服务器','error');else if(e.name!=='AbortError')console.error('API error:',e);return null}
}
