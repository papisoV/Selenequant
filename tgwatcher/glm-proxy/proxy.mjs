/**
 * GLM Refusal Proxy - 本地代理服务器（高并发稳定版）
 *
 * 优化：
 * 1. 逐 token 拒绝检测 — 每收到内容立即检测，不等累积
 * 2. 零延迟首次重试 — 拒绝是确定性的，立即重试
 * 3. 激进退避表 — 0→200→500→1000→2000→5000→10000→20000ms
 * 4. SSE buffer 解析 — 减少 split/parse 开销
 * 5. 连接池大幅扩容 — maxSockets=100, maxFreeSockets=20, lifo 复用
 * 6. 晚期拒绝中断重试 — 即使已发数据也中断重试
 * 7. 响应头立即发送 — 收到上游 status 即发，不等数据
 * 8. 客户端断连检测 — 及时释放 socket，不浪费连接池
 * 9. 写安全检查 — 所有 res 操作前检查 writableEnded
 * 10. 进程级错误兜底 — uncaughtException/unhandledRejection 不崩进程
 *
 * 用法: node proxy.mjs
 */

import http from 'node:http';
import https from 'node:https';
import { URL } from 'node:url';

// ============ 配置 ============
const PROXY_PORT = 3827;
const UPSTREAM_BASE = 'https://maas-coding-api.cn-huabei-1.xf-yun.com';
const MAX_RETRIES = 8;

// 激进退避表（毫秒）
const RETRY_DELAYS = [0, 200, 500, 1000, 2000, 5000, 10000, 20000];

// GLM 拒绝响应的特征文本（按长度排序，短在前便于早期匹配）
const REFUSAL_PATTERNS = [
  '作为AI',
  '作为人工',
  '作为一个人',
  '我无法',
  '我不能',
  '我还没学习',
  '我还没有学习',
  '这个问题我无法',
  '内容涉及',
  '请换个话题',
  '试试其他',
  '作为一个人工智能语言模型',
  '我还没学习如何回答这个问题',
  '我无法回答',
  '我不能回答',
  '我无法提供',
  '我不能提供',
  '这个问题我无法',
  '内容涉及敏感',
  '请换个话题',
  '试试其他问题',
];

// 构建前缀树用于逐字符检测
function buildPrefixTree(patterns) {
  const tree = {};
  for (const p of patterns) {
    let node = tree;
    for (const char of p) {
      if (!node[char]) node[char] = {};
      node = node[char];
    }
    node['$END'] = true; // 标记完整模式结束
  }
  return tree;
}

const REFUSAL_PREFIX_TREE = buildPrefixTree(REFUSAL_PATTERNS);

// ============ 503 / 429 全局退避（按模型隔离） ============
// 每个模型独立冷却，避免一个模型503卡住其他模型
const modelBusyState = new Map(); // model → { busyUntil, consecutive503, rateLimitUntil }

function getBusyState(model) {
  let state = modelBusyState.get(model);
  if (!state) {
    state = { busyUntil: 0, consecutive503: 0, rateLimitUntil: 0 };
    modelBusyState.set(model, state);
  }
  return state;
}

function markBusy(model) {
  const state = getBusyState(model);
  state.consecutive503++;
  // 503是排队竞争，短冷却快速重试比长等待更有效
  // 别人都在抢，你等太久反而排不上
  const cooldown = Math.min(state.consecutive503 * 300, 1500); // 300ms→600ms→900ms→1200ms→1500ms
  state.busyUntil = Date.now() + cooldown;
  log(`  🚦 上游繁忙，模型 ${model} 冷却 ${cooldown}ms (连续503: ${state.consecutive503})`);
}

function markRateLimited(model, retryAfterMs) {
  const state = getBusyState(model);
  const cooldown = retryAfterMs || 5000;
  state.rateLimitUntil = Math.max(state.rateLimitUntil, Date.now() + cooldown);
  log(`  🚦 429 限流，模型 ${model} 冷却 ${cooldown}ms`);
}

