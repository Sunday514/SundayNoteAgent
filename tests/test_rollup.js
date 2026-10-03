const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");

function repoText(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

const rollup = require(path.join(root, "automation/quickadd/rollup.js"));
const createDaily = require(path.join(root, "automation/quickadd/create_daily.js"));
const createDiary = require(path.join(root, "automation/quickadd/create_or_open_diary.js"));
for (const choice of JSON.parse(repoText("config/obsidian/quickadd.json")).choices) {
  assert.ok(fs.existsSync(path.join(root, choice.macro.commands[0].path.replace(/^SundayNoteAgent\//, ""))));
}

function file(pathname) {
  return {
    path: pathname,
    basename: pathname.split("/").pop().replace(/\.md$/, ""),
  };
}

function makeApp(initialFiles) {
  const files = new Map(Object.entries(initialFiles));
  const folders = new Set();
  const opened = [];

  for (const pathname of files.keys()) {
    const parts = pathname.split("/");
    for (let index = 1; index < parts.length; index += 1) {
      folders.add(parts.slice(0, index).join("/"));
    }
  }

  const app = {
    vault: {
      adapter: {
        async read(pathname) {
          if (!files.has(pathname)) throw new Error(`missing fixture file: ${pathname}`);
          return files.get(pathname);
        },
      },
      getAbstractFileByPath(pathname) {
        if (files.has(pathname)) return file(pathname);
        return folders.has(pathname) ? { path: pathname } : null;
      },
      async read(target) {
        return files.get(target.path);
      },
      async modify(target, content) {
        files.set(target.path, content);
      },
      async create(pathname, content) {
        files.set(pathname, content);
        return file(pathname);
      },
      async createFolder(pathname) {
        folders.add(pathname);
      },
    },
    workspace: {
      getActiveFile() {
        return null;
      },
      getLeaf() {
        return {
          async openFile(target) {
            opened.push(target.path);
          },
        };
      },
    },
  };

  return { app, files, opened };
}

function baseFiles() {
  return {
    "个人模板/每日记录.md": "## 计划\n\n## 记录\n\n### 打卡\n- [ ] 学习阅读：\n- [ ] 运动健身：\n\n### 日记",
    "20_每日记录/个人/2026-06-29.md": "### 打卡\n- [x] 学习阅读：书 A\n- [ ] 运动健身：跑步\n### 日记\n- [x] 不应统计",
    "20_每日记录/个人/2026-07-05.md": "### 打卡\n- [ ] 学习阅读：书 B\n- [x] 临时事项 | 户外",
    "21_每周记录/个人/2026-W27.md": "## 计划\n人工计划\n## 记录\n<!-- SN:weekly:auto:start -->\n<!-- SN:weekly:auto:end -->\n### 总结\n人工总结",
    "22_每月记录/个人/2026-07.md": "## 计划\n月计划\n## 记录\n<!-- SN:monthly:auto:start -->\n<!-- SN:monthly:auto:end -->\n### 总结\n月总结",
  };
}

async function testWeekly() {
  const fixture = makeApp(baseFiles());
  const params = { app: fixture.app, variables: { week: "2026-W27" } };
  await rollup(params, { period: "week" });
  const pathname = "21_每周记录/个人/2026-W27.md";
  const output = fixture.files.get(pathname);
  assert.match(output, /\| 学习阅读 \| 1 \| 2 \| 50% \|/);
  assert.match(output, /\| 运动健身 \| 0 \| 1 \| 0% \|/);
  assert.match(output, /\| 临时事项 &#124; 户外 \| 1 \| 1 \| 100% \|/);
  assert.match(output, /2026-06-30（未创建）/);
  assert.doesNotMatch(output, /\[\[[^\]]*2026-06-30/);
  assert.doesNotMatch(output, /不应统计/);
  assert.match(output, /人工计划/);
  assert.match(output, /人工总结/);
  await rollup(params, { period: "week" });
  assert.equal(fixture.files.get(pathname), output);
}

async function testMonth() {
  const files = baseFiles();
  for (const week of ["27", "28", "29", "30", "31"]) {
    files[`21_每周记录/个人/2026-W${week}.md`] = "### 打卡统计\n| 学习阅读 | 1 | 2 | 50% |";
  }
  const fixture = makeApp(files);
  await rollup({ app: fixture.app, variables: { month: "2026-07" } }, { period: "month" });
  const output = fixture.files.get("22_每月记录/个人/2026-07.md");
  assert.match(output, /\| 学习阅读 \| 4 \| 8 \| 50% \|/);
  for (const week of ["27", "28", "29", "30"]) assert.match(output, new RegExp(`W${week}`));
  assert.doesNotMatch(output, /W31/);
  assert.match(output, /月计划/);
  assert.match(output, /月总结/);
}

async function testNoCreationOrOverwrite() {
  const fixture = makeApp(baseFiles());
  const before = new Map(fixture.files);
  await assert.rejects(rollup({ app: fixture.app, variables: { week: "2026-W40" } }, { period: "week" }), /先.*创建/);
  fixture.files.set("21_每周记录/个人/2026-W27.md", "人工内容");
  await assert.rejects(rollup({ app: fixture.app, variables: { week: "2026-W27" } }, { period: "week" }), /缺少统计块/);
  assert.equal(fixture.files.get("21_每周记录/个人/2026-W27.md"), "人工内容");
  assert.equal(fixture.files.size, before.size);
}

async function testDailyAndDiary() {
  const fixture = makeApp({ "个人模板/每日记录.md": repoText("templates/每日记录.md") });
  await createDaily({ app: fixture.app, variables: { date: "2026-07-13" } });
  const pathname = "20_每日记录/个人/2026-07-13.md";
  const content = fixture.files.get(pathname);
  assert.match(content, /2026-W29/);
  assert.match(content, /value-date=2026-07-13/);
  assert.doesNotMatch(content, /\{\{/);
  fixture.files.set(pathname, content + "\n人工补充");
  fixture.files.delete("个人模板/每日记录.md");
  await createDaily({ app: fixture.app, variables: { date: "2026-07-13" } });
  assert.equal(fixture.files.get(pathname), content + "\n人工补充");
  await createDiary({ app: fixture.app, variables: { date: "2026-07-13" } });
  const diary = "40_个人写作/日记/2026-07-13.md";
  assert.equal(fixture.files.get(diary), "");
  assert.match(fixture.files.get(pathname), /\[\[40_个人写作\/日记\/2026-07-13/);
  fixture.files.set(diary, "日记正文");
  fixture.app.workspace.getActiveFile = () => file(pathname);
  await createDiary({ app: fixture.app });
  assert.equal(fixture.files.get(diary), "日记正文");
}

async function testWorkDaily() {
  const fixture = makeApp({ "工作模板/每日记录.md": repoText("templates/work/每日记录.md") });
  const params = { app: fixture.app, variables: { date: "2026-07-13" } };
  await createDaily(params, { mode: "work" });
  const pathname = "20_每日记录/工作/2026-07-13.md";
  const content = fixture.files.get(pathname);
  assert.match(content, /2026-W29/);
  assert.doesNotMatch(content, /个人|日记|打卡|\{\{/);
  assert.ok([...fixture.files.keys()].every((name) => !name.includes("个人")));
  fixture.files.set(pathname, "工作记录正文");
  await createDaily(params, { mode: "work" });
  assert.equal(fixture.files.get(pathname), "工作记录正文");
}

(async () => {
  await testWeekly();
  await testMonth();
  await testNoCreationOrOverwrite();
  await testDailyAndDiary();
  await testWorkDaily();
  console.log("routine fixture passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
