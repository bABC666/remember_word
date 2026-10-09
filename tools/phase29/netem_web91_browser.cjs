/* Live desktop/mobile checks for all91 online supplements on an isolated API. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const url='http://127.0.0.1:5198';
const credentials=JSON.parse(fs.readFileSync(process.env.NETEM_RICH_QA_CREDENTIALS,'utf8'));
const plan=JSON.parse(fs.readFileSync(process.env.NETEM_WEB91_PLAN,'utf8'));
const targets=plan.records.filter(r=>r.changes.online_values_added);
assert.equal(targets.length,91);
const report={target:url,checks:[],errors:[],failedResponses:[],screenshots:[],
  browser:'Browser plugin unavailable; bundled Playwright and installed Chrome'};
const check=(name,value)=>{assert(value,name);report.checks.push(name);};
const navigate=async(page,target)=>page.evaluate(location=>{
  history.pushState(null,'',location);dispatchEvent(new PopStateEvent('popstate'));
},target);
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROME_EXE,headless:true});
  try{
    for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
      const device=viewport.width>500?'desktop':'mobile';
      const context=await browser.newContext({viewport});const page=await context.newPage();
      page.on('pageerror',e=>report.errors.push(e.message));
      page.on('response',r=>{if(r.status()>=400&&r.status()!==401&&r.url()!==url+'/favicon.ico')report.failedResponses.push({url:r.url(),status:r.status()});});
      await page.goto(url);
      await page.getByLabel('用户名',{exact:true}).fill(credentials.username);
      await page.getByLabel('密码',{exact:true}).fill(credentials.password);
      await page.getByRole('button',{name:'登录',exact:true}).click();
      await page.getByRole('link',{name:'我的词库',exact:true}).waitFor();
      if((await (await context.request.get(url+'/api/settings/onboarding')).json()).seen===false){
        await page.getByRole('button',{name:'开始使用',exact:true}).click();
        await page.getByRole('dialog',{name:'欢迎使用拾词'}).waitFor({state:'hidden'});
      }
      for(const record of targets){
        await navigate(page,'/library/entry/'+record.entry_id);
        await page.getByRole('heading',{name:record.word,exact:true}).waitFor();
        const region=page.getByRole('region',{name:'来源义项'});const text=await region.innerText();
        const selected=record.payload.senses.filter(s=>s.quality_review?.kind==='automated_online_source_semantic_comparison');
        check(device+' '+record.word+' usable original-bound meaning',selected.some(s=>text.includes(s.text))&&!text.includes('暂缺可用'));
        check(device+' '+record.word+' no blocking audit gate',text.includes('自动来源核验')&&!text.includes('项待核'));
        check(device+' '+record.word+' source POS shown',[...new Set(selected.map(s=>s.pos_label))].every(label=>text.includes(label)));
        check(device+' '+record.word+' evidence collapsed',!await region.locator('.dictionary-extraction-evidence').getAttribute('open'));
        check(device+' '+record.word+' bottom attribution collapsed',!await page.locator('.word-source-explanation').getAttribute('open'));
        check(device+' '+record.word+' responsive width',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        if(selected.some(s=>s.meaning_kind==='derived'))check(device+' '+record.word+' derived identity visible',text.includes('据英文来源翻译整理'));
        if(record.word==='dioxide')check(device+' dioxide not carbon dioxide',text.includes('二氧化物')&&!text.includes('二氧化碳'));
        if(record.word==='snobbish')check(device+' snobbish strawberry withheld',text.includes('势利')&&!text.includes('草莓'));
        if(record.word==='him')check(device+' him object not possessive',text.includes('宾格')&&!text.includes('他的'));
        if(record.word==='lease')check(device+' lease roles kept separate',text.includes('出租')&&text.includes('承租'));
        if(record.word==='odds')check(device+' odds ratio preserved',text.includes('概率之比'));
        if(record.word==='nation')check(device+' nation first state meaning not proscribed',!text.includes('不推荐'));
        if(record.word==='reveal')check(device+' reveal narrative scope preserved',text.includes('影视或故事中'));
        if(record.word==='fetch')check(device+' fetch figurative is additional scope',text.includes('亦可用于比喻'));
        if(record.word==='double')check(device+' double per-POS ceiling not whole-word ceiling',await region.locator('li:visible').count()>=10);
        if(['certify','dioxide','double','lease','nation','reveal'].includes(record.word)){
          const file=path.join(process.env.NETEM_RICH_QA_SHOTS,'web91-'+device+'-'+record.word+'.png');
          await page.screenshot({path:file,fullPage:false});report.screenshots.push(file);
        }
      }
      await navigate(page,'/study?lexicon_id='+credentials.lexicon_id);
      await page.getByRole('button',{name:/显示答案/}).waitFor();
      await page.getByRole('button',{name:/显示答案/}).click();
      const answer=page.getByRole('region',{name:'来源义项'});await answer.waitFor();
      check(device+' study source meanings usable',(await answer.innerText()).includes('自动来源核验'));
      check(device+' study evidence remains folded',!await answer.locator('.dictionary-extraction-evidence').getAttribute('open'));
      await context.close();
    }
  }finally{
    await browser.close();fs.writeFileSync(process.env.NETEM_RICH_QA_REPORT,JSON.stringify(report,null,2)+'\n');
  }
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.failedResponses,[]);
  console.log(JSON.stringify({checks:report.checks.length,errors:report.errors,screenshots:report.screenshots.length}));
})().catch(e=>{console.error(e);process.exitCode=1;});
