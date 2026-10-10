import { McpServer, createMcpHandler } from '@modelcontextprotocol/server';
import { toNodeHandler } from '@modelcontextprotocol/node';
import { hostHeaderValidation, localhostOriginValidation } from '@modelcontextprotocol/express';
import express from 'express';
import { z } from 'zod';
import { timingSafeEqual } from 'node:crypto';
import { Sandbox, loadConfig } from './sandbox.mjs';

const key = process.env.FS_API_KEY;
if (!key || key.length < 16) throw new Error('Missing secure MCP credential');
const sandbox = new Sandbox(await loadConfig(process.env.SANDBOX_CONFIG));
await sandbox.preflight();
function createServer() {
  const server = new McpServer({name:'sundaynote-sandbox',version:'3.0.0'}, {
    instructions:'Inspect list_workspaces before executing. Commands run in a rootless offline container, never on the VPS host. Use Shell/Python/Git for reading, searching, patching and testing. Writable mounts permit overwrites and deletion: confirm task scope, preserve user work, and use version control or copies. There is no paragraph filtering or implicit backup. An accepted task is not completed; inspect its output and exit code. No host administration, mount changes, networking or credentials are exposed.'
  });
  function register(name,description,inputSchema,handler,readOnlyHint=true) {
    server.registerTool(name,{description,inputSchema,annotations:{readOnlyHint,destructiveHint:!readOnlyHint,openWorldHint:false}},async args => {
      try {
        const result = await handler(args);
        return {content:[{type:'text',text:JSON.stringify(result)}],structuredContent:result};
      } catch {
        return {isError:true,content:[{type:'text',text:'Operation failed or refused. Check workspace, task ID and available capacity; no success claimed.'}]};
      }
    });
  }
  const id = z.string().regex(/^sundaynote-job-[a-f0-9-]{36}$/);
  register('list_workspaces','Inspect isolated workspaces, mount access and resource limits; no host paths.',{},()=>sandbox.workspaces());
  register('execute','Start a Shell command in an authorized offline container. Can modify/delete writable files. Returns a task ID, not completion. Use installed Python, Git or compilers; no host shell.',{
    workspace:z.string().regex(/^[a-z][a-z0-9-]{0,39}$/),command:z.string().min(1).max(16000),
    timeout_seconds:z.number().int().min(1).max(300).default(60),
  },a=>sandbox.start(a.workspace,a.command,a.timeout_seconds),false);
  register('list_tasks','List retained tasks, including tasks surviving an MCP restart. Retains up to 20 completed tasks.',{},async()=>({tasks:await sandbox.tasks()}));
  register('task_output','Read bounded output and exit status. Running is not success; exited tasks can fail or time out.',{id},a=>sandbox.output(a.id));
  register('cancel_task','Stop the entire task container, including child processes. Does not undo file modifications.',{id},a=>sandbox.cancel(a.id),false);
  return server;
}
const app=express();
app.disable('x-powered-by');
app.use(hostHeaderValidation(['127.0.0.1','localhost']));
app.use(localhostOriginValidation());
app.get('/healthz',(_req,res)=>res.json({status:'ok',isolation:'rootless-podman'}));
app.use('/mcp',(req,res,next)=>{
  const supplied=Buffer.from(req.headers.authorization||'');
  const expected=Buffer.from('Bearer '+key);
  if(supplied.length!==expected.length||!timingSafeEqual(supplied,expected)) return res.status(401).end();
  next();
});
const handler=toNodeHandler(createMcpHandler(async()=>createServer(),{legacy:'stateless',maxRequestBodySize:65536}),{maxRequestBodySize:65536});
app.all('/mcp',(req,res)=>{void handler(req,res).catch(()=>{if(!res.headersSent)res.status(500).end();});});
const listener=app.listen(Number(process.env.BRIEFING_PORT||8787),'127.0.0.1',()=>
  console.log('SundayNote MCP listening on 127.0.0.1:'+listener.address().port));