function markOk(model) {
  const state = getBusyState(model);
  state.consecutive503 = 0;
  state.busyUntil = 0;
  state.rateLimitUntil = 0;
}

async function waitIfBusy(model) {
  const state = getBusyState(model);
  const waitUntil = Math.max(state.busyUntil, state.rateLimitUntil);
  if (waitUntil > 0) {
    const wait = waitUntil - Date.now();
    if (wait > 0) {
      log(`  🚦 等待模型 ${model} 冷却 ${wait}ms`);
      await sleep(wait);
    }
  }
}

// ============ 错误分类与差异化退避 ============
// 不可重试：鉴权失败、参数错误等确定性错误
const NO_RETRY_CODES = [401, 403];
// 429 需要解析 body 区分限流 vs 鉴权失败
// 500/502/503 服务端错误可重试

function parseUpstreamError(statusCode, body) {
  let parsed = null;
  try { parsed = JSON.parse(body); } catch {}

  // 429 + 鉴权失败 → 不可重试
  if (statusCode === 429) {
    const code = parsed?.error?.code;
    const msg = parsed?.error?.message || '';
    if (code === 11210 || msg.includes('authorization failed') || msg.includes('auth')) {
      return { retryable: false, reason: 'auth_failed', code };
    }
    // 真正的限流 429 → 可重试，用更长退避
    return { retryable: true, reason: 'rate_limited', code };
  }

  // 401/403 → 不可重试
  if (NO_RETRY_CODES.includes(statusCode)) {
    return { retryable: false, reason: 'forbidden', code: parsed?.error?.code };
  }

  // 400 → 通常不可重试（参数错误），但某些 400 可能是临时问题
  if (statusCode === 400) {
    return { retryable: false, reason: 'bad_request', code: parsed?.error?.code };
  }

  // 500/502/503/504 → 服务端错误，可重试
  if (statusCode >= 500) {
    return { retryable: true, reason: 'server_error', code: parsed?.error?.code };
  }

  // 其他 4xx → 不可重试
  if (statusCode >= 400) {
    return { retryable: false, reason: 'client_error', code: parsed?.error?.code };
  }

  return { retryable: true, reason: 'unknown' };
}

// 差异化退避：根据错误类型返回不同延迟
function getSmartRetryDelay(attempt, errorInfo) {
  if (errorInfo?.reason === 'rate_limited') {
    // 限流：更长退避，给上游喘息时间
    const delays = [1000, 2000, 4000, 8000, 16000, 30000, 60000, 120000];
    return delays[Math.min(attempt - 1, delays.length - 1)];
  }
  if (errorInfo?.reason === 'server_error') {
    // 503排队竞争：快速重试抢位，不退避太久
    const delays = [200, 400, 600, 800, 1000, 1500, 2000, 3000];
    return delays[Math.min(attempt - 1, delays.length - 1)];
  }
  // 默认（拒绝/空响应等）
  return RETRY_DELAYS[Math.min(attempt - 1, RETRY_DELAYS.length - 1)];
}

// ============ HTTPS 连接复用（高并发版） ============
const httpsAgent = new https.Agent({
  keepAlive: true,
  keepAliveMsecs: 30000,
  maxSockets: 100,
  maxFreeSockets: 20,
  timeout: 300000,
  scheduling: 'lifo', // 最近用过的 socket 优先复用，减少冷连接
});

// ============ 进程级错误兜底 ============
process.on('uncaughtException', (err) => {
  log(`!!! 未捕获异常: ${err.message}`);
  // 不退出，让进程继续跑
});
process.on('unhandledRejection', (reason) => {
  log(`!!! 未处理的 Promise 拒绝: ${reason}`);
});

// ============ 工具函数 ============

