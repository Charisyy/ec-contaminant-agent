"""Exact help intents and safe answer-validation errors; no scientific rules."""
import re

class ConversationContextError(ValueError):pass

def question_with_context(question,entity_id,db):
    if entity_id is None:return question
    if not isinstance(entity_id,str) or not re.fullmatch(r'EC\d{4}',entity_id):
        raise ConversationContextError('当前研究对象编号无效，请重新选择目录实体。')
    from .db import connect_readonly,dumps
    with connect_readonly(db) as con:
        row=con.execute('SELECT id,name,cas FROM entities WHERE id=?',(entity_id,)).fetchone()
    if row is None:raise ConversationContextError('当前研究对象已不在目录中，请重新选择。')
    return question+'\n\n页面中已定位的研究对象（不表示化学身份已确认）：'+dumps(dict(row))+'\n仅在当前问题未明确指定对象时参考。若问题指定其他对象，以当前问题为准；重名仍须让用户选择。'

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
