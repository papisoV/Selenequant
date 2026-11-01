// TODAY 视觉稿无截图健康检查(按 [[no-screenshot-files]]:禁 PNG 回喂,DOM 断言)
const { chromium } = require("playwright");
const path = require("path");

const FILE = "file:///" + path.resolve(__dirname, "../../../scratch/drafts/today-attention-router.html").replace(/\\/g, "/");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(FILE, { waitUntil: "networkidle" });

  let pass = 0, fail = 0;
  const ok = async (name, cond) => {
    if (cond) { pass++; console.log(`  PASS ${name}`); }
    else { fail++; console.log(`  FAIL ${name}`); }
  };

  // ---- 壳层对齐 ----
  const shell = await page.evaluate(() => {
    const g = (s) => document.querySelector(s);
    return {
      vhero: !!g(".vhero"), sb: !!g(".sb-nav"), sym: !!g(".symbol-bar"),
      ctx: !!g(".ctx-bar"), scene: !!g(".scene-scroll"), feed: !!g(".feed-bar"),
    };
  });
  console.log("[shell]");
  await ok("VHero56", shell.vhero); await ok("Sidebar200", shell.sb);
  await ok("SymbolBar38", shell.sym); await ok("CtxBar22", shell.ctx);
  await ok("scene滚动区", shell.scene); await ok("FeedBar28", shell.feed);

  // ---- CtxBar 简化(v2:极简计数) ----
  const ctx = await page.evaluate(() => {
    const txt = document.querySelector(".ctx-bar")?.textContent || "";
    return { txt, cv: [...document.querySelectorAll(".ctx-bar .cv")].map((e) => e.textContent.trim()) };
  });
  console.log("[CtxBar 简化]");
  await ok("研究线=1 NEEDS YOU", ctx.cv.includes("1 NEEDS YOU"));
  await ok("无'0 条进行中'证明性文案", !ctx.txt.includes("0 条进行中"));
  await ok("无'数据截至'stale 感文案", !ctx.txt.includes("数据截至"));

  // ---- 四段固定结构 ----
  const sections = await page.evaluate(() => {
    const titles = [...document.querySelectorAll(".ds-title")].map((e) => e.textContent.trim());
    return { titles, count: titles.length };
  });
  console.log("[四段结构]");
  await ok("四段全在", sections.count === 4);
  await ok("LAST HANDOFF 段", sections.titles[0] === "LAST HANDOFF");
  await ok("NEEDS YOU 段", sections.titles[1] === "NEEDS YOU");
  await ok("SELENE IS WORKING 段", sections.titles[2] === "SELENE IS WORKING");
  await ok("WORLD CHANGED 段", sections.titles[3] === "WORLD CHANGED");

  // ---- LAST HANDOFF 三行全真 ----
  const handoff = await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".h-row")];
    return {
      count: rows.length,
      labels: rows.map((r) => r.querySelector(".h-label")?.textContent.trim()),
      body: rows.map((r) => r.querySelector(".h-what")?.textContent || "").join(" | "),
    };
  });
  console.log("[LAST HANDOFF 三行]");
  await ok("三行", handoff.count === 3);
  await ok("YOU DID/SELENE DID/CURRENT", JSON.stringify(handoff.labels) === JSON.stringify(["YOU DID", "SELENE DID", "CURRENT"]));
  await ok("真值:批准 REVALIDATE", handoff.body.includes("批准 REVALIDATE"));
  await ok("真值:OOS +0.58% 转正", handoff.body.includes("+0.58%"));
  await ok("真值:REVALIDATED 结案", handoff.body.includes("REVALIDATED"));
  await ok("真值:mis-3adeb596e736 推送", handoff.body.includes("验收种子"));

  // ---- 相对时间(v2:'Xh ago'+title 精确) ----
  const rel = await page.evaluate(() => {
    const whens = [...document.querySelectorAll(".h-when, .wc-when, .done-item .t")].map((e) => e.textContent.trim());
    const titled = document.querySelectorAll(".h-when[title], .wc-when[title], .done-item .t[title]").length;
    return { whens, titled, allRel: whens.every((t) => /^(\d+h ago|\d+d ago|刚刚)$/.test(t)) };
  });
  console.log("[相对时间]");
  await ok("全部相对时间格式(非 09-07)", rel.allRel && rel.whens.length === 6);
  await ok("hover 精确时间 title 在", rel.titled === 6);

  // ---- NEEDS YOU E1 事件层(v2:第一眼=工作事件,先结论后解释) ----
  const ev = await page.evaluate(() => ({
    flag: !!document.querySelector(".ny-event .ev-flag"),
    title: document.querySelector(".ev-title")?.textContent.trim() || "",
    state: document.querySelector(".ev-state")?.textContent.trim() || "",
    line: document.querySelector(".ev-line")?.textContent.replace(/\s+/g, " ") || "",
    btn: !!document.querySelector(".ev-go .btn-approve"),
  }));
  console.log("[NEEDS YOU 事件层]");
  await ok("事件徽标存在", ev.flag);
  await ok("事件头=问题句", ev.title.includes("配置漂移判定待裁决"));
  await ok("事件状态chip=判定已推送", ev.state === "判定已推送");
  await ok("事件结论=一句(BTC 4h vs 1h 等你裁决)", ev.line.includes("BTC 4h") && ev.line.includes("1h") && ev.line.includes("等你裁决"));
  await ok("Review Mission 按钮在事件层", ev.btn);

  // ---- NEEDS YOU E1 四元细节(第二层扫读)·金橙阶 ----
  const card = await page.evaluate(() => {
    const keys = [...document.querySelectorAll(".ny-grid .k")].map((e) => e.textContent.trim());
    const done = [...document.querySelectorAll(".done-item")];
    const body = document.body.textContent;
    const approveBg = getComputedStyle(document.querySelector(".ev-go .btn-approve")).backgroundColor;
    const heavyBorder = getComputedStyle(document.querySelector(".ds-heavy")).borderColor;
    const baseBorder = getComputedStyle(document.querySelector(".day-section")).borderColor;
    return { keys, doneCount: done.length, body, approveBg, heavyBorder, baseBorder };
  });
  console.log("[NEEDS YOU E1 四元·色阶]");
  await ok("What happened 键", card.keys.includes("What happened"));
  await ok("Why now 键", card.keys.includes("Why now"));
  await ok("Selene已做=✓列表(2条活动流真值)", card.doneCount === 2);
  await ok("决定区=门(Review Mission)", card.body.includes("去 Mission 页拍板"));
  await ok("四动作chip", ["批准 Approve", "要更多证据", "重新定向", "停放 Park"].every((t) => card.body.includes(t)));
  await ok("拍板位置caption", card.body.includes("拍板在 Mission 页完整证据面前"));
  await ok("负责人介入态=金(非系统蓝)", card.approveBg === "rgb(227, 182, 80)" && card.heavyBorder !== card.baseBorder);

  // ---- 真值禁假数字 ----
  console.log("[真值军规]");
  await ok("判定推送真值(配置漂移)", card.body.includes("配置漂移"));
  await ok("声明4h vs 实跑1h 真值", card.body.includes("4h") && card.body.includes("1h"));
  await ok("bt-b62d32ac 真值", card.body.includes("bt-b62d32ac"));

  // ---- v2 设计说明词删除(指令3:设计原则存在于行为,不在文本) ----
  const bodyWorking = await page.evaluate(() => document.body.textContent);
  console.log("[副标题设计词删除]");
  for (const w of ["系统完成一轮工作", "与我相关的变化", "Relevance", "粗相位", "0 条进行中", "数据截至", "投影", "幂等"]) {
    await ok(`未出现'${w}'`, !bodyWorking.includes(w));
  }

  // ---- SELENE IS WORKING 诚实空 ----
  console.log("[WORKING 诚实空]");
  await ok("办公室很安静文案", bodyWorking.includes("办公室很安静,因为一切在等你"));
  await ok("空态短句=1项调查在等你的决定", bodyWorking.includes("1 项调查在等你的决定"));
  await ok("机制解释句已删", !bodyWorking.includes("REVALIDATE 回测会在这里跑起来"));
  await ok("禁百分比(仅行情/OOS 真值 %)", !(bodyWorking.match(/[\d.]+%/g) || []).some((m) => !["2.4%", "1.1%", "0.58%"].includes(m)));

  // ---- WORLD CHANGED 最轻 ----
  await ok("WORLD CHANGED 结案行", bodyWorking.includes("Keltner V2 配置漂移调查结案"));

  // ---- 安静态塌缩切换 ----
  await page.click('#draft-toggle button[data-s="quiet"]');
  const quiet = await page.evaluate(() => {
    const cardVisible = [...document.querySelectorAll(".st-working")].some((e) => getComputedStyle(e).display !== "none");
    const quietVisible = [...document.querySelectorAll(".st-quiet")].some((e) => getComputedStyle(e).display !== "none");
    const needSub = document.querySelector(".ds-sub-quiet")?.textContent || "";
    return { cardVisible, quietVisible, needSub };
  });
  console.log("[安静态塌缩]");
  await ok("E1卡隐藏", !quiet.cardVisible);
  await ok("安静文案显示", quiet.quietVisible);
  await ok("NEEDS YOU 计数=0 安静", quiet.needSub.includes("0 件"));
  await ok("安静文案=现在不需要你", (await page.evaluate(() => document.body.textContent)).includes("现在不需要你"));

  // 切回
  await page.click('#draft-toggle button[data-s="working"]');

  await browser.close();
  console.log(`\n==== TODAY 视觉稿健康检查: ${pass} pass / ${fail} fail ====`);
  process.exit(fail > 0 ? 1 : 0);
})();