// 逐字符检测：检查累积文本是否匹配任何拒绝模式的前缀
// 返回: 0=不匹配, 1=可能是拒绝前缀(继续), 2=确认拒绝
function checkRefusalPrefix(text, tree) {
  let node = tree;
  let matchedEnd = false;

  for (const char of text) {
    if (node[char]) {
      node = node[char];
      if (node['$END']) matchedEnd = true;
    } else {
      // 当前字符不匹配任何前缀
      return matchedEnd ? 2 : 0;
    }
  }

  // 文本结束但仍在树中 = 可能是前缀
  return matchedEnd ? 2 : 1;
}

function getRetryDelay(attempt) {
  return RETRY_DELAYS[Math.min(attempt - 1, RETRY_DELAYS.length - 1)];
}

function sleep(ms) {
  if (ms <= 0) return Promise.resolve();
  return new Promise(r => setTimeout(r, ms));
}

function log(msg) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  console.log(`[${ts}] ${msg}`);
}

// ============ 非流式请求 ============
function forwardNonStream(reqBody, headers, path, method, abortSignal) {
  return new Promise((resolve, reject) => {
    if (abortSignal?.aborted) { reject(new Error('aborted')); return; }

    const url = new URL(path, UPSTREAM_BASE);
    const bodyStr = JSON.stringify(reqBody);

    const options = {
      hostname: url.hostname,
      port: 443,
      path: url.pathname + url.search,
      method,
      headers: {
        ...headers,
        host: url.hostname,
        'content-length': Buffer.byteLength(bodyStr),
      },
      agent: httpsAgent,
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        resolve({
          statusCode: res.statusCode,
          headers: res.headers,
          body: data,
        });
      });
    });

    req.on('error', reject);

    // 客户端断连时中止上游请求
    if (abortSignal) {
      const onAbort = () => { req.destroy(); reject(new Error('aborted')); };
      abortSignal.addEventListener('abort', onAbort, { once: true });
    }

    req.write(bodyStr);
    req.end();
  });
}

// ============ SSE 解析器（buffer 优化） ============
class SSEParser {
  constructor() {
    this.buffer = '';
    this.pendingLines = [];
  }

  feed(chunk) {
    this.buffer += chunk.toString();
    const lines = this.buffer.split('\n');
    // 保留最后一个可能不完整的行
    this.buffer = lines.pop() || '';
    this.pendingLines.push(...lines);
  }

  *iterLines() {
    while (this.pendingLines.length) {
      yield this.pendingLines.shift();
    }
  }

  flush() {
    if (this.buffer) {
      const line = this.buffer;
      this.buffer = '';
      return line;
    }
    return null;
  }
}

// 从 SSE 行提取 content（兼容 OpenAI / 讯飞 / Anthropic 格式）
function extractContent(line) {
  if (!line.startsWith('data: ')) return null;
  const jsonStr = line.slice(6).trim();
  if (jsonStr === '[DONE]') return null;
  try {
    const parsed = JSON.parse(jsonStr);
    // Anthropic SSE 格式: content_block_delta → text_delta / input_json_delta / thinking_delta
    const delta = parsed?.delta;
    if (delta) {
      if (delta.type === 'text_delta' && delta.text) return delta.text;
      // input_json_delta 是 tool_use 的 JSON 片段，不算文本内容但表示有效响应
      if (delta.type === 'input_json_delta') return '';
      // thinking_delta 是 extended thinking 内容，也是有效数据
      if (delta.type === 'thinking_delta' && delta.thinking) return '';
      // stop_reason 等 delta 也标记为有效
      if (delta.type === 'stop_reason') return '';
    }
    // Anthropic SSE 事件类型都表示有效流数据
    if (parsed?.type === 'message_start' || parsed?.type === 'content_block_start' ||
        parsed?.type === 'content_block_stop' || parsed?.type === 'content_block_delta' ||
        parsed?.type === 'message_delta' || parsed?.type === 'message_stop') return '';
    // OpenAI/Anthropic 格式
    const c1 = parsed?.choices?.[0]?.delta?.content;
    if (c1) return c1;
    // 非流式 OpenAI 格式
    const c2 = parsed?.choices?.[0]?.message?.content;
    if (c2) return c2;
    // 讯飞 MaaS 格式 (可能用 text / result)
    const c3 = parsed?.data?.text;
    if (c3) return c3;
    const c4 = parsed?.result?.text;
    if (c4) return c4;
    // 通用：遍历 choices[0].delta 所有字段找字符串
    const openaiDelta = parsed?.choices?.[0]?.delta;
    if (openaiDelta && typeof openaiDelta === 'object') {
      for (const val of Object.values(openaiDelta)) {
        if (typeof val === 'string' && val.length > 0) return val;
      }
    }
    return null;
  } catch {
    return null;
  }
}

