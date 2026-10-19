/* Research conversation UI. Suggested prompts are local templates, never model calls. */
(() => {
  'use strict';
  const STORE='ec-research-conversations-v1';
  const PROMPTS=[
    {id:'identity',group:'structure',title:'核查一个物质的身份',note:'名称、CAS、结构来源与人工核查状态',question:'核查可卡因的名称、CAS、选定结构和身份核查状态，列出来源。'},
    {id:'groups',group:'structure',title:'识别结构与官能团',note:'查看结构片段、匹配次数与方法版本',question:'分析 EC0872 的分子结构和官能团，并注明所用结构与规则版本。'},
    {id:'neighbors',group:'neighbors',title:'寻找结构相似物',note:'在全目录中查找近邻，查看已有结构簇',question:'查找与可卡因结构相近的目录实体，列出相似度和已有结构簇。'},
    {id:'evidence',group:'ecology',title:'整理已有生态证据',note:'区分基准类型，保留来源和数据缺口',question:'整理 EC0872 已收录的生态证据，区分基准类型，并列出数据缺口。'},
    {id:'selection',group:'structure',title:'从同名候选开始',note:'先定位目录中的具体研究对象',question:'查找目录中名称包含“双酚”的实体，返回候选供我选择。'},
    {id:'gaps',group:'ecology',title:'列出需要补充的资料',note:'围绕身份、结构与生态证据安排核查',question:'分析 EC0872 的身份与生态证据目前还缺哪些资料，不生成综合风险排名。'}
  ];
  const uid=()=>crypto.randomUUID();
  const timeLabel=value=>new Date(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false});
  const dateLabel=value=>new Date(value).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});
  let sessions=[],activeId='',busy=false,filter='all',pendingId=null,pendingSince=0;
  const rawResults=new Map();

  function freshSession(){return {id:uid(),title:'新对话',updatedAt:Date.now(),messages:[],entity:null,draft:''};}
  function session(){return sessions.find(value=>value.id===activeId);}
  function loadHistory(){
    try{
      const stored=JSON.parse(localStorage.getItem(STORE)||'null');
      if(stored?.version===1&&Array.isArray(stored.sessions)){
        sessions=stored.sessions.filter(value=>value&&typeof value.id==='string'&&Array.isArray(value.messages)).slice(0,12)
          .map(value=>({...value,title:String(value.title||'研究对话'),messages:value.messages.filter(message=>message&&['user','assistant'].includes(message.role)&&typeof message.text==='string').slice(-40),draft:String(value.draft||'')}));
        activeId=sessions.some(value=>value.id===stored.activeId)?stored.activeId:sessions[0]?.id||'';
      }
    }catch(_){sessions=[];}
    if(!sessions.length){const value=freshSession();sessions=[value];activeId=value.id;}
  }
  function savedResponse(data){
    if(!data)return null;
    return {stored_view:'conversation_summary',entity_ids:data.entity_ids||[],answer:data.answer||'',
      supported_findings:data.supported_findings||[],source_urls:data.source_urls||[],missing_inputs:data.missing_inputs||[],
      risk_score:data.risk_score??null,execution_mode:data.execution_mode,llm_used:data.llm_used,
      model_answer_accepted:data.model_answer_accepted,model_validation:data.model_validation,
      verification_scope:data.verification_scope,workflow_runs:(data.workflow_runs||[]).map(run=>({
        run_id:run.run_id,entity_id:run.entity_id,status:run.status,query:run.query,workflows:run.workflows,
        selection:run.selection,trace:run.trace,methods:run.methods,provenance:run.provenance,
        warnings:run.warnings,artifact_name:run.artifact_name,
        results:{identity:run.results?.identity?{id:run.results.identity.id,name:run.results.identity.name,cas:run.results.identity.cas}:null}
      }))};
  }
  function persist(){
    try{
      let values=[session(),...sessions.filter(value=>value.id!==activeId).sort((a,b)=>b.updatedAt-a.updatedAt)].slice(0,12).map(value=>({...value,messages:value.messages.slice(-40).map(message=>({...message,data:savedResponse(message.data)}))}));
      let encoded=JSON.stringify({version:1,activeId,sessions:values});
      while(encoded.length>1800000&&values.length>1){
        const index=values.findLastIndex(value=>value.id!==activeId);values.splice(index,1);
        encoded=JSON.stringify({version:1,activeId,sessions:values});
      }
      localStorage.setItem(STORE,encoded);
      const retainedSessions=new Set(values.map(value=>value.id));
      sessions=sessions.filter(value=>retainedSessions.has(value.id));
      sessions.forEach(value=>{value.messages=value.messages.slice(-40);});
      const retainedMessages=new Set(sessions.flatMap(value=>value.messages.map(message=>message.id)));
      for(const id of rawResults.keys())if(!retainedMessages.has(id))rawResults.delete(id);
    }catch(_){$('#chat-live-status').textContent='浏览器未能保存记录；当前页面仍可继续对话。';}
  }
  function drawSessions(){
    $('#chat-session-list').innerHTML=[...sessions].sort((a,b)=>b.updatedAt-a.updatedAt).map(value=>`<button class="chat-session ${value.id===activeId?'active':''}" data-chat-session="${esc(value.id)}" ${busy?'disabled':''}><span class="chat-session-title">${esc(value.title)}</span><small>${dateLabel(value.updatedAt)} · ${Math.ceil(value.messages.length/2)} 轮问答</small></button>`).join('');
    $('#chat-export').disabled=!session().messages.length||busy;
    document.querySelectorAll('[data-chat-session]').forEach(button=>button.onclick=()=>{
      if(busy)return;session().draft=$('#question').value;activeId=button.dataset.chatSession;persist();render();
    });
  }
  function drawPrompts(){
    const values=PROMPTS.filter(value=>filter==='all'||value.group===filter);
    $('#chat-prompts').innerHTML=values.map(value=>`<button class="chat-prompt" data-chat-prompt="${value.id}" title="${esc(value.question)}"><span class="chat-prompt-number">${String(PROMPTS.indexOf(value)+1).padStart(2,'0')}</span><span><strong>${esc(value.title)}</strong><p>${esc(value.note)}</p></span></button>`).join('');
    document.querySelectorAll('[data-chat-filter]').forEach(button=>button.classList.toggle('active',button.dataset.chatFilter===filter));
    document.querySelectorAll('[data-chat-prompt]').forEach(button=>button.onclick=()=>fill(PROMPTS.find(value=>value.id===button.dataset.chatPrompt).question));
  }
  const inline=value=>esc(value).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>');
  function markdown(text){
    const lines=String(text).split('\n');let result='',list=false;
    const closeList=()=>{if(list){result+='</ul>';list=false;}};
    for(let index=0;index<lines.length;index++){
      const line=lines[index];
      if(line.includes('|')&&index+1<lines.length&&/^\s*\|?[\s:-]+(\|[\s:-]+)+\|?\s*$/.test(lines[index+1])){
        closeList();const cells=value=>value.replace(/^\s*\||\|\s*$/g,'').split('|').map(cell=>cell.trim());
        const headings=cells(line);result+='<div class="table-wrap"><table><thead><tr>'+headings.map(cell=>'<th>'+inline(cell)+'</th>').join('')+'</tr></thead><tbody>';index+=2;
        while(index<lines.length&&lines[index].includes('|')){result+='<tr>'+cells(lines[index]).map(cell=>'<td>'+inline(cell)+'</td>').join('')+'</tr>';index++;}
        index--;result+='</tbody></table></div>';continue;
      }
      const heading=line.match(/^#{1,4}\s+(.+)$/),bullet=line.match(/^\s*[-*]\s+(.+)$/);
      if(bullet){if(!list){result+='<ul>';list=true;}result+='<li>'+inline(bullet[1])+'</li>';continue;}
      closeList();if(heading)result+='<h4>'+inline(heading[1])+'</h4>';else if(line.trim())result+='<p>'+inline(line)+'</p>';
    }
    closeList();return result;
  }
  function sourceLabel(url){try{return new URL(url).hostname.replace(/^www\./,'');}catch(_){return '查看来源';}}
  function followups(data){
    if(data.execution_mode==='local_help')return [{label:'从身份核查开始',question:PROMPTS[0].question},{label:'试试官能团分析',question:PROMPTS[1].question}];
    if(data.entity_ids?.length!==1)return [];
    const id=data.entity_ids[0];if(!/^EC\d{4}$/.test(id))return [];
    const completed=new Set((data.workflow_runs||[]).flatMap(value=>value.workflows||[]));
    const values=[];
    if(!completed.has('neighbors'))values.push({label:'再找结构相似物',question:`查找 ${id} 在全目录中的结构相似物，列出相似度和已有结构簇。`});
    if(!completed.has('ecology'))values.push({label:'查看生态证据',question:`整理 ${id} 已收录的生态证据，区分基准类型并列出缺口。`});
    values.push({label:'核查身份与来源',question:`核查 ${id} 的名称、CAS、选定结构与来源，说明人工核查状态。`});
    values.push({label:'整理待补资料',question:`整理 ${id} 的身份与生态证据还缺哪些资料。`});
    return values.slice(0,3);
  }
  function evidenceMarkup(data){
    const runs=data.workflow_runs||[],trace=runs.flatMap(run=>(run.trace||[]).map(step=>({...step,entity_id:run.entity_id}))),urls=[...new Set(data.source_urls||[])];
    let markup='';
    if(urls.length)markup+=`<details class="chat-evidence"><summary>来源链接 · ${urls.length}</summary><div class="chat-source-list">${urls.map(url=>sourceLink(url,sourceLabel(url))).join('')}</div></details>`;
    if(trace.length)markup+=`<details class="chat-evidence"><summary>工具调用记录 · ${trace.length} 步</summary>${resultTable(['工具','对象','状态','耗时'],trace.map(step=>`<tr><td>${esc(toolLabels[step.tool]||step.tool)}</td><td>${esc(step.entity_id||'—')}</td><td>${esc(statusName(step.status))}</td><td>${step.elapsed_ms} ms</td></tr>`))}<p class="source">${runs.map(run=>esc(run.entity_id||'待选择对象')+' · '+esc(run.run_id)).join('<br>')}</p></details>`;
    const pending=runs.find(run=>run.status==='needs_entity_selection');
    if(pending?.selection?.choices?.length)markup+=`<div class="chat-choice-list">${pending.selection.choices.map(choice=>`<button data-chat-choice="${esc(choice.entity_id)}">${esc(choice.entity_id)} · ${esc(choice.name)} <span class="muted">${esc(choice.cas||'CAS 未填写')}</span></button>`).join('')}</div>`;
    return markup;
  }
  function drawMessages(){
    const value=session();$('#chat-welcome').classList.toggle('hidden',value.messages.length>0);
    $('#answer').innerHTML=value.messages.map(message=>{
      if(message.role==='user')return `<article class="chat-message user"><div class="chat-user-content">${esc(message.text)}</div></article>`;
      const header=`<div class="chat-assistant-header"><span class="chat-avatar">研</span><b>研究助手</b><time>${timeLabel(message.createdAt)}</time></div>`;
      if(message.id===pendingId)return `<article class="chat-message assistant">${header}<div class="chat-assistant-content"><div class="chat-pending"><span class="chat-pending-dot"></span><span id="chat-pending-text">请求已提交，等待模型与工具返回…</span></div></div></article>`;
      if(message.error)return `<article class="chat-message assistant">${header}<div class="chat-assistant-content"><div class="chat-error">${esc(message.text)}<br><button data-chat-retry="${esc(message.id)}">重新填写问题</button><button data-chat-settings>检查模型设置</button></div></div></article>`;
      const data=message.data||{},next=followups(data);
      return `<article class="chat-message assistant">${header}<div class="chat-assistant-content">${data.execution_mode==='local_help'?'<div class="chat-response-note">本地使用帮助 · 未调用模型</div>':data.execution_mode==='deterministic_fallback'?'<div class="chat-response-note">模型解释未通过校验，以下为已执行工具的结果整理。</div>':data.execution_mode==='directory_result'?'<div class="chat-response-note">目录检索结果</div>':''}${markdown(message.text)}${data.missing_inputs?.length?`<div class="chat-response-gap"><strong>需要补充的资料</strong><ul>${data.missing_inputs.map(item=>'<li>'+esc(item)+'</li>').join('')}</ul></div>`:''}${evidenceMarkup(data)}<div class="chat-message-actions"><button data-chat-copy="${esc(message.id)}">复制回答</button><button data-chat-download="${esc(message.id)}" id="${message.id===value.messages[value.messages.length-1]?.id?'download-answer':'download-'+esc(message.id)}">导出本轮记录</button></div>${next.length?`<div class="chat-followups"><span>可以继续问</span>${next.map((item,index)=>`<button data-chat-followup="${esc(message.id)}" data-followup-index="${index}">${esc(item.label)} →</button>`).join('')}</div>`:''}</div></article>`;
    }).join('');
    document.querySelectorAll('[data-chat-copy]').forEach(button=>button.onclick=async()=>{
      const message=value.messages.find(item=>item.id===button.dataset.chatCopy);
      try{await navigator.clipboard.writeText(message.text);button.textContent='已复制';}catch(_){$('#chat-live-status').textContent='浏览器未允许复制，可选中回答文本复制。';}
    });
    document.querySelectorAll('[data-chat-download]').forEach(button=>button.onclick=()=>{
      const message=value.messages.find(item=>item.id===button.dataset.chatDownload);
      jsonDownload(rawResults.get(message.id)||message.data,'research-answer.json');
    });
    document.querySelectorAll('[data-chat-followup]').forEach(button=>button.onclick=()=>{
      const message=value.messages.find(item=>item.id===button.dataset.chatFollowup);fill(followups(message.data)[Number(button.dataset.followupIndex)].question);
    });
    document.querySelectorAll('[data-chat-retry]').forEach(button=>button.onclick=()=>fill(value.messages.find(item=>item.id===button.dataset.chatRetry).question));
    document.querySelectorAll('[data-chat-settings]').forEach(button=>button.onclick=()=>switchTab('settings'));
    document.querySelectorAll('[data-chat-choice]').forEach(button=>button.onclick=()=>{
      const pending=value.messages.flatMap(message=>message.data?.workflow_runs||[]).find(run=>run.status==='needs_entity_selection'&&run.selection?.choices?.some(choice=>choice.entity_id===button.dataset.chatChoice));
      const choice=pending.selection.choices.find(item=>item.entity_id===button.dataset.chatChoice);value.entity={id:choice.entity_id,name:choice.name,cas:choice.cas};
      fill(`请分析 ${choice.entity_id} 的${(pending.workflows||[]).map(item=>workflowNames[item]).join('、')||'结构与生态证据'}。`);persist();drawContext();
    });
  }
  function drawContext(){
    const entity=session().entity;$('#chat-context').classList.toggle('hidden',!entity);
    $('#chat-context-name').textContent=entity?`${entity.id} · ${entity.name||''}${entity.cas?' · CAS '+entity.cas:''}`:'';
  }
  function resizeInput(){const input=$('#question');input.style.height='auto';input.style.height=Math.min(180,Math.max(66,input.scrollHeight))+'px';$('#chat-char-count').textContent=input.value.length+'/2000';}
  function sync(){
    $('#ask').disabled=busy||!environmentReady||!$('#question').value.trim();
    $('#ask').textContent=busy?'等待结果…':'发送 ↑';
    $('#chat-model-needed').classList.toggle('hidden',!!modelConfig?.configured);
    $('#chat-connection').classList.toggle('ready',!!modelConfig?.configured&&environmentReady);
    $('#chat-connection').textContent=!environmentReady?'环境检查中':modelConfig?.configured?modelConfig.model:'模型未配置';
    ['chat-new','chat-mobile-new','chat-clear-history'].forEach(id=>$('#'+id).disabled=busy);
    resizeInput();
  }
  function render(){drawSessions();drawPrompts();drawMessages();drawContext();$('#question').value=session().draft||'';sync();}
  function fill(question){if(busy)return;$('#question').value=question;session().draft=question;persist();sync();$('#question').focus();}
  function scrollToLatest(){const scroll=$('#chat-scroll');scroll.scrollTo({top:scroll.scrollHeight,behavior:'instant'});}
  function newConversation(){
    if(busy)return;session().draft=$('#question').value;
    const empty=sessions.find(value=>!value.messages.length);
    if(empty)activeId=empty.id;else{const value=freshSession();sessions.unshift(value);activeId=value.id;}
    persist();render();$('#question').focus();
  }
  function updateEntity(value,data){
    if((data.workflow_runs||[]).some(run=>run.status==='needs_entity_selection')){value.entity=null;return;}
    if(data.execution_mode==='local_help')return;
    if(data.entity_ids?.length!==1){value.entity=null;return;}
    const run=(data.workflow_runs||[]).find(item=>item.entity_id===data.entity_ids[0]);
    if(run){const identity=run.results?.identity||{};value.entity={id:run.entity_id,name:identity.name||'',cas:identity.cas||''};}
  }
  async function send(){
    const question=$('#question').value.trim();if(!question||busy||!environmentReady)return;
    const value=session();busy=true;pendingSince=Date.now();pendingId=uid();
    value.messages.push({id:uid(),role:'user',text:question,createdAt:Date.now()},
      {id:pendingId,role:'assistant',text:'',createdAt:Date.now()});
    if(value.title==='新对话')value.title=question.slice(0,24)+(question.length>24?'…':'');
    value.updatedAt=Date.now();value.draft='';$('#question').value='';$('#chat-live-status').textContent='请求已提交';render();scrollToLatest();
    const message=value.messages[value.messages.length-1];
    const interval=setInterval(()=>{if($('#chat-pending-text'))$('#chat-pending-text').textContent=`等待模型与工具返回 · 已用时 ${Math.floor((Date.now()-pendingSince)/1000)} 秒`;},1000);
    try{
      const payload={question};if(value.entity?.id)payload.context_entity_id=value.entity.id;
      const data=await post('/api/ask',payload);message.text=data.answer;message.data=data;
      rawResults.set(message.id,data);updateEntity(value,data);$('#chat-live-status').textContent=data.execution_mode==='local_help'?'本地回复已完成':data.execution_mode==='directory_result'?'目录检索已完成':data.execution_mode==='deterministic_fallback'?'模型解释未通过校验，已展示实际工具结果':'本轮问答已完成';
    }catch(error){message.text=error.message;message.error=true;message.question=question;$('#chat-live-status').textContent='本轮请求未完成';}
    finally{clearInterval(interval);busy=false;pendingId=null;value.updatedAt=Date.now();persist();render();scrollToLatest();}
  }

  loadHistory();
  $('#chat-form').onsubmit=event=>{event.preventDefault();send();};
  $('#question').oninput=()=>{session().draft=$('#question').value;sync();};
  $('#question').onblur=persist;
  $('#question').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();send();}};
  $('#chat-new').onclick=newConversation;$('#chat-mobile-new').onclick=newConversation;
  $('#chat-clear-history').onclick=()=>{if(busy)return;sessions=[freshSession()];activeId=sessions[0].id;rawResults.clear();try{localStorage.removeItem(STORE);}catch(_){}render();$('#chat-live-status').textContent='本机对话记录已清除';};
  $('#chat-export').onclick=()=>jsonDownload({type:'research_conversation',exported_at:new Date().toISOString(),...session(),messages:session().messages.map(message=>({...message,data:savedResponse(message.data)}))},'research-conversation.json');
  $('#chat-reset-context').onclick=()=>{session().entity=null;persist();drawContext();};
  $('#chat-model-settings').onclick=()=>switchTab('settings');$('#chat-help-example').onclick=()=>fill(PROMPTS[1].question);
  document.querySelectorAll('[data-chat-filter]').forEach(button=>button.onclick=()=>{filter=button.dataset.chatFilter;drawPrompts();});
  document.querySelectorAll('[data-chat-example]').forEach(button=>button.onclick=()=>fill(`请分析 ${button.dataset.chatExample} 的结构、官能团和已有生态证据。`));
  window.chatUI={sync,refresh:()=>{sync();},newConversation};render();
})();
