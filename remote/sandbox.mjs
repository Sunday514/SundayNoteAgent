import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, realpath, stat } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { z } from 'zod';

const exec = promisify(execFile);
const label = 'io.sundaynote.sandbox';
const idSchema = z.string().regex(/^sundaynote-job-[a-f0-9-]{36}$/);
const schema = z.object({
  version: z.literal(1),
  image: z.string().regex(/^sha256:[a-f0-9]{64}$/),
  workspaces: z.array(z.object({
    id: z.string().regex(/^[a-z][a-z0-9-]{0,39}$/),
    mounts: z.array(z.object({
      source: z.string().startsWith('/'),
      target: z.string().regex(/^\/workspace\/[a-z][a-z0-9-]{0,39}$/),
      mode: z.enum(['ro', 'rw']),
    }).strict()).min(1).max(8),
  }).strict()).max(16),
}).strict();
const within = (p, root) => p === root || p.startsWith(root + '/');

export async function loadConfig(file) {
  if (!file) throw new Error('SANDBOX_CONFIG is required');
  const config = schema.parse(JSON.parse(await readFile(file, 'utf8')));
  const ids = new Set();
  const protectedPaths = [process.env.HOME, dirname(resolve(file)), dirname(new URL(import.meta.url).pathname)].filter(Boolean);
  for (const workspace of config.workspaces) {
    if (ids.has(workspace.id)) throw new Error('Duplicate workspace');
    ids.add(workspace.id);
    const targets = new Set();
    for (const mount of workspace.mounts) {
      if (targets.has(mount.target)) throw new Error('Duplicate mount target');
      targets.add(mount.target);
      const source = await realpath(mount.source);
      if (source !== mount.source || /[,\n\r]/.test(source) || !(await stat(source)).isDirectory()) throw new Error('Noncanonical mount');
      if (['/', '/home', '/srv', '/mnt', '/media', '/opt', '/var', '/tmp'].includes(source)
          || ['/etc', '/proc', '/sys', '/dev', '/run', '/root'].some(p => within(source, p))
          || source.split('/').some(p => ['.ssh', '.config', '.local', '.aws', '.codex'].includes(p))
          || protectedPaths.some(p => within(p, source))) throw new Error('Unsafe mount');
    }
  }
  return config;
}

export function runArgs(config, workspace, id, command, timeout) {
  return ['run', '-d', '--name', id, '--label', label + '=1',
    '--label', 'io.sundaynote.workspace=' + workspace.id, '--pull=never',
    '--network=none', '--http-proxy=false', '--no-hosts',
    '--read-only', '--read-only-tmpfs=false', '--cap-drop=ALL',
    '--security-opt=no-new-privileges', '--userns=keep-id',
    '--user', process.getuid() + ':' + process.getgid(), '--pid=private', '--ipc=private',
    '--pids-limit=64', '--memory=256m', '--memory-swap=256m', '--cpus=0.5',
    '--ulimit=nofile=1024:1024', '--ulimit=fsize=67108864:67108864',
    '--timeout', String(timeout), '--stop-timeout=1',
    '--log-driver=k8s-file', '--log-opt=max-size=1048576',
    '--tmpfs=/tmp:rw,nosuid,nodev,size=64m,mode=1777',
    '--tmpfs=/workspace:rw,nosuid,nodev,size=1m,mode=755',
    '--env=HOME=/tmp', '--env=TMPDIR=/tmp',
    ...workspace.mounts.flatMap(m => ['--mount',
      'type=bind,source=' + m.source + ',destination=' + m.target + ',' + m.mode + ',bind-propagation=rprivate']),
    '--workdir', workspace.mounts[0].target, '--entrypoint=/bin/sh', config.image, '-lc', command];
}

export class Sandbox {
  constructor(config) { this.config = config; this.queue = Promise.resolve(); }
  async podman(args) {
    const env = {};
    for (const name of ['HOME', 'PATH', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS']) {
      if (process.env[name]) env[name] = process.env[name];
    }
    return exec('/usr/bin/podman', ['--remote=false', ...args], {env, timeout:30000, maxBuffer:4*1024*1024});
  }
  async preflight() {
    if (process.getuid() === 0) throw new Error('Root MCP is forbidden');
    const info = JSON.parse((await this.podman(['info', '--format=json'])).stdout);
    if (!info.host.security.rootless || info.host.cgroupVersion !== 'v2'
        || !['cpu','memory','pids'].every(c => info.host.cgroupControllers.includes(c))) {
      throw new Error('Rootless Podman with CPU/memory/PID cgroups v2 is required');
    }
    await this.podman(['image', 'exists', this.config.image]);
  }
  workspaces() {
    return {network:'none', max_concurrent_tasks:1, timeout_max_seconds:300, memory_mib:256, pids:64, cpus:0.5,
      workspaces:this.config.workspaces.map(w => ({id:w.id, mounts:w.mounts.map(({target,mode}) => ({path:target,mode}))}))};
  }
  async tasks() {
    const {stdout} = await this.podman(['ps','-a','--filter','label='+label+'=1','--format=json']);
    return JSON.parse(stdout).map(c => ({id:c.Names[0], state:c.State, workspace:c.Labels['io.sundaynote.workspace']}));
  }
  async start(workspaceId, command, timeout=60) {
    const operation = this.queue.then(async () => {
      const workspace = this.config.workspaces.find(w => w.id === workspaceId);
      if (!workspace) throw new Error('Unknown workspace');
      const tasks = await this.tasks();
      if (tasks.some(t => !['exited','stopped'].includes(t.state))) throw new Error('Another task is active');
      for (const task of tasks.filter(t => ['exited','stopped'].includes(t.state)).slice(19)) {
        idSchema.parse(task.id);
        await this.podman(['rm',task.id]);
      }
      for (const mount of workspace.mounts) {
        if (await realpath(mount.source) !== mount.source) throw new Error('Mount path changed');
      }
      const id = 'sundaynote-job-' + randomUUID();
      try {
        await this.podman(runArgs(this.config,workspace,id,command,timeout));
      } catch (error) {
        await this.podman(['rm','-f',id]).catch(() => {});
        throw error;
      }
      return {id, workspace:workspaceId, accepted:true, timeout_seconds:timeout};
    });
    this.queue = operation.catch(() => {});
    return operation;
  }
  async inspect(id) {
    idSchema.parse(id);
    const c = JSON.parse((await this.podman(['inspect',id])).stdout)[0];
    if (c.Config.Labels[label] !== '1') throw new Error('Not a SundayNote task');
    return c;
  }
  async output(id) {
    const c = await this.inspect(id);
    const {stdout,stderr} = await this.podman(['logs','--tail=200',id]);
    return {id, state:c.State.Status, running:c.State.Running, exit_code:c.State.Running ? null : c.State.ExitCode,
      oom_killed:c.State.OOMKilled, output:(stdout+stderr).slice(-64000),
      output_window:'Last 200 lines, at most 64000 characters; older output may be discarded'};
  }
  async cancel(id) {
    const c = await this.inspect(id);
    if (c.State.Running) await this.podman(['stop','--time=0',id]);
    return this.output(id);
  }
}