// ============ 流式请求（chunk 级透传 + 拒绝检测） ============
function forwardStreamAggressive(reqBody, headers, path, method, clientRes, abortSignal) {
  return new Promise((resolve, reject) => {
    if (abortSignal?.aborted) { reject(new Error('aborted')); return; }

    const url = new URL(path, UPSTREAM_BASE);
    const bodyStr = JSON.stringify(reqBody);

    const options = {
      hostname: url.hostname,
      port: 443,
      path: url.pathname + url.search,
      method,
      headers: {
        ...headers,
        host: url.hostname,
        'content-length': Buffer.byteLength(bodyStr),
      },
      agent: httpsAgent,
    };

    let headersTimer = null;
    const upstreamReq = https.request(options, (upstreamRes) => {
      let fullContent = '';
      let refused = false;
      let rawLinesLogged = 0;
      let hasValidData = false;
      let headersSent = false;
      const parser = new SSEParser();

      // 上游返回非 2xx 状态码 — 不转发，标记为可重试的上游错误
      if (upstreamRes.statusCode >= 400) {
        if (headersTimer) { clearTimeout(headersTimer); headersTimer = null; }
        let errBody = '';
        upstreamRes.on('data', chunk => errBody += chunk);
        upstreamRes.on('end', () => {
          log(`  ✗ 上游 HTTP ${upstreamRes.statusCode}: ${errBody.slice(0, 200)}`);
          resolve({ upstreamError: true, statusCode: upstreamRes.statusCode, fullContent: errBody, headersSent: false });
        });
        return;
      }

      // 延迟发送响应头：优先等有效数据（便于拒绝重试），但设超时保活（防客户端断开）
      const sendHeaders = () => {
        if (!headersSent && !clientRes.writableEnded) {
          if (headersTimer) { clearTimeout(headersTimer); headersTimer = null; }
          const outHeaders = { ...upstreamRes.headers };
          delete outHeaders['content-length'];
          // 禁用 chunked 之外的所有缓冲，强制每次 write 立即下发
          outHeaders['x-accel-buffering'] = 'no';
          outHeaders['cache-control'] = 'no-cache, no-transform';
          clientRes.writeHead(upstreamRes.statusCode, outHeaders);
          // 立即 flush TCP 缓冲，防止 Node 攒包导致客户端假死
          clientRes.flushHeaders?.();
          // 底层 socket 关闭 Nagle 算法，小 chunk 不合并
          clientRes.socket?.setNoDelay?.(true);
          headersSent = true;
        }
      };
      // 2秒内没收到有效数据就先发 headers，防止客户端因等不到响应而断开
      headersTimer = setTimeout(sendHeaders, 2000);

      upstreamRes.on('data', chunk => {
        if (refused || abortSignal?.aborted) return;

        // 解析 SSE 提取文本内容（用于拒绝检测）
        parser.feed(chunk);
        for (const line of parser.iterLines()) {
          if (rawLinesLogged < 5 && line.startsWith('data: ')) {
            log(`  🐛 SSE raw: ${line.slice(0, 200)}`);
            rawLinesLogged++;
          }
          const content = extractContent(line);
          if (content !== null) {
            sendHeaders();
            hasValidData = true;
            if (content) fullContent += content;

            // 逐 token 检测拒绝
            const check = checkRefusalPrefix(fullContent, REFUSAL_PREFIX_TREE);
            if (check === 2) {
              refused = true;
              if (headersTimer) { clearTimeout(headersTimer); headersTimer = null; }
              upstreamRes.destroy();
              resolve({ refused: true, fullContent, headersSent, hasValidData });
              return;
            }
          }
        }

        // 原样透传 chunk（保留完整 SSE 格式，包括空行分隔符）
        if (!refused && headersSent && !clientRes.writableEnded) {
          const ok = clientRes.write(chunk);
          // 强制把数据立刻推到 TCP，防止 Node 缓冲攒包导致客户端假死
          if (ok === false) {
            // 背压：等 drain，但不阻塞上游
          }
          // 主动 flush（cork 模式下需要）
          if (clientRes.socket?.writableNeedDrain === false) {
            clientRes.socket?.resume?.();
          }
        }
      });

      upstreamRes.on('end', () => {
        if (refused || abortSignal?.aborted) return;

        // 处理 buffer 中剩余内容
        const lastLine = parser.flush();
        if (lastLine) {
          const content = extractContent(lastLine);
          if (content !== null) {
            sendHeaders();
            hasValidData = true;
            if (content) fullContent += content;
          }
        }

        // 确保发送 headers（空响应情况）
        sendHeaders();

        // 最终检查
        const check = checkRefusalPrefix(fullContent, REFUSAL_PREFIX_TREE);
        if (check === 2) {
          resolve({ refused: true, fullContent, headersSent, hasValidData });
        } else {
          resolve({ refused: false, fullContent, headersSent, hasValidData });
        }
      });

      upstreamRes.on('error', (err) => {
        if (!refused) {
          reject(err);
        }
      });
    });

    upstreamReq.on('error', (err) => {
      if (headersTimer) { clearTimeout(headersTimer); headersTimer = null; }
      reject(err);
    });

    // 客户端断连时中止上游请求
    if (abortSignal) {
      const onAbort = () => { upstreamReq.destroy(); reject(new Error('aborted')); };
      abortSignal.addEventListener('abort', onAbort, { once: true });
    }

    upstreamReq.write(bodyStr);
    upstreamReq.end();
  });
}

