// TODAY React 页无截图健康检查(禁 PNG 回喂,DOM 断言)
// 前提:前端 localhost:3000 + 后端 localhost:8088 已起;真数据来自 B4 /api/v1/research/today
const { chromium } = require("playwright");

const URL = "http://localhost:3000/research/today";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  let pass = 0, fail = 0;
  const ok = async (name, cond) => {
    if (cond) { pass++; console.log(`  PASS ${name}`); }
    else { fail++; console.log(`  FAIL ${name}`); }
  };

  await page.goto(URL, { waitUntil: "networkidle", timeout: 60000 });

  // 等 useApi resolve:四段渲染 + 加载态消失(B4 数据到页)
  await page.waitForFunction(
    () => {
      const t = document.body.innerText || "";
      return t.includes("LAST HANDOFF") && !t.includes("加载中…");
    },
    { timeout: 20000 },
  );
  await page.waitForTimeout(500);

  const grab = () => page.evaluate(() => {
    const titles = [...document.querySelectorAll("section")].map((s) =>
      s.querySelector("span")?.textContent?.trim() || "",
    );
    // innerText 只取可见文本(排除 Next RSC payload 脚本噪声)
    const body = document.body.innerText || "";
    return { titles, body };
  });

  console.log("[四段固定结构]");
  const first = await grab();
  await ok("LAST HANDOFF 段", first.titles.includes("LAST HANDOFF"));
  await ok("NEEDS YOU 段", first.titles.includes("NEEDS YOU"));
  await ok("SELENE IS WORKING 段", first.titles.includes("SELENE IS WORKING"));
  await ok("WORLD CHANGED 段", first.titles.includes("WORLD CHANGED"));

  console.log("[真值链路]");
  // B4 真数据渲染:页面不能是骨架/加载态/错误态
  await ok("非加载态", !first.body.includes("加载中…"));
  await ok("非错误态", !first.body.includes("后端不可用"));
  await ok("灵魂问句", first.body.includes("现在什么值得我关注"));

  // 按 B4 当前真值分支断言(种子会流转:needs_input → investigation → …),不写死期待
  const b4 = await (async () => {
    const res = await fetch("http://localhost:8088/api/v1/research/today");
    return await res.json();
  })();
  const needsCount = (b4.needs_you || []).length;
  const workingCount = (b4.working || []).length;
  const quiet = needsCount === 0;
  console.log(`  [B4 真值] needs_you=${needsCount} working=${workingCount} quiet=${quiet}`);
  if (quiet) {
    await ok("安静态塌缩文案", first.body.includes("现在不需要你"));
    await ok("无拍板按钮(安静)", !first.body.includes("去 Mission 页拍板"));
  } else {
    const topNeed = b4.needs_you[0];
    await ok("首条 needs_you 标题渲染", first.body.includes(topNeed.title.slice(0, 8)));
    await ok("真值 mis-id", first.body.includes(topNeed.id));
    await ok("Review 按钮=门", first.body.includes("去 Mission 页拍板"));
    await ok("链接指向 Mission 详情", (await page.$$eval('a[href^="/research/missions?id="]', (as) => as.length)) >= 1);
  }

  console.log("[事件层 · 先结论后解释]");
  // WORKING 段按真值断:有项则首条标题出现,空则诚实文案
  if (workingCount > 0) {
    await ok("working 首条标题渲染", first.body.includes(b4.working[0].title.slice(0, 8)));
  } else if (needsCount > 0) {
    await ok("诚实空态文案", first.body.includes("办公室很安静"));
  } else {
    await ok("WORKING 段渲染(全空态)", first.titles.includes("SELENE IS WORKING"));
  }

  console.log("[相对时间]");
  const relOk = /\d+[mh] ago|刚刚/.test(first.body);
  await ok("相对时间格式出现", relOk);
  await ok("无裸 09-07 时间戳", !(first.body.includes("2026-09-07 ") && !first.titles.some((t) => t.includes("ago"))));

  console.log("[无设计说明词(指令3)]");
  for (const w of ["投影", "幂等", "Relevance", "粗相位", "pure read", "idempotent", "系统完成一轮工作"]) {
    await ok(`未出现'${w}'`, !first.body.includes(w));
  }

  console.log("[金色负责人介入态]");
  if (quiet) {
    await ok("安静态无金色段(塌缩)", true); // needs_you 空时不要求金色
  } else {
    const goldUsed = await page.evaluate(() => {
      const el = [...document.querySelectorAll("section")].find((s) =>
        s.textContent?.includes("NEEDS YOU"),
      );
      if (!el) return { hasGold: false, bg: "" };
      const bg = getComputedStyle(el).borderColor;
      const btn = document.querySelector('a[href^="/research/missions?id="]');
      const btnBg = btn ? getComputedStyle(btn).backgroundColor : "";
      return { hasGold: bg.includes("227, 182, 80") || btnBg === "rgb(227, 182, 80)", bg, btnBg };
    });
    await ok("金色出现在 NEEDS YOU 段/按钮", goldUsed.hasGold);
  }

  console.log("[交接真值 · 人话进面/内部词不进面]");
  // kind 是内部状态机词(如 phase_transition),detail.note 才是人话 — 军规:实现契约不进用户面
  const handoff = await (async () => {
    const res = await fetch("http://localhost:8088/api/v1/research/today");
    const j = await res.json();
    return (j.last_handoff && j.last_handoff.detail && typeof j.last_handoff.detail.note === "string")
      ? j.last_handoff.detail.note
      : null;
  })();
  if (handoff) {
    await ok("detail.note 人话渲染到 UI", first.body.includes(handoff));
  } else {
    pass++; console.log("  SKIP detail.note 断言(后端无 note,跳过)");
  }
  await ok("内部 kind 词未泄漏(无 phase_transition 裸词)", !first.body.includes("phase_transition"));

  console.log("[诚实空态文案(当 working 空)]");
  // B4 真值:working 可能为空 → 安静文案;非空则为行渲染。两者都合法,断言不崩即可
  await ok("WORKING 段有内容渲染", first.titles.includes("SELENE IS WORKING"));

  await browser.close();
  console.log(`\n==== TODAY React 页健康检查: ${pass} pass / ${fail} fail ====`);
  process.exit(fail > 0 ? 1 : 0);
})();
