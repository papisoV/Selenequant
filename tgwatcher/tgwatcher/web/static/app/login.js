// login.js — login flow functions (Phase 3C)
// Extracted from app.js. Non-module: functions are global.
// Dependencies (resolved at call time):
//   - authToken, phoneCodeHash, _loginCheckPending (state.js)
//   - API (state.js)
//   - api (api-client.js)
//   - initApp (ui.js)

function showLoginStep(step){for(let i=0;i<=3;i++)document.getElementById('loginStep'+i).classList.toggle('active',i===step)}

function setAuthToken(){
  const t=document.getElementById('authTokenInput').value.trim();
  if(!t){document.getElementById('loginError0').textContent='请输入 Token';return}
  authToken=t;localStorage.setItem('tgwatcher_token',t);checkLogin();
}

async function checkLogin(){
  if(!authToken){
    // Try auto-login from localhost bootstrap endpoint first
    try{
      const r=await fetch(API+'/api/auth/bootstrap',{headers:{'Content-Type':'application/json'}});
      if(r.ok){
        const j=await r.json();
        if(j&&j.token){
          authToken=j.token;localStorage.setItem('tgwatcher_token',j.token);
        }
      }
    }catch(e){/* network error — fall back to manual entry */}
    if(!authToken){document.getElementById('loginOverlay').style.display='flex';return}
  }
  if(_loginCheckPending)return;_loginCheckPending=true;
  try{
    const r=await api('/api/login/status');
    if(!r){
      // Network error / timeout (e.g. catchup crawl holding Telethon lock).
      // Do NOT kick back to login overlay — the previous checkLogin already
      // established the session. checkConnection() will recover on next poll.
      if(!document.getElementById('app').style.display || document.getElementById('app').style.display==='none'){
        document.getElementById('loginOverlay').style.display='flex';
      }
      return;
    }
    if(r.error==='Unauthorized'){authToken='';localStorage.removeItem('tgwatcher_token');showLoginStep(0);document.getElementById('loginError0').textContent='Token 无效';document.getElementById('loginOverlay').style.display='flex';return}
    if(r.logged_in){document.getElementById('loginOverlay').style.display='none';document.getElementById('app').style.display='flex';initApp();return}
    showLoginStep(1);document.getElementById('loginOverlay').style.display='flex';
  }finally{_loginCheckPending=false}
}

async function sendCode(){
  document.getElementById('loginError1').textContent='';document.getElementById('btnSendCode').disabled=true;document.getElementById('btnSendCode').textContent='发送中...';
  const r=await api('/api/login',{method:'POST',body:JSON.stringify({})});
  document.getElementById('btnSendCode').disabled=false;document.getElementById('btnSendCode').textContent='发送验证码';
  if(!r){document.getElementById('loginError1').textContent='网络错误';return}
  if(r.status==='code_sent'){phoneCodeHash=r.phone_code_hash;showLoginStep(2);document.getElementById('loginCode').focus();document.getElementById('btnResendCode').style.display='inline'}
  else if(r.status==='already_logged_in'){document.getElementById('loginOverlay').style.display='none';document.getElementById('app').style.display='flex';initApp()}
  else document.getElementById('loginError1').textContent=r.error||'发送失败';
}

async function verifyCode(){
  const code=document.getElementById('loginCode').value.trim();
  if(!code){document.getElementById('loginError2').textContent='请输入验证码';return}
  document.getElementById('loginError2').textContent='';document.getElementById('btnVerify').disabled=true;document.getElementById('btnVerify').textContent='验证中...';
  const r=await api('/api/login',{method:'POST',body:JSON.stringify({code,phone_code_hash:phoneCodeHash})});
  document.getElementById('btnVerify').disabled=false;document.getElementById('btnVerify').textContent='验证';
  if(!r){document.getElementById('loginError2').textContent='网络错误';return}
  if(r.status==='logged_in')showLoginStep(3);
  else{document.getElementById('loginError2').textContent=r.error||'验证失败';document.getElementById('btnResendCode').style.display='inline'}
}

function afterLogin(){document.getElementById('loginOverlay').style.display='none';document.getElementById('app').style.display='flex';initApp()}
function showTokenHelp(){const h=document.getElementById('tokenHelp');h.style.display=h.style.display==='none'?'block':'none'}

async function resendCode(){
  document.getElementById('loginError2').textContent='';document.getElementById('btnResendCode').disabled=true;document.getElementById('btnResendCode').textContent='重新发送中...';
  const r=await api('/api/login',{method:'POST',body:JSON.stringify({})});
  document.getElementById('btnResendCode').disabled=false;document.getElementById('btnResendCode').textContent='重新发送';
  if(!r){document.getElementById('loginError2').textContent='网络错误';return}
  if(r.status==='code_sent'){phoneCodeHash=r.phone_code_hash;document.getElementById('loginCode').value='';document.getElementById('loginCode').focus()}
  else if(r.status==='already_logged_in'){document.getElementById('loginOverlay').style.display='none';document.getElementById('app').style.display='flex';initApp()}
  else document.getElementById('loginError2').textContent=r.error||'发送失败';
}