// ============ 主请求处理 ============
async function handleRequest(req, res) {
  // HEAD request handling — health check
  if (req.method === 'HEAD') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end();
    log(`← HEAD ${req.url} → 200 (健康检查)`);
    return;
  }

  // count_tokens 短路 — 上游不支持此端点，直接估算返回
  if (req.url.includes('/count_tokens')) {
    let bodyStr = '';
    req.on('data', chunk => bodyStr += chunk);
    req.on('end', () => {
      let charCount = 0;
      try {
        const body = JSON.parse(bodyStr);
        // 累加 system 文本
        if (typeof body.system === 'string') charCount += body.system.length;
        else if (Array.isArray(body.system)) {
          for (const b of body.system) {
            if (b.type === 'text' && b.text) charCount += b.text.length;
          }
        }
        // 累加 messages 文本
        if (Array.isArray(body.messages)) {
          for (const msg of body.messages) {
            if (typeof msg.content === 'string') charCount += msg.content.length;
            else if (Array.isArray(msg.content)) {
              for (const b of msg.content) {
                if (b.type === 'text' && b.text) charCount += b.text.length;
                else if (b.type === 'tool_result' && b.content) {
                  if (typeof b.content === 'string') charCount += b.content.length;
                  else if (Array.isArray(b.content)) {
                    for (const c of b.content) {
                      if (c.type === 'text' && c.text) charCount += c.text.length;
                    }
                  }
                }
                else if (b.type === 'tool_use' && b.input) {
                  charCount += JSON.stringify(b.input).length;
                }
              }
            }
          }
        }
        // tool 用到的 tools 定义也计入
        if (Array.isArray(body.tools)) {
          charCount += JSON.stringify(body.tools).length;
        }
      } catch { charCount = bodyStr.length; }
      // 估算：中英混合约 3.5 字符/token，最低 1
      const inputTokens = Math.max(1, Math.round(charCount / 3.5));
      log(`← COUNT_TOKENS → ${inputTokens} tokens (estimated, ${charCount} chars)`);
      if (!res.writableEnded) {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ input_tokens: inputTokens }));
      }
    });
    return;
  }

  // 客户端断连检测 — 仅在响应未完成时连接断开才视为客户端主动断开
  const abortController = new AbortController();
  let clientGone = false;
  let requestComplete = false;
  const onClientClose = () => {
    // res.writableFinished=true 说明我们已正常 end()，不是客户端断开
    if (!requestComplete && !res.writableFinished) {
      clientGone = true;
      abortController.abort();
      log(`  ⊘ 客户端断开连接 ${req.method} ${req.url}`);
    }
  };
  res.on('close', onClientClose);

  let bodyStr = '';
  let bodyDone = false;
  req.on('data', chunk => bodyStr += chunk);
  req.on('end', async () => {
    bodyDone = true;
    if (clientGone) return;

    try {
      let reqBody;
      try {
        reqBody = JSON.parse(bodyStr);
      } catch {
        if (!res.writableEnded) {
          res.writeHead(400);
          res.end('Invalid JSON');
        }
        return;
      }

      const model = reqBody.model || 'unknown';
      const isStream = reqBody.stream === true;
      log(`→ ${req.method} ${req.url} model=${model} stream=${isStream}`);

      const fwdHeaders = { ...req.headers };
      delete fwdHeaders.host;
      delete fwdHeaders['content-length'];
      delete fwdHeaders.connection;

      let streamHeadersSent = false;
      const signal = abortController.signal;

      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        if (signal.aborted) {
          log(`  ⊘ 客户端已断开，放弃重试`);
          return;
        }

        // 等待模型级冷却（503 红绿灯）
        if (attempt > 1) await waitIfBusy(model);

        try {
          if (!isStream) {
            // ---- 非流式 ----
            const result = await forwardNonStream(reqBody, fwdHeaders, req.url, req.method, signal);

            if (signal.aborted) return;

            // 上游 HTTP 错误 — 分类判断是否可重试
            if (result.statusCode >= 400) {
              const errorInfo = parseUpstreamError(result.statusCode, result.body);
              if (result.statusCode === 503) markBusy(model);
              if (errorInfo.reason === 'rate_limited') markRateLimited(model, getSmartRetryDelay(attempt, errorInfo));
              if (!errorInfo.retryable) {
                log(`  ✗ 上游 HTTP ${result.statusCode} [${errorInfo.reason}] 不可重试，直接返回`);
                if (!res.writableEnded) {
                  res.writeHead(result.statusCode, result.headers);
                  res.end(result.body);
                }
                return;
              }
              const delay = getSmartRetryDelay(attempt, errorInfo);
              log(`  ⚠ 上游错误 HTTP ${result.statusCode} [${errorInfo.reason}] (attempt ${attempt}/${MAX_RETRIES}, wait ${delay}ms)`);
              if (attempt < MAX_RETRIES) {
                await sleep(delay);
                continue;
              }
              log(`  ✗ 达到最大重试次数，返回上游错误`);
              if (!res.writableEnded) {
                res.writeHead(result.statusCode, result.headers);
                res.end(result.body);
              }
              return;
            }

            let responseBody;
            try {
              responseBody = JSON.parse(result.body);
            } catch {
              log(`  ✗ 响应解析失败，直接转发 (attempt ${attempt})`);
              if (!res.writableEnded) {
                res.writeHead(result.statusCode, result.headers);
                res.end(result.body);
              }
              return;
            }

            // 提取响应文本内容（兼容 OpenAI / 讯飞 / Anthropic 格式）
            let content = '';
            const anthropicContent = responseBody?.content;
            if (Array.isArray(anthropicContent)) {
              // Anthropic Messages API 格式: content=[{type:"text",text:"..."},{type:"tool_use",...}]
              for (const block of anthropicContent) {
                if (block.type === 'text' && block.text) content += block.text;
              }
            } else {
              // OpenAI / 讯飞格式
              content = responseBody?.choices?.[0]?.message?.content || responseBody?.data?.text || responseBody?.result?.text || '';
            }
            // Anthropic 格式下，即使没有 text block，有 tool_use block 也算有效响应
            const hasToolUse = Array.isArray(anthropicContent) && anthropicContent.some(b => b.type === 'tool_use');
            const isEmpty = content.length === 0 && !hasToolUse;
            const check = checkRefusalPrefix(content, REFUSAL_PREFIX_TREE);
            if (check === 2) {
              log(`  ⚠ 检测到拒绝响应 (attempt ${attempt}/${MAX_RETRIES}): "${content.slice(0, 60)}..."`);
              if (attempt < MAX_RETRIES) {
                await sleep(getRetryDelay(attempt));
                continue;
              }
              log(`  ✗ 达到最大重试次数，返回拒绝响应`);
            } else if (isEmpty) {
              log(`  ⚠ 空响应 (attempt ${attempt}/${MAX_RETRIES}), body=${result.body.slice(0, 200)}`);
              if (attempt < MAX_RETRIES) {
                await sleep(getRetryDelay(attempt));
                continue;
              }
              log(`  ✗ 达到最大重试次数，返回空响应`);
            } else {
              log(`  ✓ 正常响应 (attempt ${attempt}, ${content.length} chars${hasToolUse ? ', +tool_use' : ''})`);
              markOk(model);
            }

            if (!res.writableEnded) {
              res.writeHead(result.statusCode, result.headers);
              res.end(result.body);
            }
            return;

          } else {
            // ---- 流式（激进优化）----
            if (streamHeadersSent) {
              log(`  ✗ 流式 headers 已发送，无法重试，中断连接`);
              if (!res.writableEnded) res.destroy();
              return;
            }

            const result = await forwardStreamAggressive(reqBody, fwdHeaders, req.url, req.method, res, signal);
            streamHeadersSent = result.headersSent;

            // 上游 HTTP 错误 — 分类判断是否可重试
            if (result.upstreamError) {
              const errorInfo = parseUpstreamError(result.statusCode, result.fullContent);
              if (result.statusCode === 503) markBusy(model);
              if (errorInfo.reason === 'rate_limited') markRateLimited(model, getSmartRetryDelay(attempt, errorInfo));
              if (!errorInfo.retryable) {
                log(`  ✗ 上游 HTTP ${result.statusCode} [${errorInfo.reason}] 不可重试，直接返回`);
                if (!res.writableEnded) {
                  res.writeHead(result.statusCode);
                  res.end(result.fullContent);
                }
                return;
              }
              const delay = getSmartRetryDelay(attempt, errorInfo);
              log(`  ⚠ 上游错误 HTTP ${result.statusCode} [${errorInfo.reason}] (attempt ${attempt}/${MAX_RETRIES}, wait ${delay}ms)`);
              if (attempt < MAX_RETRIES) {
                await sleep(delay);
                continue;
              }
              log(`  ✗ 达到最大重试次数，返回上游错误`);
              if (!res.writableEnded) {
                res.writeHead(result.statusCode);
                res.end(result.fullContent);
              }
              return;
            }

            if (result.refused) {
              if (result.headersSent) {
                if (!res.writableEnded) res.destroy();
                log(`  ⚠ 晚期拒绝 (已发部分数据, 中断重试 attempt ${attempt}/${MAX_RETRIES})`);
                return;
              } else {
                log(`  ⚠ 早期拒绝 (attempt ${attempt}/${MAX_RETRIES}): "${result.fullContent.slice(0, 60)}..."`);
              }

              if (attempt < MAX_RETRIES) {
                await sleep(getRetryDelay(attempt));
                continue;
              }

              log(`  ✗ 达到最大重试次数，返回空响应`);
              if (!res.writableEnded) {
                const outHeaders = { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' };
                res.writeHead(200, outHeaders);
                res.write('data: [DONE]\n\n');
                res.end();
              }
              return;
            }

            // 正常完成
            if (!result.hasValidData && result.fullContent.length === 0 && !result.refused) {
              log(`  ⚠ 流式空响应 (attempt ${attempt}/${MAX_RETRIES})`);
              if (attempt < MAX_RETRIES && !streamHeadersSent) {
                await sleep(getRetryDelay(attempt));
                continue;
              }
              log(`  ✗ 流式空响应，无法重试（headers ${streamHeadersSent ? '已发' : '未发'}）`);
            } else {
              log(`  ✓ 正常响应 (attempt ${attempt}, ${result.fullContent.length} chars)`);
              markOk(model);
            }
            if (!res.writableEnded) res.end();
            return;
          }

        } catch (err) {
          // 客户端主动断开，静默处理
          if (err.message === 'aborted' || signal.aborted) {
            log(`  ⊘ 请求因客户端断开而中止`);
            return;
          }

          log(`  ✗ 请求失败 (attempt ${attempt}/${MAX_RETRIES}): ${err.message}`);

          if (isStream && streamHeadersSent) {
            log(`  ✗ 流式 headers 已发送，无法重试，中断连接`);
            if (!res.writableEnded) res.destroy();
            return;
          }

          if (attempt < MAX_RETRIES) {
            await sleep(getRetryDelay(attempt));
            continue;
          }
          if (!res.writableEnded) {
            res.writeHead(502);
            res.end(JSON.stringify({ error: `Proxy error after ${MAX_RETRIES} retries: ${err.message}` }));
          }
          return;
        }
      }
    } finally {
      requestComplete = true;
    }
  });
}

