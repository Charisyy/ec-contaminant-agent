"""Exact help intents and safe answer-validation errors; no scientific rules."""
import re

class AnswerValidationError(ValueError):
    def __init__(self,code,message):
        super().__init__(message)
        self.code=code
        self.public_message=message

def local_help(question):
    normalized=re.sub(r'[\s，。！？!?.,；;：:]+','',question.casefold())
    greetings={'你好','您好','在吗','早上好','下午好','晚上好','hi','hello'}
    thanks={'谢谢','谢谢你','多谢','thanks','thankyou'}
    help_intents={'帮助','使用帮助','你能做什么','你能干什么','help'}
    if normalized not in greetings|thanks|help_intents:return None
    answer=('不客气。需要继续分析时，请提供名称、CAS 或实体编号。' if normalized in thanks else
        '你好。可以查询化学身份、分析结构与官能团、寻找相似物，以及整理生态证据。'
        '请提供名称、CAS 或实体编号，例如：“分析 EC0872 的官能团和生态证据”。')
    return {'entity_ids':[],'answer':answer,'supported_findings':[],'source_urls':[],
        'missing_inputs':[],'risk_score':None,'workflow_runs':[],
        'execution_mode':'local_help','llm_used':False,
        'verification_scope':'本地使用帮助；未请求模型，未执行科研分析。'}
