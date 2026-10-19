"""Local model configuration. Public responses never contain credentials."""
from __future__ import annotations
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit
from .db import PROJECT

LOCK=threading.RLock()
ENV_PATH=PROJECT/'.env'
PRESETS=[{'id':'deepseek','label':'DeepSeek','base_url':'https://api.deepseek.com',
    'model':'deepseek-flash','documentation':'https://api-docs.deepseek.com/zh-cn/'},
    {'id':'compatible','label':'自定义兼容接口','base_url':'','model':''}]
KEYS=('EC_AGENT_PROVIDER','EC_AGENT_MODEL','EC_AGENT_BASE_URL','EC_AGENT_API_KEY')

def runtime_settings():
    with LOCK:return {key:os.getenv(key,'').strip() for key in KEYS}

def public_settings():
    value=runtime_settings()
    provider=value['EC_AGENT_PROVIDER'] or ('deepseek' if value['EC_AGENT_BASE_URL']=='https://api.deepseek.com' else 'compatible')
    return {'provider':provider,'model':value['EC_AGENT_MODEL'].removeprefix('openai/'),
        'base_url':value['EC_AGENT_BASE_URL'],'key_configured':bool(value['EC_AGENT_API_KEY']),
        'configured':bool(value['EC_AGENT_MODEL'] and value['EC_AGENT_API_KEY']),
        'presets':PRESETS}

def save_settings(data,env_path=None):
    if not isinstance(data,dict) or set(data)-{'provider','model','base_url','api_key','clear'}:
        raise ValueError('模型设置包含无效字段')
    path=Path(env_path) if env_path is not None else ENV_PATH
    with LOCK:
        previous=runtime_settings()
        if data.get('clear') is True:
            changes={key:'' for key in KEYS}
        else:
            provider=data.get('provider','compatible')
            if provider not in ('deepseek','compatible'):raise ValueError('请选择有效的接口类型')
            preset=PRESETS[0] if provider=='deepseek' else data
            model=preset.get('model','');base=preset.get('base_url','')
            key=data.get('api_key','')
            if not all(isinstance(v,str) for v in (model,base,key)):raise ValueError('模型设置必须为文本')
            model,base,key=model.strip(),base.strip().rstrip('/'),key.strip()
            if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,120}',model):raise ValueError('请填写有效的模型名称')
            parsed=urlsplit(base)
            if len(base)>2048 or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('请填写服务基础地址，不含凭据、查询参数或片段')
            if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1','::1')):
                raise ValueError('远程接口使用 HTTPS；本机服务可使用 HTTP')
            if base.endswith(('/chat/completions','/responses')):raise ValueError('请填写基础地址，例如 https://example.com/v1，而非完整请求路径')
            if len(key)>8192 or any(ord(c)<32 or ord(c)==127 for c in key):raise ValueError('Key 包含无效字符')
            if not key:
                same=base==previous['EC_AGENT_BASE_URL']
                key=previous['EC_AGENT_API_KEY'] if same else ''
            if not key:raise ValueError('请填写 API Key；更换服务地址时需要重新填写')
            changes={'EC_AGENT_PROVIDER':provider,'EC_AGENT_MODEL':model if '/' in model else 'openai/'+model,
                'EC_AGENT_BASE_URL':base,'EC_AGENT_API_KEY':key}
        from dotenv import set_key
        path.parent.mkdir(parents=True,exist_ok=True)
        # Preserve other local settings; replace all model values as one file operation.
        handle,temp_name=tempfile.mkstemp(prefix='.model-settings-',dir=path.parent)
        os.close(handle)
        temp=Path(temp_name)
        try:
            temp.write_text(path.read_text(encoding='utf-8') if path.exists() else '',encoding='utf-8')
            for name,value in changes.items():set_key(temp,name,value,quote_mode='always')
            os.replace(temp,path)
        finally:temp.unlink(missing_ok=True)
        os.environ.update(changes)
        return public_settings()

def transport_options(settings):
    # DeepSeek defaults to thinking mode; this adapter uses the documented
    # non-thinking tool protocol and does not request or expose reasoning content.
    if settings.get('EC_AGENT_PROVIDER')=='deepseek' or urlsplit(settings.get('EC_AGENT_BASE_URL','')).hostname=='api.deepseek.com':
        return {'extra_body':{'thinking':{'type':'disabled'}}}
    return {}

def model_failure_details(exc):
    from .conversation import AnswerValidationError
    seen=set();current=exc;status=None;name=type(exc).__name__.lower()
    for _ in range(8):
        if current is None or id(current) in seen:break
        seen.add(id(current))
        if isinstance(current,AnswerValidationError):
            return {'error':current.public_message,'code':current.code,'stage':'answer_validation'}
        candidate=getattr(current,'status_code',None)
        if isinstance(candidate,int):status=candidate;name=type(current).__name__.lower();break
        current=current.__cause__ or current.__context__
    code='model_request_failed';message='模型请求未完成；请查看本次错误编号，固定分析仍可使用。'
    if status in (401,403) or 'authentication' in name:code='model_authentication';message='模型认证失败，请检查 Key 和服务权限。'
    elif status==402:code='model_balance';message='模型服务账户余额不足，请检查账户状态。'
    elif status==429 or 'ratelimit' in name:code='model_rate_limit';message='模型服务额度或请求频率受限，请检查账户状态。'
    elif 'timeout' in name:code='model_timeout';message='模型服务响应超时，请稍后重试。'
    elif 'connection' in name:code='model_connection';message='无法连接模型服务，请检查服务地址和网络。'
    elif status==404:code='model_not_found';message='未找到模型或接口，请检查模型名称和服务地址。'
    elif status in (400,422):code='model_protocol';message='模型服务拒绝了请求参数或工具调用格式。基础连接成功也可能出现此错误。'
    elif status is not None and status>=500:code='model_service';message='模型服务暂时异常，请稍后重试。'
    value={'error':message,'code':code,'stage':'model_request'}
    if status is not None:value['provider_status']=status
    return value

def model_error(exc):return model_failure_details(exc)['error']

def test_connection():
    settings=runtime_settings()
    if not settings['EC_AGENT_MODEL'] or not settings['EC_AGENT_API_KEY']:raise ValueError('请先保存模型设置')
    from openai import OpenAI
    # Only this explicit action sends a small model request; no catalog data is sent.
    client=OpenAI(api_key=settings['EC_AGENT_API_KEY'],base_url=settings['EC_AGENT_BASE_URL'] or None,
        timeout=20,max_retries=0)
    model=settings['EC_AGENT_MODEL'].removeprefix('openai/')
    if model.startswith('gpt-6'):
        result=client.responses.create(model=model,input='Reply OK.',max_output_tokens=16,store=False)
        if not result.id:raise RuntimeError('Empty model response')
    else:
        result=client.chat.completions.create(model=model,
            messages=[{'role':'user','content':'Reply OK.'}],max_tokens=16,**transport_options(settings))
        if not result.choices:raise RuntimeError('Empty model response')
    return {'ok':True,'message':'模型请求成功。工具调用能力仍需通过实际研究问题验证。'}
