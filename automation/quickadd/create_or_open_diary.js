module.exports = async function createOrOpenDiary(params) {
  const { app, variables = {} } = params;
  const active = app.workspace.getActiveFile();
  const activeDate = active?.path.startsWith("20_每日记录/个人/") ? active.basename : "";
  const dateText = getDateFromVariables(variables) || activeDate || formatDate(new Date());
  const path = `40_个人写作/日记/${dateText}.md`;
  const file = await createIfMissing(app, path, "");
  await replaceDailyDiaryButton(app, dateText, path);
  await app.workspace.getLeaf(false).openFile(file);
};

function getDateFromVariables(variables) {
  const raw = variables && variables.date ? String(variables.date).trim() : "";
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : "";
}

function formatDate(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

async function createIfMissing(app, path, content) {
  let file = app.vault.getAbstractFileByPath(path);
  if (file) return file;
  await ensureFolder(app, path.split("/").slice(0, -1).join("/"));
  return app.vault.create(path, content);
}

async function replaceDailyDiaryButton(app, dateText, diaryPath) {
  const dailyPath = `20_每日记录/个人/${dateText}.md`;
  const dailyFile = app.vault.getAbstractFileByPath(dailyPath);
  if (!dailyFile) return;

  const diaryLink = `- [[${diaryPath.replace(/\.md$/, "")}|${dateText} 日记]]`;
  const content = await app.vault.read(dailyFile);
  if (content.includes(diaryLink)) return;

  const lines = content.split("\n");
  const index = lines.findIndex((line) => line.includes("[创建今日日记](obsidian://quickadd?choice="));
  if (index === -1) return;

  lines[index] = diaryLink;
  await app.vault.modify(dailyFile, lines.join("\n"));
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
