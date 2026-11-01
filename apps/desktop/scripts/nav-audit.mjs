import fs from "fs";
import path from "path";

const ts = fs.readFileSync("lib/page-manifest.ts", "utf8");
const chainsTs = fs.readFileSync("lib/navigation-chains.ts", "utf8");

const pageRe = /slug:\s*"([^"]+)",\s*title:\s*"([^"]+)",\s*scene:\s*"?(\w+)"?/g;
const pages = [];
let m;
while ((m = pageRe.exec(ts)) !== null) pages.push({ slug: m[1], title: m[2], scene: m[3] });

const chainRe = /steps:\s*\[([^\]]+)\]/g;
const inChains = new Set();
while ((m = chainRe.exec(chainsTs)) !== null) m[1].split(",").forEach((s) => inChains.add(s.trim().replace(/"/g, "")));

const dedupe = new Set([
  "dex-pair-chain-pair_address", "dex-tokens-token_address", "alpha-formulas-formula_id",
  "alpha-train-session_id-status", "backtests-task_id", "marketplace-listings-listing_id",
]);

function walk(d) {
  let r = [];
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.name === "node_modules") continue;
    const p = path.join(d, e.name);
    if (e.isDirectory()) r = r.concat(walk(p));
    else if (e.name === "page.tsx") r.push(p);
  }
  return r;
}

const disk = walk("app").map((p) => p.replace(/\\/g, "/").replace(/^app/, "").replace(/\/page\.tsx$/, "") || "/");

for (const sc of ["watch", "research", "trade", "system"]) {
  const all = pages.filter((p) => p.scene === sc);
  const chain = all.filter((p) => inChains.has(p.slug));
  const more = all.filter((p) => !inChains.has(p.slug) && !dedupe.has(p.slug));
  console.log(`${sc}: manifest=${all.length} chain=${chain.length} more=${more.length}`);
  console.log("  MORE: " + more.map((p) => p.slug).join(", "));
}

const manifestSlugs = new Set(pages.map((p) => p.slug));
const orphans = disk.filter((r) => {
  const slug = r.split("/")[2] || r.split("/")[1];
  return !manifestSlugs.has(slug);
});
console.log("ORPHANS(" + orphans.length + "): " + orphans.join(", "));
console.log("disk total=" + disk.length + " manifest=" + pages.length);
