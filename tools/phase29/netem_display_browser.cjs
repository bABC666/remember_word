/* Actual authenticated UI checks against an explicit disposable clone only. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
const url='http://127.0.0.1:5198';
const credentials=JSON.parse(fs.readFileSync(process.env.NETEM_RICH_QA_CREDENTIALS,'utf8'));
const plan=JSON.parse(fs.readFileSync(process.env.NETEM_DISPLAY_PLAN,'utf8'));
const output=process.env.NETEM_DISPLAY_REPORT;
const shots=process.env.NETEM_DISPLAY_SHOTS;
const report={target:url,browser:'Browser plugin not available; bundled Playwright with installed Chrome',
  checks:[],errors:[],consoleErrors:[],consoleWarnings:[],failedResponses:[],screenshots:[]};
const check=(name,value)=>{assert(value,name);report.checks.push(name);};
const navigate=async(page,target)=>page.evaluate(target=>{
  history.pushState(null,'',target);dispatchEvent(new PopStateEvent('popstate'));
},target);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_EXE,headless:true});
 try{
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
   const device=viewport.width>500?'desktop':'mobile';
   const context=await browser.newContext({viewport});const page=await context.newPage();
   page.on('pageerror',e=>report.errors.push(e.message));
   page.on('console',message=>{
    if(message.type()==='error'&&!/Failed to load resource:.*401/.test(message.text())&&!message.location().url.endsWith('/favicon.ico'))report.consoleErrors.push(message.text());
    if(message.type()==='warning')report.consoleWarnings.push(message.text());
   });
   page.on('response',r=>{if(r.status()>=400&&r.status()!==401&&!r.url().endsWith('/favicon.ico'))report.failedResponses.push({status:r.status(),url:r.url()});});
   await page.goto(url);await page.getByLabel('用户名',{exact:true}).fill(credentials.username);
   await page.getByLabel('密码',{exact:true}).fill(credentials.password);
   await page.getByRole('button',{name:'登录',exact:true}).click();
   await page.getByRole('link',{name:'我的词库',exact:true}).waitFor();
   if(!(await (await context.request.get(url+'/api/settings/onboarding')).json()).seen){
    await page.getByRole('button',{name:'开始使用',exact:true}).click();
    await page.getByRole('dialog',{name:'欢迎使用拾词'}).waitFor({state:'hidden'});
   }
   const beforeBrowse=(await (await context.request.get(url+'/api/words?lexicon_id='+credentials.lexicon_id+'&limit=1')).json()).total;
   const targets=plan.records.length<=100?plan.records:plan.records.filter(r=>
    ['Easter','July','January','you','February','yellow','and','or','learn','question','lead','read','bow','wind','record','double','lease','fine','set'].includes(r.word)
     || parseInt(r.payload_sha256.slice(0,4),16)<750);
   for(const record of targets){
    await navigate(page,'/library/entry/'+record.entry_id);
    await page.getByRole('heading',{name:record.word,exact:true}).waitFor();
    const region=page.getByRole('region',{name:'来源义项'});await region.waitFor();
    const text=await region.innerText();const profile=record.payload.learning_display;
    check(device+' '+record.word+' projection renders',text.includes('自动来源核验')&&!text.includes('暂缺可用'));
    check(device+' '+record.word+' source folded',!await region.locator('.dictionary-extraction-evidence').getAttribute('open'));
    check(device+' '+record.word+' original source explanation folded',!await page.locator('.word-source-explanation').getAttribute('open'));
    const reps=profile.pronunciations.filter(p=>p.representative);
    check(device+' '+record.word+' representative context',reps.every(p=>text.includes(p.text)));
    check(device+' '+record.word+' no flattened original IPA',!text.includes('来源音标：'));
    check(device+' '+record.word+' no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    for(const group of await region.locator('.concise-group').all()){
     if(await group.isVisible())check(device+' '+record.word+' per-POS ceiling',await group.locator('li:visible').count()<=3);
    }
    if(['Easter','July','January'].includes(record.word))check(device+' '+record.word+' merged expressions',await region.locator('li:visible').count()===1);
    if(record.word==='learn')check(device+' learn distinct English uses retained',text.includes('to be studying')&&text.includes('to come to know'));
    if(record.word==='and')check(device+' and usages remain separate',await region.locator('li:visible').count()===2);
    if(record.word==='double')check(device+' double not whole-word cap',await region.locator('li:visible').count()>=10);
    if(record.word==='you')check(device+' you isolated phonemes excluded',!/(\/d\/|\/s\/|\/t\/|\/z\/)/.test(text));
    if(record.word==='February')check(device+' February fragments excluded',!/(\/j\/|\/ɹi\/|\/ɹə\.ɹi\/)/.test(text));
    if(['July','January','you','February','learn','and','double'].includes(record.word)){
     const file=path.join(shots,device+'-'+record.word+'.png');await page.screenshot({path:file,fullPage:false});report.screenshots.push(file);
    }
    if(record.word==='July'){
     const toggle=region.getByText(/^其他来源读音/);await toggle.click();
     check(device+' variant fold opens',await toggle.locator('..').getAttribute('open')!==null);
     await region.getByText('查看词典来源',{exact:true}).click();
     check(device+' evidence preserves raw forms',(await region.locator('.dictionary-extraction-evidence').innerText()).includes('7月'));
     check(device+' no artificial human confirmation',(await region.locator('.dictionary-extraction-evidence').innerText()).includes('不新增人工核心确认'));
    }
   }
   // Complete catalog pagination through the actual API, without creating progress.
   const ids=new Set();let offset=0,total=0;
   do{const response=await context.request.get(url+'/api/words?catalog=true&lexicon_id='+credentials.lexicon_id+'&limit=500&offset='+offset);
      check(device+' catalog page '+offset+' success',response.ok());const data=await response.json();
      total=data.total;for(const word of data.words)ids.add(word.lexicon_entry_id);offset+=data.words.length;
      if(!data.words.length)break;
   }while(offset<total);
   check(device+' full pagination unique5528',total===5528&&ids.size===5528);
   const afterBrowse=(await (await context.request.get(url+'/api/words?lexicon_id='+credentials.lexicon_id+'&limit=1')).json()).total;
   check(device+' browsing creates no learning states',afterBrowse===beforeBrowse);
   const unlearned=await (await context.request.get(url+'/api/words?catalog=true&lexicon_id='+credentials.lexicon_id+'&status=unstudied&limit=500')).json();
   check(device+' unlearned filter preserved',unlearned.words.length>0&&unlearned.words.every(word=>word.status==='new'));
   await navigate(page,'/settings');
   const target=page.getByLabel('每日新词目标',{exact:true});await target.waitFor();
   check(device+' daily new maximum500',await target.getAttribute('max')==='500');
   await target.fill('500');await page.getByRole('button',{name:'保存设置',exact:true}).click();
   await page.getByText('设置已保存',{exact:true}).waitFor();await page.reload();
   await page.waitForFunction(()=>document.querySelector('input[max="500"]')?.value==='500');
   check(device+' settings survive refresh',await page.getByLabel('每日新词目标',{exact:true}).inputValue()==='500');
   await navigate(page,'/study?lexicon_id='+credentials.lexicon_id);
   await page.getByRole('button',{name:/显示答案/}).waitFor();await page.getByRole('button',{name:/显示答案/}).click();
   check(device+' study renders source answer',(await page.getByRole('region',{name:'来源义项'}).innerText()).includes('自动来源核验'));
   const queue=await (await context.request.get(url+'/api/study/today?lexicon_id='+credentials.lexicon_id)).json();
   check(device+' queue remains batched',queue.words.length<=50);
   const logout=await context.request.post(url+'/api/auth/logout',{headers:{Origin:url}});
   check(device+' QA session logged out',logout.ok());
   await context.close();
  }
 }finally{await browser.close();fs.writeFileSync(output,JSON.stringify(report,null,2));}
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.failedResponses,[]);
 assert.deepEqual(report.consoleErrors,[]);
 console.log(JSON.stringify({checks:report.checks.length,errors:report.errors,screenshots:report.screenshots.length}));
})().catch(e=>{console.error(e.message);process.exitCode=1;});
