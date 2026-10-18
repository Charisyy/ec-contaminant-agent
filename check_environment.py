"""Local startup check. Reads the bundled database; never rebuilds it."""
from __future__ import annotations
import sys
from pathlib import Path

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    if sys.version_info[:2]!=(3,12):raise RuntimeError('此 Windows 交付版请使用 Python 3.12（64位）。')
    import struct
    if struct.calcsize('P')!=8:raise RuntimeError('请安装64位 Python。')
    sys.path.insert(0,str(Path(__file__).parent/'src'))
    import crewai,rdkit
    from ec_agent.db import DEFAULT_DB,connect_readonly,stats
    from ec_agent.policy import method_metadata
    with connect_readonly(DEFAULT_DB) as con:
        profile=stats(con)
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('数据库完整性检查未通过。')
    print('环境检查通过：Python 3.12 / CrewAI '+crewai.__version__+' / RDKit '+rdkit.__version__)
    print('目录实体：'+str(profile['entities'])+'；暴露记录：'+str(profile['exposure_records']))
    print('当前官能团规则：'+method_metadata()['rules_version']+'；历史缓存保持原样。')
    return profile

if __name__=='__main__':main()
