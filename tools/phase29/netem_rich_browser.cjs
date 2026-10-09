/* Real browser QA against an isolated API and dedicated Vite port. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const url = 'http://127.0.0.1:5197';
const credentials = JSON.parse(fs.readFileSync(process.env.NETEM_RICH_QA_CREDENTIALS, 'utf8'));
const shots = process.env.NETEM_RICH_QA_SHOTS;
const output = process.env.NETEM_RICH_QA_REPORT;
assert(shots && output);
const report = { browser: 'Browser plugin not available; bundled Playwright and installed Chrome',
  checks: [], screenshots: [], errors: [], failedResponses: [], consoleDetails: [], nonApplicationWarnings: [] };
const check = (name, value) => { assert(value, name); report.checks.push(name); };
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_EXE, headless: true });
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const device = viewport.width > 500 ? 'desktop' : 'mobile';
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      page.on('pageerror', error => report.errors.push(error.message));
      page.on('response', response => {
        if (response.status() >= 400 && response.status() !== 401) report.failedResponses.push({
          url: response.url(), status: response.status(), device,
        });
      });
      page.on('console', message => {
        if (message.type() === 'error' && !message.text().includes('401 (Unauthorized)')) {
          const detail = { text: message.text(), location: message.location() };
          report.consoleDetails.push(detail);
          if (detail.location.url === url + '/favicon.ico' && message.text().includes('404 (Not Found)')) {
            report.nonApplicationWarnings.push('Existing Vite favicon.ico is missing (404); no app/API resource failed');
          } else report.errors.push(message.text());
        }
      });
      await page.goto(url);
      await page.getByLabel('用户名', { exact: true }).fill(credentials.username);
      await page.getByLabel('密码', { exact: true }).fill(credentials.password);
      await page.getByRole('button', { name: '登录', exact: true }).click();
      await page.getByRole('link', { name: '我的词库', exact: true }).waitFor();
      const onboarding = page.getByRole('button', { name: '开始使用', exact: true });
      const onboardingState = await (await context.request.get(url + '/api/settings/onboarding')).json();
      if (onboardingState.seen === false) {
        await onboarding.waitFor();
        await onboarding.click();
        await page.getByRole('dialog', { name: '欢迎使用拾词' }).waitFor({ state: 'hidden' });
      }
      for (const word of ['play', 'abundant', 'above', 'April']) {
        await page.goto(`${url}/library/entry/${credentials.entry_ids[word]}`);
        await page.getByRole('heading', { name: word, exact: true }).waitFor();
        check(`${device} ${word} route identity`, page.url().includes(`/entry/${credentials.entry_ids[word]}`));
        const body = await page.locator('body').innerText();
        check(`${device} ${word} explicitly unconfirmed`, body.includes('自动抽取 · 未核实'));
        check(`${device} ${word} no core confirmation`, !body.includes('释义裁定'));
        if (word === 'play') {
          check(`${device} play noun and verb source groups`, body.includes('名词') && body.includes('动词'));
          check(`${device} play more than three source values`, await page.getByRole('region', { name: '来源义项' }).locator('li').count() >= 4);
        }
        if (word === 'abundant') {
          check(`${device} abundant located IPA`, body.includes('əˈbʌn.dn̩t'));
          check(`${device} abundant adjective definition`, body.includes('形容词') && body.includes('大量的，丰盛的'));
        }
        if (word === 'above') check(`${device} upstream sour withheld`, !body.includes('酸'));
        if (word === 'April') check(`${device} pronoun defect withheld`, !body.includes('代词'));
        const source = page.locator('details').filter({ has: page.getByText('查看来源说明', { exact: true }) }).first();
        check(`${device} ${word} bottom attribution initially collapsed`, !await source.getAttribute('open'));
        check(`${device} ${word} no horizontal overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        check(`${device} ${word} no framework overlay`, await page.locator('vite-error-overlay').count() === 0);
        if (['play', 'abundant'].includes(word)) {
          const file = path.join(shots, `netem-rich-${device}-${word}.png`);
          await page.screenshot({ path: file, fullPage: false });
          report.screenshots.push(file);
        }
        if (word === 'abundant') {
          const evidence = page.getByText(/来源与抽取依据（/);
          await evidence.click();
          check(`${device} evidence expands with original line and hash`,
            (await page.locator('body').innerText()).includes('文件 SHA-256'));
          await source.locator('summary').first().click();
          check(`${device} bottom source explanation expands`, await source.getAttribute('open') !== null);
        }
      }
      await page.goto(url + '/study');
      await page.getByRole('button', { name: /显示答案/ }).waitFor();
      check(`${device} study answer initially hidden`, await page.getByRole('region', { name: '来源义项' }).count() === 0);
      await page.getByRole('button', { name: /显示答案/ }).click();
      await page.getByRole('region', { name: '来源义项' }).waitFor();
      check(`${device} study source groups reveal`, (await page.locator('body').innerText()).includes('自动抽取 · 未核实'));
      check(`${device} study no overflow`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      const file = path.join(shots, `netem-rich-${device}-study.png`);
      await page.screenshot({ path: file, fullPage: false });
      report.screenshots.push(file);
      await context.close();
    }
    fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
    assert.deepEqual(report.errors, []);
    console.log(JSON.stringify({ checks: report.checks.length, errors: report.errors, screenshots: report.screenshots }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
