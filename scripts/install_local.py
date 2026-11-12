#!/usr/bin/env python3
"""Set up a private venv; optionally connect this checkout to local Codex."""
import argparse,json,re,shutil,subprocess,sys,venv
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def main():
    p=argparse.ArgumentParser(description='阅读之间：安装本地依赖与可选的 Codex MCP 连接')
    p.add_argument('--connect-codex',action='store_true',help='显式将服务注册到当前电脑的 Codex 配置')
    p.add_argument('--name',default='reading-between')
    a=p.parse_args()
    if sys.version_info<(3,11):raise SystemExit('需要 Python 3.11+。')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',a.name):raise SystemExit('服务名称只支持小写字母、数字和连字符。')
    env=ROOT/'.venv';py=env/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    if not py.exists():venv.create(env,with_pip=True)
    subprocess.run([str(py),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
    subprocess.run([str(py),'-m','pip','check'],check=True)
    command=[str(py),str(ROOT/'scripts/server.py')]
    config={'mcpServers':{a.name:{'type':'stdio','command':command[0],'args':command[1:]}}}
    (ROOT/'mcp.local.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    if a.connect_codex:
        codex=shutil.which('codex')
        if not codex:raise SystemExit('依赖已安装；未找到 Codex CLI。请先安装 Codex，或按 mcp.local.json 手动添加本地服务。')
        old=subprocess.run([codex,'mcp','get',a.name,'--json'],capture_output=True,text=True)
        if old.returncode==0:
            existing=json.loads(old.stdout);transport=existing.get('transport',{})
            if transport.get('command')!=command[0] or transport.get('args')!=command[1:]:
                raise SystemExit('存在同名但配置不同的服务，未覆盖。请使用 --name 指定新名称。')
            print('同一服务已经配置。')
        else:subprocess.run([codex,'mcp','add',a.name,'--',*command],check=True)
        print('本机 MCP 配置已完成。请在支持的客户端中新建对话，确认能发现并调用工具。')
    else:print('依赖已安装；客户端尚未连接。配置文件：mcp.local.json')
    print('数据在本机用户数据目录；不会自动上传或调用模型 API。')
if __name__=='__main__':main()
