"""Stable citation identifiers derived only from observed tool sources."""
import hashlib
import re

def citation_sources(observations,entity_ids=None):
    values={}
    for run in observations:
        if entity_ids is not None and run.get('entity_id') not in entity_ids:continue
        for source in run.get('provenance',{}).get('sources',[]):
            url=source.get('url')
            if not url:continue
            ref='SRC_'+hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
            if ref in values and values[ref]!=url:raise ValueError('Citation identifier collision')
            values[ref]=url
    return [{'ref':ref,'url':url} for ref,url in values.items()]

def resolve_citations(refs,observations,entity_ids):
    from .conversation import AnswerValidationError
    allowed={value['ref']:value['url'] for value in citation_sources(observations,entity_ids)}
    if any(ref not in allowed for ref in refs):
        raise AnswerValidationError('unobserved_source','模型引用的来源编号未在本次工具结果中出现，回答未通过核查。')
    return list(dict.fromkeys(allowed[ref] for ref in refs))

def text_urls(text):
    return [url.rstrip('.,;:!?，。；：！？）)') for url in re.findall(r'https?://[^\s<>"\[\]{}]+',text)]
