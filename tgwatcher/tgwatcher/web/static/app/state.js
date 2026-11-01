// state.js — cross-domain global state (Phase 3C)
// Plain globals (non-module pattern). All scripts share these via bare names.
// SSE-private state (sseConnected, _sseAbort, etc.) stays in sse.js.
const API='';

let currentChat=null;
let currentPage=1;
let pageSize=50;
let totalMessages=0;
let crawlRunning=false;
let expandedRow=null;

let phoneCodeHash=null;
let allDialogs=[];

let authToken=localStorage.getItem('tgwatcher_token')||'';

let searchTimer=null;
let _loginCheckPending=false;
let _connCheckPending=false;

// Messages
let _msgReqSeq=0;
let _msgAbort=null;

// Export
let _exportPreviewTimer=null;

// Signal export
let _signalExportPreviewTimer=null;

// Dashboard charts
let trendChart=null;
let comparisonChart=null;

// Groups view + crawl
let autoPollState=[];
let autoPollTimer=null;

let listenerState={enabled:false,groups:[]};
let webhookState={enabled:false,endpoints:[]};

// Signal tab charts
let signalTrendChart=null;
let signalEventChart=null;
