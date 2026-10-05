const DAILY = "20_每日记录/个人";
const WEEKLY = "21_每周记录/个人";
const MONTHLY = "22_每月记录/个人";

module.exports = async function rollup({ app, variables = {} }, settings = {}) {
  const period = settings.period;
  if (!["week", "month"].includes(period)) throw new Error("请选择周统计或月统计。");
  const active = app.workspace.getActiveFile();
  const today = new Date();
  const id = period === "week"
    ? variables.week || (active?.path.startsWith(`${WEEKLY}/`) ? active.basename : isoWeek(today))
    : variables.month || (active?.path.startsWith(`${MONTHLY}/`) ? active.basename : dateId(today).slice(0, 7));
  if (!(period === "week" ? /^\d{4}-W\d{2}$/ : /^\d{4}-\d{2}$/).test(id)) {
    throw new Error("记录日期格式不正确。");
  }
  const target = app.vault.getAbstractFileByPath(`${period === "week" ? WEEKLY : MONTHLY}/${id}.md`);
  if (!target) throw new Error("请先由 Agent 或 Obsidian 模板创建这份记录，再刷新统计。");
  const old = await app.vault.read(target);
  const marker = period === "week" ? "weekly" : "monthly";
  const start = `<!-- SN:${marker}:auto:start -->`;
  const end = `<!-- SN:${marker}:auto:end -->`;
  const first = old.indexOf(start);
  const last = old.indexOf(end, first + start.length);
  if (first < 0 || last < 0) throw new Error("记录缺少统计块，请让 Agent 按当前模板补齐。");

  const rows = [];
  const totals = new Map();
  const template = await app.vault.adapter.read("个人模板/每日记录.md");
  const definitions = [...checkboxes(template).keys()].map((name) => ({
      name: name.replace(/[:：]\s*$/, "").trim(), detail: /[:：]\s*$/.test(name),
  }));
  if (period === "week") {
    for (const { name } of definitions) totals.set(name, { done: 0, total: 0 });
    const monday = weekMonday(id);
    for (let day = 0; day < 7; day += 1) {
      const name = dateId(addDays(monday, day));
      const path = `${DAILY}/${name}.md`;
      const file = app.vault.getAbstractFileByPath(path);
      rows.push({ name, path, exists: Boolean(file) });
      if (!file) continue;
      for (const [raw, done] of checkboxes(await app.vault.read(file))) {
        const definition = definitions.find((item) => raw === item.name ||
          (item.detail && (raw.startsWith(`${item.name}：`) || raw.startsWith(`${item.name}:`))));
        if (!definition) continue;
        const name = definition.name;
        const value = totals.get(name);
        value.total += 1;
        value.done += Number(done);
        totals.set(name, value);
      }
    }
  } else {
    // 整周归属沿用现有规则：周日落在哪个月，整周就计入该月。
    const [year, month] = id.split("-").map(Number);
    for (let date = new Date(year, month - 1, 1); date.getMonth() === month - 1; date = addDays(date, 1)) {
      if (date.getDay() !== 0) continue;
      const name = isoWeek(date);
      const path = `${WEEKLY}/${name}.md`;
      const file = app.vault.getAbstractFileByPath(path);
      rows.push({ name, path, exists: Boolean(file) });
      if (!file) continue;
      for (const line of section(await app.vault.read(file), "打卡统计")) {
        const match = line.match(/^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|/);
        if (!match) continue;
        const name = match[1].trim().replace(/&#124;/g, "|");
        if (!definitions.some((item) => item.name === name)) continue;
        const value = totals.get(name) || { done: 0, total: 0 };
        value.done += Number(match[2]);
        value.total += Number(match[3]);
        totals.set(name, value);
      }
    }
  }
  const choice = period === "week" ? "统计本周打卡" : "刷新每月统计";
  const table = ["### 打卡统计", "", "| 项目 | 完成 | 应统计 | 完成率 |", "| --- | ---: | ---: | ---: |"];
  for (const [name, { done, total }] of totals) {
    table.push(`| ${name.replace(/\|/g, "&#124;")} | ${done} | ${total} | ${total ? `${Math.round(done / total * 100)}%` : "-"} |`);
  }
  const links = [`### ${period === "week" ? "每日记录" : "周记录"}`, "",
    ...rows.map(({ name, path, exists }) => exists ? `- [[${path}|${name}]]` : `- ${name}（未创建）`)];
  const body = [start, "", `[${period === "week" ? "刷新本周统计" : choice}](obsidian://quickadd?choice=${encodeURIComponent(choice)}&value-${period}=${id})`, "",
    ...(period === "week" ? [...table, "", ...links] : [...links, "", ...table]), "", end].join("\n");
  await app.vault.modify(target, old.slice(0, first) + body + old.slice(last + end.length));
  await app.workspace.getLeaf(false).openFile(target);
};

function section(text, title) {
  const lines = text.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === `### ${title}`);
  if (start < 0) return [];
  const result = [];
  for (const line of lines.slice(start + 1)) {
    if (/^#{1,3}\s/.test(line)) break;
    result.push(line);
  }
  return result;
}

function checkboxes(text) {
  const values = new Map();
  for (const line of section(text, "打卡")) {
    const match = line.match(/^- \[([ xX])\]\s*(.+)$/);
    if (match) values.set(match[2].trim(), match[1].toLowerCase() === "x");
  }
  return values;
}

function dateId(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function addDays(date, amount) {
  const next = new Date(date);
  next.setDate(next.getDate() + amount);
  return next;
}

function weekMonday(id) {
  const [year, week] = id.split("-W").map(Number);
  const jan4 = new Date(year, 0, 4);
  return addDays(jan4, 1 - (jan4.getDay() || 7) + (week - 1) * 7);
}

function isoWeek(date) {
  const utc = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  utc.setUTCDate(utc.getUTCDate() + 4 - (utc.getUTCDay() || 7));
  const year = utc.getUTCFullYear();
  const week = Math.ceil(((utc - new Date(Date.UTC(year, 0, 1))) / 86400000 + 1) / 7);
  return `${year}-W${String(week).padStart(2, "0")}`;
}
