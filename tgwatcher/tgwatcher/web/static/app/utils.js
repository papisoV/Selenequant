// utils.js — pure utility helpers (Phase 3B)
// Extracted from app.js. Non-module: functions are global.
// Dependencies: none (CHART_COLORS also declared in state.js but app.js still
// holds the legacy `const CHART_COLORS` — kept here for backward compat until
// Phase 3C removes the app.js duplicate).

const CHART_COLORS=['#00e5ff','#00ff88','#ffb300','#a855f7','#f472b6','#60a5fa','#2dd4bf','#ff3d71'];

function _isLightTheme(){return document.documentElement.getAttribute('data-theme')==='light'}
function _chartColors(){
  const light=_isLightTheme();
  return{
    grid:light?'#d5d9e0':'#1a1f2e',
    tick:light?'#718096':'#6b7a8d',
    legend:light?'#4a5568':'#a0aec0',
    tooltipBg:light?'#ebedf2':'#141a26',
    tooltipTitle:light?'#1a2030':'#e8ecf1',
    tooltipBody:light?'#4a5568':'#a0aec0',
    border:light?'rgba(0,0,0,.08)':'rgba(255,255,255,.06)',
  };
}
function _chartOpts(){
  const c=_chartColors();
  return{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:c.legend,font:{family:"'Inter'",size:11},boxWidth:12}},tooltip:{backgroundColor:c.tooltipBg,titleColor:c.tooltipTitle,bodyColor:c.tooltipBody,borderColor:c.border,borderWidth:1}},interaction:{intersect:false,mode:'index'}};
}
function _chartScales(){
  const c=_chartColors();
  return{x:{grid:{color:c.grid,lineWidth:1},ticks:{color:c.tick,maxTicksLimit:10,font:{family:"'JetBrains Mono'",size:10}},border:{color:c.border}},y:{grid:{color:c.grid,lineWidth:1},ticks:{color:c.tick,font:{family:"'JetBrains Mono'",size:10}},border:{color:c.border}}};
}

function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function fmtTime(iso){if(!iso)return'-';const d=new Date(iso+'Z');if(isNaN(d))return iso;const y=d.getFullYear(),m=String(d.getMonth()+1).padStart(2,'0'),day=String(d.getDate()).padStart(2,'0'),h=String(d.getHours()).padStart(2,'0'),mi=String(d.getMinutes()).padStart(2,'0');return `${y}-${m}-${day} ${h}:${mi}`}
function fmtTimeShort(iso){if(!iso)return'--:--';const d=new Date(iso+'Z');if(isNaN(d))return iso;return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}
function fmtChatTime(dateStr){
  if(!dateStr)return '';
  // DB stores UTC; append 'Z' so the browser parses as UTC, then getHours()
  // returns local time (consistent with fmtTime).
  const iso=(dateStr.includes('T')?dateStr:dateStr.replace(' ','T'))+'Z';
  const d=new Date(iso);
  if(isNaN(d))return '';
  const now=new Date();
  const diff=(now-d)/1000;
  if(diff<60)return '刚刚';
  if(diff<3600)return Math.floor(diff/60)+'分钟前';
  if(diff<86400)return Math.floor(diff/3600)+'小时前';
  if(diff<86400*7)return Math.floor(diff/86400)+'天前';
  const mm=String(d.getMonth()+1).padStart(2,'0');
  const dd=String(d.getDate()).padStart(2,'0');
  return `${d.getFullYear()}-${mm}-${dd}`;
}
function _fmtDuration(sec){
  if(sec<60)return sec+'秒';
  if(sec<3600)return Math.floor(sec/60)+'分'+(sec%60?sec%60+'秒':'');
  const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60);
  return h+'时'+(m?m+'分':'');
}