// ============ 启动服务器 ============
const server = http.createServer(handleRequest);

// 每条新连接立即禁用 Nagle，防止 SSE 小 chunk 被 TCP 攒包导致客户端假死
server.on('connection', (socket) => {
  socket.setNoDelay(true);
});

// 宽松超时：5 分钟（流式长请求需要）
server.timeout = 300000;
server.headersTimeout = 60000;
server.requestTimeout = 300000;
server.keepAliveTimeout = 65000;

server.listen(PROXY_PORT, '127.0.0.1', () => {
  log('========================================');
  log(`GLM Refusal Proxy 已启动 (高并发稳定版)`);
  log(`监听: http://127.0.0.1:${PROXY_PORT}`);
  log(`上游: ${UPSTREAM_BASE}`);
  log(`最大重试: ${MAX_RETRIES} 次`);
  log(`退避表: 拒绝=${RETRY_DELAYS.join('→')}ms | 限流=1s→2m | 500=0.5s→1m`);
  log(`错误分类: 429鉴权失败=不重试 | 429限流=长退避 | 500/502/503=中退避 | 400/401/403=不重试`);
  log(`拒绝模式: ${REFUSAL_PATTERNS.length} 个 (前缀树检测)`);
  log(`连接池: maxSockets=100, maxFreeSockets=20, lifo`);
  log(`超时: 300s (请求), 65s (keep-alive)`);
  log('========================================');
  log('');
  log('请在 settings.json 中设置:');
  log(`  "ANTHROPIC_BASE_URL": "http://127.0.0.1:${PROXY_PORT}/anthropic"`);
  log('');
  log('等待请求...');
});
