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


def validated_tool_reply(observations,error):
    """Present executed tool facts after rejecting the model's explanation."""
    if not observations:raise error
    pending=[run for run in observations if run.get('status')=='needs_entity_selection']
    runs=pending or observations
    ids=[];urls=[];lines=[];gaps=[]
    for run in runs:
        state=run.get('status');selection=run.get('selection',{})
        if state=='needs_entity_selection':
            choices=selection.get('choices',[])
            lines.append(f"目录匹配到 {selection.get('total',len(choices))} 个候选，当前显示前 {len(choices)} 个。请明确选择实体，或补充 CAS、实体编号缩小范围。")
            continue
        if state=='not_found':
            lines.append('目录中未找到该对象，请核对名称、CAS 或实体编号。')
            continue
        ident=run.get('entity_id');result=run.get('results',{});identity=result.get('identity',{})
        if not ident:
            lines.append('本次工具未能定位实体，请查看工具记录。');continue
        if ident not in ids:ids.append(ident)
        lines.append(f"### {ident} · {identity.get('name') or '目录实体'}")
        lines.append('CAS：'+str(identity.get('cas') or '未填写')+'；身份'+('已人工确认。' if identity.get('human_review')=='confirmed' else '待人工核查，数据库匹配不等于身份确认。'))
        for source in run.get('provenance',{}).get('sources',[]):
            if source.get('url') and source['url'] not in urls:urls.append(source['url'])
        structure=result.get('structure',{})
        if structure and structure.get('status')!='parsed':
            reason=structure.get('reason') or '结构步骤未完成，请查看工具记录。'
            lines.append('结构分析：'+reason);gaps.append(reason)
        groups=result.get('functional_groups',{})
        if groups.get('status')=='parsed':
            lines.append('官能团规则版本：'+str(groups.get('rules_version','未注明'))+'。')
            values=groups.get('functional_groups',{})
            lines.extend(f"- {name}：{value['count']}" for name,value in values.items())
            if not values:lines.append('当前有限规则未匹配到片段。')
            lines.append('数值为规则匹配次数；芳香原子数不是芳香环数，片段命中不构成毒性判断。')
        similarity=result.get('similarity',{})
        if similarity.get('neighbors'):
            lines.append('结构相似物（Tanimoto）：')
            lines.extend(f"- {v['id']} · {v['name']}：{v['similarity']:.3f}" for v in similarity['neighbors'])
            lines.append('结构相似性不表示毒性等效。')
        ecology=result.get('ecology')
        if ecology is not None:
            evidence=ecology.get('evidence',[])
            total=ecology.get('evidence_count',len(evidence))
            lines.append(f'目录收录 {total} 条生态证据，本次展示 {len(evidence)} 条。')
            if ecology.get('evidence_summary'):lines.append(ecology['evidence_summary']['notice'])
            if not evidence:lines.append('当前未收录证据，不能据此判断安全。')
            for item in evidence[:12]:
                text=str(item.get('original_text') or '').strip()
                value=text or ((item.get('relation','') if item.get('relation')!='=' else '')+str(item.get('value','—')))
                medium={'freshwater':'淡水','marine':'河口/海水','soil':'土壤','sediment':'沉积物'}.get(item.get('compartment'),item.get('compartment') or '未注明')
                duration={'acute':'急性','chronic':'慢性','unknown':'未注明'}.get(item.get('duration_class'),item.get('duration_class') or '未注明')
                lines.append(f"- {item.get('endpoint','未注明端点')}；对象：{item.get('taxon') or '未注明'}；介质：{medium}；时长类别：{duration}；{value} {item.get('unit') or ''}。")
            if total>12:lines.append('以上展示前 12 条；请打开实体详情翻页或导出完整证据 CSV。本轮问答记录只保存展示部分。')
            reason=ecology.get('risk',{}).get('reason') or ecology.get('reason')
            if reason:lines.append(reason);gaps.append(reason)
        for warning in run.get('warnings',[]):
            if warning not in gaps:gaps.append(warning)
    return {'entity_ids':ids,'answer':'\n\n'.join(lines),'supported_findings':[],'source_urls':urls,
        'missing_inputs':list(dict.fromkeys(gaps)),'risk_score':None,'workflow_runs':observations,
        'execution_mode':'deterministic_fallback','llm_used':True,'model_answer_accepted':False,
        'model_validation':{'code':error.code,'message':error.public_message},
        'verification_scope':'模型解释未通过校验，已舍弃；此处仅整理本轮实际执行的固定工具结果。'}


def directory_reply(observations):
    """Directory status and choices are controlled by the actual lookup."""
    if not observations:return None
    if not any(run.get('status')=='needs_entity_selection' for run in observations) and not all(run.get('status')=='not_found' for run in observations):
        return None
    reply=validated_tool_reply(observations,AnswerValidationError('directory_state','目录状态'))
    reply.update(execution_mode='directory_result',model_answer_accepted=None,model_validation=None,
        verification_scope='目录状态与候选由实际检索结果生成；尚未定位明确实体，不提供实体级科学结论。')
    return reply

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
