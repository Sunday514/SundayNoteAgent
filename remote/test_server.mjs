import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomBytes} from 'node:crypto';
import {once} from 'node:events';
const exec=promisify(execFile);

test('真实 rootless MCP：隔离、工具链、任务、取消、超时', {
  skip:!process.env.SANDBOX_TEST_IMAGE, timeout:120000,
},async t=>{
  const temp=await mkdtemp(join(tmpdir(),'sundaynote-integration-'));
  const jobs=[];
  let child;
  t.after(async()=>{
    if(child?.exitCode===null){child.kill();await once(child,'exit');}
    for(const id of jobs)await exec('/usr/bin/podman',['rm','-f',id]).catch(()=>{});
    await rm(temp,{recursive:true,force:true});
  });
  await mkdir(join(temp,'rw'));await mkdir(join(temp,'ro'));
  await writeFile(join(temp,'ro','evidence.txt'),'source');
  await writeFile(join(temp,'private.txt'),'HOST_PRIVATE_MARKER');
  const config={version:1,image:process.env.SANDBOX_TEST_IMAGE,workspaces:[{id:'fixture',mounts:[
    {source:join(temp,'rw'),target:'/workspace/project',mode:'rw'},
    {source:join(temp,'ro'),target:'/workspace/source',mode:'ro'},
  ]}]};
  const configFile=join(temp,'config.json');await writeFile(configFile,JSON.stringify(config));
  const key=randomBytes(24).toString('hex');
  child=spawn(process.execPath,[new URL('./server.mjs',import.meta.url).pathname],{
    env:{...process.env,FS_API_KEY:key,BRIEFING_PORT:'0',SANDBOX_CONFIG:configFile},stdio:['ignore','pipe','pipe'],
  });
  const port=await new Promise((resolve,reject)=>{
    let output='',errors='';
    child.stderr.on('data',c=>{errors+=c;});
    child.stdout.on('data',c=>{output+=c;const m=output.match(/127\.0\.0\.1:(\d+)/);if(m)resolve(m[1]);});
    child.once('error',reject);child.once('exit',()=>reject(new Error(errors||'Server exited')));
  });
  const url='http://127.0.0.1:'+port+'/mcp';
  assert.equal((await fetch(url)).status,401);
  let counter=0;
  async function rpc(method,params){
    const response=await fetch(url,{method:'POST',headers:{authorization:'Bearer '+key,'content-type':'application/json',
      accept:'application/json, text/event-stream'},body:JSON.stringify({jsonrpc:'2.0',id:++counter,method,params})});
    assert.equal(response.status,200);
    const text=await response.text();
    const data=text.split('\n').find(l=>l.startsWith('data:'));
    const value=JSON.parse(data?data.slice(5):text);
    assert.equal(value.error,undefined);return value.result;
  }
  await rpc('initialize',{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'fixture',version:'1'}});
  const tools=(await rpc('tools/list',{})).tools;
  assert.deepEqual(tools.map(t=>t.name).sort(),['cancel_task','execute','list_tasks','list_workspaces','task_output']);
  assert.equal(tools.find(t=>t.name==='execute').annotations.destructiveHint,true);
  const call=(name,args)=>rpc('tools/call',{name,arguments:args});
  async function start(command,timeout_seconds=20){
    const result=await call('execute',{workspace:'fixture',command,timeout_seconds});
    assert.notEqual(result.isError,true,JSON.stringify(result));
    jobs.push(result.structuredContent.id);return result.structuredContent.id;
  }
  async function finish(id){
    for(let i=0;i<60;i++){
      const value=(await call('task_output',{id})).structuredContent;
      if(value&&!value.running)return value;
      await new Promise(r=>setTimeout(r,200));
    }
    throw new Error('Task did not finish');
  }
  assert.equal((await call('execute',{workspace:'unknown',command:'id'})).isError,true);
  const command=[
    'set -eu','test -z "${FS_API_KEY-}"','test ! -e '+temp+'/private.txt',
    'test ! -e /home/codex/.ssh','test ! -e /run/podman/podman.sock',
    'test "$(cat /sys/fs/cgroup/memory.max)" = 268435456',
    'test "$(cat /sys/fs/cgroup/pids.max)" = 64',
    'test "$(cat /sys/fs/cgroup/cpu.max)" = "50000 100000"',
    'test "$(cat /workspace/source/evidence.txt)" = source',
    'if touch /workspace/source/forbidden 2>/dev/null; then exit 12; fi',
    'if touch /etc/forbidden 2>/dev/null; then exit 13; fi',
    'python3 -c \'import socket; s=socket.socket(); s.settimeout(1); assert s.connect_ex(("1.1.1.1",443)) != 0\'',
    'git --version','gcc --version','python3 -c \'from pathlib import Path; Path("result.bin").write_bytes(bytes([0,1,255]))\'',
    'echo SANDBOX_OK',
  ].join('\n');
  const done=await finish(await start(command));
  assert.equal(done.exit_code,0,done.output);assert.match(done.output,/SANDBOX_OK/);
  assert.deepEqual(await readFile(join(temp,'rw','result.bin')),Buffer.from([0,1,255]));
  assert.equal(await readFile(join(temp,'private.txt'),'utf8'),'HOST_PRIVATE_MARKER');
  const running=await start('sleep 60 & wait',90);
  assert.equal((await call('execute',{workspace:'fixture',command:'id'})).isError,true);
  const cancelled=(await call('cancel_task',{id:running})).structuredContent;
  assert.equal(cancelled.running,false);
  assert.notEqual(cancelled.exit_code,0);
  const timed=await finish(await start('sleep 60',2));
  assert.equal(timed.running,false);assert.notEqual(timed.exit_code,0);
  assert.ok((await call('list_tasks',{})).structuredContent.tasks.some(t=>t.id===running));
});
