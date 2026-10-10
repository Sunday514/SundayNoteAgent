import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,symlink,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {loadConfig,runArgs,Sandbox} from './sandbox.mjs';

test('配置拒绝宽目录、符号链接、重复入口和动态扩权',async t=>{
  const temp=await mkdtemp(join(tmpdir(),'sundaynote-config-'));
  t.after(()=>rm(temp,{recursive:true,force:true}));
  const source=join(temp,'data'); await mkdir(source);
  const file=join(temp,'config.json');
  const config={version:1,image:'sha256:'+'a'.repeat(64),workspaces:[{id:'test',mounts:[{source,target:'/workspace/project',mode:'rw'}]}]};
  const save=()=>writeFile(file,JSON.stringify(config));
  await save(); assert.equal((await loadConfig(file)).workspaces.length,1);
  for(const path of ['/',process.env.HOME,'/etc',temp]){
    config.workspaces[0].mounts[0].source=path; await save(); await assert.rejects(loadConfig(file));
  }
  const link=join(temp,'link');await symlink(source,link);
  config.workspaces[0].mounts[0].source=link;await save();await assert.rejects(loadConfig(file));
  config.workspaces[0].mounts[0].source=source;
  config.workspaces[0].network='host';await save();await assert.rejects(loadConfig(file));
  delete config.workspaces[0].network;
  config.workspaces.push(config.workspaces[0]);await save();await assert.rejects(loadConfig(file));
});

test('命令只进入容器，参数锁定隔离和资源约束',()=>{
  const workspace={id:'test',mounts:[{source:'/tmp/fixture',target:'/workspace/project',mode:'ro'}]};
  const command='echo hello; cat /etc/passwd';
  const args=runArgs({image:'sha256:'+'a'.repeat(64)},workspace,'sundaynote-job-test',command,30);
  assert.equal(args.at(-1),command);
  for(const flag of ['--network=none','--http-proxy=false','--read-only','--cap-drop=ALL',
    '--security-opt=no-new-privileges','--userns=keep-id','--pids-limit=64','--memory=256m',
    '--cpus=0.5','--pull=never','--entrypoint=/bin/sh'])assert.ok(args.includes(flag));
  assert.ok(args.some(a=>a.includes('destination=/workspace/project,ro,')));
  assert.ok(!args.includes('--privileged'));
  assert.ok(!args.some(a=>a.includes('FS_API_KEY')));
});

test('任务操作拒绝任意容器名称和其他标签',async()=>{
  const sandbox=new Sandbox({workspaces:[]});
  sandbox.podman=async()=>({stdout:JSON.stringify([{Config:{Labels:{}}}])});
  await assert.rejects(sandbox.inspect('unrelated-container'));
  await assert.rejects(sandbox.inspect('sundaynote-job-'+'a'.repeat(36)));
  await assert.rejects(sandbox.start('missing','id'));
});

test('启动失败清理本次容器，不阻塞后续任务',async()=>{
  const sandbox=new Sandbox({workspaces:[{id:'test',mounts:[{source:'/tmp',target:'/workspace/project',mode:'rw'}]}]});
  const calls=[];
  sandbox.podman=async args=>{
    calls.push(args);
    if(args[0]==='ps')return {stdout:'[]'};
    if(args[0]==='run')throw new Error('runtime start failed');
    return {stdout:''};
  };
  await assert.rejects(sandbox.start('test','id'),/runtime start failed/);
  const start=calls.find(a=>a[0]==='run');
  assert.deepEqual(calls.at(-1),['rm','-f',start[start.indexOf('--name')+1]]);
});
