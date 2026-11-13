// Build first. CHROME_PATH can override the installed Chromium executable.
// Uses a local mock API, never a real database or model service.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, access, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
let executable
for (const candidate of [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].filter(Boolean)) {
  try { await access(candidate); executable = candidate; break } catch { /* try next */ }
}
assert.ok(executable, 'Set CHROME_PATH to an installed Chromium executable')
const connection = {id:'browser-demo',name:'Browser test',dialect:'sqlite',has_password:false,options:{}}
const metrics = [], templates = [], queries = [], runs = [], errors = []
let statusCalls = 0
const server = createServer(async (req,res) => {
  try {
    const pathname = new URL(req.url, 'http://localhost').pathname
    if(pathname.startsWith('/api/')) {
      let raw = ''; for await (const chunk of req) raw += chunk
      const body = raw ? JSON.parse(raw) : {}
      let data = {}
      if(pathname === '/api/auth/status') data = {required:false}
      else if(pathname === '/api/settings') data = {llm_configured:true}
      else if(pathname === '/api/connections') data = [connection]
      else if(pathname.endsWith('/workspace/glossary')) data = {tables:[]}
      else if(pathname.endsWith('/metrics')) {
        if(req.method === 'POST') {data={...body,id:'metric-1'}; metrics.push(data)}
        else data={items:metrics,can_edit:true}
      } else if(pathname.endsWith('/templates')) {
        if(req.method === 'POST') {data={...body,id:'template-1'}; templates.push(data)}
        else data={items:templates}
      } else if(pathname.includes('/templates/') && pathname.endsWith('/run')) {
        runs.push(body); data={sql:'SELECT id FROM users LIMIT 10',tables:['users'],rows:[{id:1}],row_count:1,columns:['id'],explanation:'按已确认口径查询'}
      } else if(pathname.includes('/mutate/status/')) {
        statusCalls++; data={preview_id:'preview-demo',status:statusCalls === 1 ? 'executing':'executed',executed_at:statusCalls === 1 ? null:'2026-11-13T00:00:00Z'}
      } else if(pathname.endsWith('/query/nl')) {
        queries.push(body)
        data=queries.length === 1 ? {sql:'',rows:[],row_count:0,clarification:{question:'请选择时间范围',options:['最近7天','最近30天']},clarification_prompt:body.prompt} : {sql:'SELECT id FROM users LIMIT 10',rows:[{id:1}],row_count:1,columns:['id'],explanation:'采用已确认的时间范围'}
      }
      res.writeHead(200, {'content-type':'application/json'}); res.end(JSON.stringify(data)); return
    }
    const asset = path.resolve(root,'dist',pathname === '/' ? 'index.html' : pathname.slice(1))
    assert.ok(asset.startsWith(path.resolve(root,'dist') + path.sep))
    const content = await readFile(asset)
    res.writeHead(200, {'content-type':asset.endsWith('.js') ? 'text/javascript' : asset.endsWith('.css') ? 'text/css' : 'text/html'}); res.end(content)
  } catch(e) {res.writeHead(500); res.end(String(e))}
})
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve))
const origin = `http://127.0.0.1:${server.address().port}`
const profile = await mkdtemp(path.join(tmpdir(),'naturecrud-browser-'))
const browser = spawn(executable, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'], {windowsHide:true,stdio:'ignore'})
let ws
console.log('Browser process', browser.pid)
try {
  let port
  for(let i=0;i<100;i++) {try {port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0].trim(); break} catch {await pause(100)}}
  assert.ok(port,'Browser did not expose debugging port')
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`, {signal: AbortSignal.timeout(5000)})).json()
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl)
  await new Promise((resolve,reject) => {const t=setTimeout(()=>reject(new Error('WebSocket connection timeout')),5000);ws.onopen=()=>{clearTimeout(t);resolve()}; ws.onerror=reject})
  console.log('Connected to browser')
  let seq=0; const pending = new Map()
  ws.onmessage = event => {
    const message=JSON.parse(event.data)
    if(message.id) {const entry=pending.get(message.id); if(entry) {pending.delete(message.id); message.error ? entry.reject(message.error) : entry.resolve(message.result)}}
    if(message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text)
  }
  const call=(method,params={}) => new Promise((resolve,reject) => {const id=++seq; const timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout: '+method))},10000); pending.set(id,{resolve:r=>{clearTimeout(timer);resolve(r)},reject:e=>{clearTimeout(timer);reject(e)}}); ws.send(JSON.stringify({id,method,params}))})
  const evaluate=async expression => {
    const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true})
    if(r.exceptionDetails) throw new Error(r.exceptionDetails.text + ': ' + r.result.description)
    return r.result.value
  }
  const waitFor=async expression => {for(let i=0;i<100;i++){if(await evaluate(expression)) return;await pause(100)}throw new Error('Timed out: '+expression)}
  const clickText=async text => {await waitFor(`Array.from(document.querySelectorAll('button,summary')).some(e=>e.textContent.trim()===${JSON.stringify(text)})`); await evaluate(`Array.from(document.querySelectorAll('button,summary')).find(e=>e.textContent.trim()===${JSON.stringify(text)}).click()`)}
  const fill=async (selector,value) => evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)throw new Error('Missing input');Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`)
  const fillLabel=async (label,value) => evaluate(`(()=>{const l=Array.from(document.querySelectorAll('label')).find(e=>e.textContent.startsWith(${JSON.stringify(label)}));const e=l.querySelector('input,textarea');Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`)
  await call('Runtime.enable'); await call('Page.enable')
  console.log('Browser domains enabled')
  const session={step:4,connection,schema:{connection_id:connection.id,dialect:'sqlite',tables:[]},selectedTables:[{table:'users'}],interpret:{connection_id:connection.id,dialect:'sqlite',selected_tables:['users'],overview:'',tables:[],relationships:[],join_hints:[],warnings:[],source:'test'},chatItems:[{role:'assistant',kind:'text',text:'等待核实原操作',mutationStatusId:'preview-demo'}],mode:'query'}
  await call('Page.addScriptToEvaluateOnNewDocument',{source:`if(!sessionStorage.getItem('nlcrud.workspace.v1')) sessionStorage.setItem('nlcrud.workspace.v1',${JSON.stringify(JSON.stringify(session))});`})
  await call('Page.navigate',{url:origin})
  await waitFor("document.body.textContent.includes('指标口径库')")
  console.log('Workbench loaded')
  await clickText('指标口径库 · 0 项')
  await fillLabel('指标名称','有效用户')
  await fillLabel('数据库表名','users')
  await fillLabel('计算口径','只统计状态为正常的用户')
  await clickText('添加口径')
  await waitFor("document.body.textContent.includes('指标口径库 · 1 项')")
  console.log('Metric saved')
  assert.equal(metrics[0].definition,'只统计状态为正常的用户')
  await clickText('常用查询与参数模板 · 0 项')
  await fillLabel('收藏名称','日期模板')
  await fillLabel('查询问题','查询 {{start_date}} 之后的用户')
  await waitFor("!!document.querySelector('select[aria-label=\"start_date 类型\"]')")
  await evaluate("(()=>{const e=document.querySelector('select[aria-label=\"start_date 类型\"]');e.value='date';e.dispatchEvent(new Event('change',{bubbles:true}));})()")
  await clickText('保存收藏')
  await waitFor("document.body.textContent.includes('常用查询与参数模板 · 1 项')")
  console.log('Template saved')
  assert.equal(templates[0].parameters[0].kind,'date')
  await clickText('选择运行')
  await fill('input[type=date]','2026-11-13')
  await clickText('运行查询')
  await waitFor("document.body.textContent.includes('按已确认口径查询')")
  assert.equal(runs[0].values.start_date,'2026-11-13')
  await fill('form textarea','查看最近的用户')
  await evaluate("document.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
  await waitFor("document.body.textContent.includes('请选择时间范围')")
  await clickText('最近7天')
  await waitFor("document.body.textContent.includes('采用已确认的时间范围')")
  assert.equal(queries.length,2)
  assert.ok(queries[1].prompt.includes('查看最近的用户') && queries[1].prompt.includes('最近7天'))
  await waitFor("document.body.textContent.includes('已完成写入')")
  await call('Page.reload')
  await waitFor("document.body.textContent.includes('已完成写入') && document.body.textContent.includes('日期模板')")
  assert.deepEqual(errors,[])
  console.log('Workbench browser tests passed: metrics, typed templates, clarification, status polling and refresh restore')
  await call('Browser.close').catch(()=>{})
} finally {
  if(ws) ws.close()
  browser.kill()
  server.closeAllConnections(); await new Promise(resolve => server.close(resolve))
  // Verify containment before removing only this test's temporary profile.
  if(path.resolve(profile).startsWith(path.resolve(tmpdir()) + path.sep) && path.basename(profile).startsWith('naturecrud-browser-')) {
    await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:200}).catch(()=>{})
  }
}
