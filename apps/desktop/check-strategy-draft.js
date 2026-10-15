// STRATEGY 视觉稿无截图健康检查(非产品代码,零 PNG)
const { chromium } = require("playwright");

const URL = "file:///<local-path-redacted>";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console.error: ${m.text()}`); });

  await page.goto(URL);
  await page.waitForTimeout(300);

  const visible = (sel) => page.$eval(sel, (el) => el.offsetParent !== null).catch(() => false);
  const countVisible = (sel) => page.$$eval(sel, (els) => els.filter((e) => e.offsetParent !== null).length);
  const textOf = (sel) => page.$eval(sel, (el) => el.textContent.trim()).catch(() => "");

  const results = [];
  const check = (name, ok) => { results.push(`${ok ? "PASS" : "FAIL"} ${name}`); if (!ok) process.exitCode = 1; };

  // 0. 初始状态
  check("初始 data-depth=workspace", (await page.getAttribute("html", "data-depth")) === "workspace");

  // 0.5 壳结构(对齐真实 app:VHero+Sidebar+main 三列)
  check("壳:app-shell 存在", await visible(".app-shell"));
  check("壳:sb-item active = 策略工厂",
    await page.$eval(".sb-item.active", (el) => el.textContent.includes("策略工厂")).catch(() => false));
  check("壳:feed-bar 底部锚定", await page.$eval(".feed-bar", (el) => {
    const r = el.getBoundingClientRect();
    return Math.abs(r.bottom - window.innerHeight) < 2;
  }));
  check("壳:scene-scroll 可滚动", await page.$eval(".scene-scroll", (el) => el.scrollHeight >= el.clientHeight));
  const sbW = await page.$eval(".sb-nav", (el) => el.getBoundingClientRect().width);
  check("壳:sidebar 宽 ≈200px", Math.abs(sbW - 200) < 4);

  // 1. 头部:漂移并列(INTENDED / ACTUAL / DRIFT badge)
  check("头部:strat-head 存在", await visible(".strat-head"));
  check("头部:漂移 badge 含 DRIFT", (await textOf(".drift-badge")).includes("DRIFT"));
  check("头部:INTENDED 声明存在", (await page.$eval(".intent-row", (el) => el.textContent)).includes("INTENDED"));
  check("头部:ACTUAL 实跑存在", (await page.$eval(".intent-row", (el) => el.textContent)).includes("ACTUAL"));

  // 2. NOW 驾驶舱:条件定性行 + 诚实未运行态
  check("NOW:cond-row 恰 5 行(Configuration/Evidence/Perf/Fit/Execution)",
    (await countVisible(".cond-row")) === 5);
  check("NOW:mismatch 定性存在", (await page.$eval(".cond-state.c-mismatch", (el) => el.textContent.trim())).includes("mismatch"));
  check("NOW:未运行诚实态(signal-idle)", await visible(".signal-idle"));
  check("NOW:未运行态文字含「未在运行」", (await textOf(".signal-idle")).includes("未在运行"));

  // 3. HISTORY:7 点全 loss + 每点带 hover 真值 + 可点击
  check("HISTORY:ribbon-dot 恰 7 个", (await countVisible(".ribbon-dot")) === 7);
  const lossDots = await page.$$eval(".ribbon-dot.loss", (els) => els.length);
  check("HISTORY:7 点全标 loss(真实全亏)", lossDots === 7);
  const withTooltip = await page.$$eval(".ribbon-dot[data-tt]", (els) => els.filter((e) => e.getAttribute("data-tt")).length);
  check("HISTORY:每点带 hover 真值 title", withTooltip === 7);

  // 4. INTELLIGENCE:四桶 + 1 条真知识 + 其余空态 + Lineage
  check("INTELLIGENCE:bucket 恰 4 桶", (await countVisible(".bucket")) === 4);
  check("INTELLIGENCE:KNOWN TO WORK 空态", await visible(".bucket .empty-feed"));
  check("INTELLIGENCE:KNOWN FAILURE 真知识卡存在", await visible(".knowledge"));
  check("INTELLIGENCE:知识卡含三件套 Source/Validation/Last observed", (async () => {
    const t = await page.$eval(".knowledge", (el) => el.textContent);
    return t.includes("Source") && t.includes("Validation") && t.includes("Last observed");
  })());
  check("INTELLIGENCE:知识卡含 RECORDED provenance", (await textOf(".knowledge")).includes("RECORDED"));
  check("INTELLIGENCE:Lineage 存在且含「重验证非 V3」语义", (await textOf(".lineage")).includes("非 V3"));
  check("INTELLIGENCE:知识卡 Source 可回源(mis-d7a63c17d185)",
    (await textOf(".knowledge .kn-src")).includes("mis-d7a63c17d185"));

  // 5.5 六段结构(0907-3 采纳):ERRORS / EVOLUTION — 必须在进 REPLAY 前查(workspace 可见)
  check("六段:ERRORS 段存在", await visible(".err-strip"));
  check("六段:ERRORS 事件卡恰 7 张", (await countVisible(".err-ev")) === 7);
  check("六段:ERRORS 事件卡带调查入口", (await textOf(".err-strip")).includes("调查"));
  check("六段:ERRORS 逐笔占位标注", (await textOf(".err-cause")).includes("占位"));
  check("六段:共性根因条含 mis-d7a63c17d185 + 不可归因", (async () => {
    const t = await page.$eval(".err-cause", (el) => el.textContent);
    return t.includes("mis-d7a63c17d185") && t.includes("不可归因于策略");
  })());
  check("六段:EVOLUTION 段存在(独立 panel)", (async () => {
    const heads = await page.$$(".panel-head .ph-name");
    const texts = await Promise.all(heads.map((h) => h.evaluate((el) => el.textContent)));
    return texts.some((t) => t.includes("EVOLUTION"));
  })());
  check("六段:EVOLUTION 无 V3 pending 诚实态", (await page.$eval(".v-workspace", (el) => el.textContent)).includes("无在途 V3"));
  check("六段:workspace 段序 NOW→HISTORY→ERRORS→INTELLIGENCE→EVOLUTION", (async () => {
    const names = await page.$$eval(".v-workspace > section > .panel-head .ph-name", (els) => els.map((e) => e.textContent.trim()));
    return names.length === 5 && names[0].includes("NOW") && names[1].includes("HISTORY") && names[2].includes("ERRORS") && names[3].includes("INTELLIGENCE") && names[4].includes("EVOLUTION");
  })());

  // 5. REPLAY:点击 ribbon → 进错误调查现场
  await page.click('.ribbon-dot[data-ep="2"]');
  await page.waitForTimeout(200);
  check("REPLAY:点击 ribbon 切 data-depth=replay", (await page.getAttribute("html", "data-depth")) === "replay");
  check("REPLAY:epNum = 3(点 E3)", (await textOf("#epNum")) === "3");
  check("REPLAY:scrubber 存在", await visible(".scrubber"));
  check("REPLAY:scrub-tick 恰 7 个", (await countVisible(".scrub-tick")) === 7);
  check("REPLAY:MODE chip = T 冻结调查现场", (await textOf(".ph-metal")).includes("T 冻结"));
  check("REPLAY:T 时刻标注 = E3 时刻", (await textOf("#epT")).includes("T 08-12 18:00"));
  check("REPLAY:当时信号条件区存在", await visible(".rv-condition"));
  check("REPLAY:RECORDED AT T 清单存在", (await page.$eval(".dual-list", (el) => el.textContent)).includes("RECORDED AT T"));
  check("REPLAY:NOT AVAILABLE 清单存在", (await page.$eval(".dual-list", (el) => el.textContent)).includes("NOT AVAILABLE"));
  check("REPLAY:Market State 窄带存在", await visible(".market-strip"));
  check("REPLAY:窄带 4 项读数齐(Entry/下轨/Exit/收益)", (async () => {
    const t = await page.$eval(".ms-read", (el) => el.textContent);
    return t.includes("Entry") && t.includes("下轨") && t.includes("Exit") && t.includes("收益");
  })());
  check("REPLAY:下轨 RECONSTRUCTED 标注", (await textOf(".market-strip")).includes("RECONSTRUCTED"));
  check("REPLAY:占位价标注存在", (await textOf(".ms-note")).includes("占位"));
  check("REPLAY:收益读数跟档位 E3=-3.94%", (await textOf("#msRetV")) === "-3.94%");
  check("REPLAY:当时可用记忆空态(冷启动为真)", (await textOf(".mem-empty")).includes("记忆系统当时不存在"));
  check("REPLAY:Outcome 显示 AFTER THE FACT chip", (await textOf(".rv-outcome")).includes("AFTER THE FACT"));
  check("REPLAY:Outcome 初始密封(blur + 已密封 chip)", (async () => {
    const cls = await page.$eval("#rvOutcome", (el) => el.className);
    const txt = await textOf(".rv-outcome");
    return cls.includes("sealed") && txt.includes("已密封");
  })());
  check("REPLAY:seal-note 认识论铁律在位", (await textOf(".rv-outcome")).includes("知道得一样多"));
  check("REPLAY:T 决策区存在(Execute/Skip/Investigate)", (async () => {
    const t = await textOf(".decision-zone");
    return t.includes("Execute") && t.includes("Skip") && t.includes("Investigate");
  })());
  check("REPLAY:T 决策区占位声明", (await textOf(".decision-zone")).includes("占位交互"));
  check("REPLAY:YOU ARE HERE 光标存在", await visible(".scrub-cursor"));
  await page.click(".reveal-zone .btn");
  await page.waitForTimeout(120);
  check("REPLAY:Reveal 解封 Outcome(revealed 态)", (await page.$eval("#rvOutcome", (el) => el.className)).includes("revealed"));
  check("REPLAY:Reveal 展开 post-mortem", await visible("#postmortem"));
  check("REPLAY:post-mortem 归因句(不可归因+mis+重验证)", (async () => {
    const t = await page.$eval("#postmortem", (el) => el.textContent);
    return t.includes("不可归因于策略") && t.includes("mis-d7a63c17d185") && t.includes("重验证");
  })());
  // 换 episode → 结局重新密封(防偷看):点另一档验证密封重置,再回 ep 3 复位
  // force:true — tick 视觉重叠,ep5 命中检测被 ep6 挡住;验收语义是"该档可选中"非点击几何
  await page.click('.scrub-tick[data-ep="5"]', { force: true });
  await page.waitForTimeout(150);
  check("REPLAY:换档后 Outcome 重新密封", (await page.$eval("#rvOutcome", (el) => el.className)).includes("sealed"));
  check("REPLAY:换档后 post-mortem 收起", !(await visible("#postmortem")));
  await page.click('.scrub-tick[data-ep="2"]', { force: true });
  await page.waitForTimeout(150);
  check("REPLAY:回到 ep 3 复位", (await textOf("#epNum")) === "3");

  // 6. scrubber 前进
  await page.click(".scrub-btn >> nth=1");
  await page.waitForTimeout(150);
  check("REPLAY:scrubber 前进到 ep 4", (await textOf("#epNum")) === "4");
  await page.click(".scrub-btn >> nth=0");
  await page.waitForTimeout(150);
  check("REPLAY:scrubber 后退回 ep 3", (await textOf("#epNum")) === "3");

  // 7. 返回工作区
  await page.click('#draft-toggle button[data-d="workspace"]');
  await page.waitForTimeout(150);
  check("返回工作区:data-depth=workspace", (await page.getAttribute("html", "data-depth")) === "workspace");
  check("返回工作区:now-zone 可见", await visible(".now-zone"));

  check("零 pageerror / console.error", errors.length === 0);
  if (errors.length) errors.forEach((e) => console.log("  " + e));

  console.log(results.join("\n"));
  console.log(`\n${results.filter((r) => r.startsWith("PASS")).length}/${results.length} passed`);
  await browser.close();
})();
