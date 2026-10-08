/* Real UI audit on explicit isolated API/Vite services. Credentials never logged. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const url = process.env.NETEM_SOURCE_AUDIT_URL || 'http://127.0.0.1:5198';
const credentials = JSON.parse(fs.readFileSync(process.env.NETEM_RICH_QA_CREDENTIALS, 'utf8'));
const shots = process.env.NETEM_RICH_QA_SHOTS;
const output = process.env.NETEM_RICH_QA_REPORT;
assert(shots && output && url === 'http://127.0.0.1:5198', 'Explicit isolated QA target required');
const report = {browser: 'Browser plugin not available; bundled Playwright and installed Chrome',
  target: url, checks: [], errors: [], failedResponses: [], screenshots: [], consoleDetails: [], nonApplicationWarnings: []};
const check = (name, value) => {assert(value, name); report.checks.push(name);};
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_EXE, headless: true});
  try {
    for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
      const device = viewport.width > 500 ? 'desktop' : 'mobile';
      const context = await browser.newContext({viewport});
      const page = await context.newPage();
      page.on('pageerror', e => report.errors.push(e.message));
      page.on('console', msg => {if (msg.type() === 'error' && !msg.text().includes('401 (Unauthorized)')) {
        const detail = {text:msg.text(),location:msg.location()};
        report.consoleDetails.push(detail);
        if (detail.location.url === url+'/favicon.ico' && msg.text().includes('404 (Not Found)'))
          report.nonApplicationWarnings.push('Existing Vite favicon.ico is absent (404); app/API resources are checked separately');
        else report.errors.push(msg.text());
      }});
      page.on('response', response => {if (response.status() >= 400 && response.status() !== 401) report.failedResponses.push({url:response.url(),status:response.status()});});
      await page.goto(url);
      await page.getByLabel('用户名', {exact:true}).fill(credentials.username);
      await page.getByLabel('密码', {exact:true}).fill(credentials.password);
      await page.getByRole('button', {name:'登录',exact:true}).click();
      await page.getByRole('link', {name:'我的词库',exact:true}).waitFor();
      const onboarding = await (await context.request.get(url+'/api/settings/onboarding')).json();
      if (onboarding.seen === false) {
        await page.getByRole('button', {name:'开始使用',exact:true}).click();
        await page.getByRole('dialog', {name:'欢迎使用拾词'}).waitFor({state:'hidden'});
      }
      const ids = {...credentials.entry_ids};
      for (const word of ['August','Bible','Christmas','December','Catholic','a','about','compass']) {
        const response = await context.request.get(url+'/api/words?catalog=true&lexicon_id='+credentials.lexicon_id+'&search='+encodeURIComponent(word));
        const entry = (await response.json()).words.find(item => item.word === word);
        assert(entry, 'target word missing: '+word);
        ids[word] = entry.lexicon_entry_id;
      }
      if (device === 'desktop') {
        await page.goto(url+'/library?search=April');
        await page.getByRole('button', {name:/April/}).first().click();
        await page.getByRole('heading', {name:'April',exact:true}).waitFor();
        check('desktop screenshot user flow has April meaning', (await page.locator('body').innerText()).includes('四月'));
      }
      for (const word of ['April','August','Bible','Christmas','December','play','set','run','abundant','above','Catholic','a','about','compass']) {
        await page.goto(url+'/library/entry/'+ids[word]);
        await page.getByRole('heading',{name:word,exact:true}).waitFor();
        const region = page.getByRole('region',{name:'来源义项'});
        const text = await region.innerText();
        const body = await page.locator('body').innerText();
        check(device+' '+word+' source audit label', text.includes('词典释义 · 自动来源核验'));
        check(device+' '+word+' no review gate in lesson', !text.includes('项待核') && !text.includes('暂无可可靠分组'));
        check(device+' '+word+' evidence folded', !await region.locator('.dictionary-extraction-evidence').getAttribute('open'));
        check(device+' '+word+' original attribution folded', !await page.locator('.word-source-explanation').getAttribute('open'));
        check(device+' '+word+' no horizontal overflow', await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        if (['April','August','Bible','Christmas','December'].includes(word)) {
          const expected = {April:'四月',August:'八月',Bible:'經',Christmas:'节',December:'十二月'};
          check(device+' '+word+' reliable meaning retained', text.includes(expected[word]) || (word==='Bible'&&text.includes('经')) || (word==='Christmas'&&text.includes('節')));
          check(device+' '+word+' bad pronoun not promoted', !text.includes('代词'));
          check(device+' '+word+' valid source IPA shown', !body.includes('暂无音标') && text.includes('来源音标'));
        }
        if (word==='play') {
          check(device+' play noun and verb', text.includes('名词')&&text.includes('动词'));
          check(device+' play music definition retained', text.includes('演奏'));
          check(device+' play not capped at three per word', await region.locator('li:visible').count()>=4);
        }
        if (word==='set') {
          check(device+' set common source values', text.includes('放置')&&text.includes('确定')&&text.includes('一套'));
          check(device+' set incorrect snippets withheld', !text.includes('但是')&&!text.includes('袖')&&!text.includes('睡觉'));
          const more = region.getByText(/更多词典释义/);
          await more.click();
          check(device+' set full source values expandable', await more.locator('..').getAttribute('open')!==null);
        }
        if (word==='run') check(device+' run shared translation retained', text.includes('跑'));
        if (word==='above') check(device+' above acid withheld', !text.includes('酸'));
        if (word==='Catholic') check(device+' Catholic unsupported legacy not restored', text.includes('暂缺可用')&&!text.includes('常用'));
        if (word==='a') check(device+' a original article POS retained', text.includes('冠词')&&!text.includes('然后'));
        if (word==='about') check(device+' about upstream bee withheld', !text.includes('蜜蜂'));
        if (word==='compass') check(device+' compass original meaning now visible', text.includes('指南针'));
        if (['April','play','set'].includes(word)) {
          const file = path.join(shots, 'audit-'+device+'-'+word+'.png');
          await page.screenshot({path:file,fullPage:false});
          report.screenshots.push(file);
        }
        if (word==='April') {
          await region.getByText('查看词典来源',{exact:true}).click();
          check(device+' original bad POS and evidence still available', (await region.innerText()).includes('pronoun'));
          check(device+' all review provenance visible', (await region.innerText()).includes('文件 SHA-256'));
        }
      }
      await page.goto(url+'/study?lexicon_id='+credentials.lexicon_id);
      await page.getByRole('button',{name:/显示答案/}).waitFor();
      check(device+' study answer hidden', await page.getByRole('region',{name:'来源义项'}).count()===0);
      await page.getByRole('button',{name:/显示答案/}).click();
      const answer = page.getByRole('region',{name:'来源义项'});
      await answer.waitFor();
      check(device+' study audited source meaning visible', (await answer.innerText()).includes('自动来源核验'));
      check(device+' study no blocking review message', !(await answer.innerText()).includes('项待核'));
      check(device+' study no overflow', await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      const file = path.join(shots,'audit-'+device+'-study.png');
      await page.screenshot({path:file,fullPage:false}); report.screenshots.push(file);
      await context.close();
    }
  } finally {
    await browser.close();
    fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
  }
  assert.deepEqual(report.errors,[]); assert.deepEqual(report.failedResponses,[]);
  console.log(JSON.stringify({checks:report.checks.length,errors:report.errors,screenshots:report.screenshots}));
})().catch(error=>{console.error(error);process.exitCode=1;});
