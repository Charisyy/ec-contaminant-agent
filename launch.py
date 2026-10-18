"""Launch the local Windows application and open its browser when ready."""
from __future__ import annotations
import json,os,sys,threading,time,urllib.request,webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env',override=False)
    from ec_agent import __version__
    from ec_agent.server import serve
    port=int(os.getenv('EC_AGENT_PORT','8766'))
    if not 1<=port<=65535:raise ValueError('端口无效')
    url=f'http://127.0.0.1:{port}'
    def open_when_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(url+'/api/health',timeout=1) as response:health=json.load(response)
                if health.get('application')=='ec-catalog-agent' and health.get('version')==__version__:
                    webbrowser.open(url);return
            except (OSError,ValueError):pass
            time.sleep(.5)
        print('浏览器尚未打开。请查看启动信息，服务就绪后手动访问 '+url,flush=True)
    print('正在启动新污染物研究工作台。使用期间请保留此窗口；按 Ctrl+C 停止。',flush=True)
    threading.Thread(target=open_when_ready,daemon=True).start()
    serve(ROOT/'data/catalog.sqlite',port)

if __name__=='__main__':main()
