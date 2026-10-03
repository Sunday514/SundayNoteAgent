module.exports = async function createDaily(params, settings = {}) {
  const { app, variables = {} } = params;
  const scope = settings.mode === "work" ? "工作" : "个人";
  const dateText = getDateFromVariables(variables) || formatDate(new Date());
  const date = parseDate(dateText);
  const week = isoWeekId(date);
  const weekday = weekdayName(date);
  const path = `20_每日记录/${scope}/${dateText}.md`;
  let file = app.vault.getAbstractFileByPath(path);
  if (!file) {
    const template = await app.vault.adapter.read(`${scope}模板/每日记录.md`);
    const content = renderTemplate(template, { dateText, week, weekday });
    await ensureFolder(app, `20_每日记录/${scope}`);
    file = await app.vault.create(path, content);
  }
  await app.workspace.getLeaf(false).openFile(file);
};

function getDateFromVariables(variables) {
  const raw = variables && variables.date ? String(variables.date).trim() : "";
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : "";
}

function parseDate(text) {
  const [year, month, day] = text.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatDate(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function isoWeekId(date) {
  const utc = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = utc.getUTCDay() || 7;
  utc.setUTCDate(utc.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((utc - yearStart) / 86400000) + 1) / 7);
  return `${utc.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

function weekdayName(date) {
  return ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"][date.getDay()];
}

function renderTemplate(template, { dateText, week, weekday }) {
  return template
    .replace(/{{DATE:YYYY-MM-DD}}/g, dateText)
    .replace(/{{DATE:gggg-\[W\]ww}}/g, week)
    .replace(/{{WEEKDAY}}/g, weekday)
    .replace(/{{DATE:dddd}}/g, weekday)
    .replace(/value-date={{DATE:YYYY-MM-DD}}/g, `value-date=${dateText}`);
}

async function ensureFolder(app, folderPath) {
  const parts = folderPath.split("/").filter(Boolean);
  let current = "";
  for (const part of parts) {
    current = current ? `${current}/${part}` : part;
    if (!app.vault.getAbstractFileByPath(current)) {
      await app.vault.createFolder(current);
    }
  }
}
