"""Full-catalog contaminant database. Procurement flags never restrict ingestion."""
import os
os.environ.setdefault('OTEL_SDK_DISABLED','true')
os.environ.setdefault('CREWAI_TELEMETRY_ENABLED','false')
os.environ.setdefault('CREWAI_TRACING_ENABLED','false')
# Loopback traffic must reach the local server rather than an inherited proxy.
for _proxy_key in ('NO_PROXY','no_proxy'):
    _hosts=[value.strip() for value in os.getenv(_proxy_key,'').split(',') if value.strip()]
    os.environ[_proxy_key]=','.join(dict.fromkeys([*_hosts,'127.0.0.1','localhost','::1']))
__version__ = "0.2.8"
