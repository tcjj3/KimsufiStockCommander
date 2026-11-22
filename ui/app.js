(() => {
'use strict';

const $ = s => document.querySelector(s);
const state = { data:null, analytics:null, replay:null, portfolioReplay:null, optimizer:null, filter:{ram:new Set(),disk:new Set()}, ui:{settingsInitialized:false,dirtySections:new Set(),stateRefresh:null,analyticsRefresh:null,lastStateError:'',pendingTargets:new Map()}, auth:{required:null,authenticated:false,token:'',waitPromise:null,waitResolve:null,dialogMandatory:false,storedTokenInvalid:false} };
const diskLabels = {nvme:'NVMe',ssd:'SATA SSD',hdd:'HDD',other:'Other'};
const notificationEventLabels = {
  AVAILABLE:'库存出现', DC_CHANGE:'机房变化', SOLD_OUT:'售罄', ORDER_SUCCESS:'下单成功',
  ORDER_MISS:'库存竞争失败', ORDER_REVIEW:'需要复核', BUDGET_BLOCKED:'预算阻止',
  PRICE_BLOCKED:'付款策略阻止', CREDENTIALS_MISSING:'OVH 凭证缺失', POLL_ERROR:'轮询错误', HEARTBEAT:'心跳'
};

function storedApiToken(){
  return sessionStorage.getItem('kscApiToken') || localStorage.getItem('kscApiToken') || '';
}
function setStoredApiToken(token,remember){
  sessionStorage.removeItem('kscApiToken'); localStorage.removeItem('kscApiToken');
  if(!token)return;
  (remember?localStorage:sessionStorage).setItem('kscApiToken',token);
}
function hasStoredApiToken(){return !!storedApiToken()}
function setAuthMessage(text='',kind='status'){
  const el=$('#authMessage'); if(!el)return;
  el.textContent=text; el.classList.toggle('error',kind==='error'); el.classList.toggle('success',kind==='success');
}
function syncAuthConnectButton(){
  const b=$('#authConnect'),i=$('#apiTokenInput'); if(!b||!i)return;
  b.disabled=!i.value.trim();
}
function renderAuthDialogState(message=''){
  const authenticated=!!state.auth.authenticated,stored=hasStoredApiToken();
  const connect=$('#authConnect'),forget=$('#authForget'),remember=$('#rememberApiToken');
  if(connect)connect.textContent=authenticated?'更换 Token':(state.auth.storedTokenInvalid?'重新认证':'连接');
  if(forget){
    forget.hidden=!authenticated&&!stored;
    forget.textContent=authenticated?'注销并清除本地 Token':'清除已保存 Token';
  }
  if(remember)remember.checked=!!localStorage.getItem('kscApiToken');
  if(message)setAuthMessage(message);
  else if(authenticated)setAuthMessage('当前已认证。可输入新 Token 更换认证，或直接关闭此窗口。','success');
  else if(state.auth.storedTokenInvalid)setAuthMessage('已保存的 Token 无效或服务器 Token 已变更。请输入新 Token，或清除已保存 Token。','error');
  else setAuthMessage('当前未认证，浏览器未保存 Token。请输入 Commander API Token。');
  syncAuthConnectButton();
}
function authHeaders(token=state.auth.token){ return token?{'Authorization':`Bearer ${token}`}:{ }; }
function updateAuthUi(){
  const b=$('#authStatusBtn'); if(!b)return;
  if(state.auth.required===false){b.textContent='🔓 API 鉴权未启用';b.classList.remove('auth-ok','auth-bad');return}
  if(state.auth.authenticated){b.textContent='🔐 API 已认证';b.classList.add('auth-ok');b.classList.remove('auth-bad')}
  else{b.textContent='🔒 API 未认证';b.classList.add('auth-bad');b.classList.remove('auth-ok')}
}
function beginAuthWait(){
  if(!state.auth.waitPromise)state.auth.waitPromise=new Promise(resolve=>{state.auth.waitResolve=resolve});
  return state.auth.waitPromise;
}
function finishAuthWait(){
  const r=state.auth.waitResolve;state.auth.waitResolve=null;state.auth.waitPromise=null;if(r)r(true);
}
function setAuthDialogMandatory(mandatory){
  state.auth.dialogMandatory=!!mandatory;
  const close=$('#authClose'),cancel=$('#authCancel');
  if(close)close.hidden=state.auth.dialogMandatory;
  if(cancel)cancel.hidden=state.auth.dialogMandatory;
}
function closeAuthDialog(){
  if(state.auth.dialogMandatory)return false;
  const d=$('#authDialog'); if(d?.open)d.close();
  return true;
}
function showAuthDialog(message='',mandatory=false){
  const d=$('#authDialog'); if(!d)return;
  setAuthDialogMandatory(mandatory);
  const input=$('#apiTokenInput'),toggle=$('#authTokenToggle');
  if(input)input.type='password';
  if(toggle){toggle.textContent='👁';toggle.setAttribute('aria-label','显示 Token');toggle.title='显示 Token'}
  renderAuthDialogState(message);
  $('#authHttpWarning').textContent=(location.protocol==='http:'&&!['127.0.0.1','localhost','::1'].includes(location.hostname))?'⚠ 当前使用 HTTP，Token 会以明文经过网络；建议仅用于可信 LAN/VPN，跨不可信网络请使用 HTTPS/WireGuard/Tailscale。':'';
  if(!d.open)d.showModal();
  setTimeout(()=>$('#apiTokenInput')?.focus(),0);
}
async function validateApiToken(token){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
  try{
    const r=await fetch('/api/auth/check',{headers:{...authHeaders(token)},signal:controller.signal,cache:'no-store'});
    const data=await r.json().catch(()=>({}));
    return {ok:r.ok&&data.authenticated===true,data,status:r.status};
  }catch(e){return {ok:false,error:e};}finally{clearTimeout(timer)}
}
async function initAuth(){
  const r=await fetch('/api/health',{cache:'no-store'}); const h=await r.json();
  state.auth.required=!!h.authRequired;
  if(!state.auth.required){state.auth.authenticated=true;state.auth.token='';updateAuthUi();return true}
  const token=storedApiToken();
  state.auth.storedTokenInvalid=false;
  if(token){
    const v=await validateApiToken(token);
    if(v.ok){state.auth.token=token;state.auth.authenticated=true;state.auth.storedTokenInvalid=false;updateAuthUi();return true}
    state.auth.storedTokenInvalid=true;
  }
  state.auth.authenticated=false;state.auth.token='';updateAuthUi();beginAuthWait();showAuthDialog('',true);
  await state.auth.waitPromise;return true;
}
function handleUnauthorized(){
  state.auth.required=true;state.auth.authenticated=false;state.auth.token='';state.auth.storedTokenInvalid=hasStoredApiToken();updateAuthUi();beginAuthWait();showAuthDialog('',true);
}
async function api(path, method='GET', body=null, timeoutMs=30000){
  if(state.auth.required&&!state.auth.authenticated)await beginAuthWait();
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeoutMs);
  try{
    const headers={'Content-Type':'application/json',...authHeaders()};
    const r=await fetch(path,{method,headers,body:body?JSON.stringify(body):null,signal:controller.signal,cache:'no-store'});
    const data=await r.json().catch(()=>({}));
    if(r.status===401){handleUnauthorized();throw new Error('API 未认证')}
    if(!r.ok)throw new Error(data.error||`${r.status} ${r.statusText}`);
    return data;
  }catch(e){
    if(e?.name==='AbortError')throw new Error(`${path} 请求超时（${Math.round(timeoutMs/1000)}s）`);
    throw e;
  }finally{clearTimeout(timer)}
}
async function apiBlob(path,timeoutMs=30000){
  if(state.auth.required&&!state.auth.authenticated)await beginAuthWait();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
  try{
    const r=await fetch(path,{headers:{...authHeaders()},signal:controller.signal,cache:'no-store'});
    if(r.status===401){handleUnauthorized();throw new Error('API 未认证')}
    if(!r.ok){const data=await r.json().catch(()=>({}));throw new Error(data.error||`${r.status} ${r.statusText}`)}
    return await r.blob();
  }finally{clearTimeout(timer)}
}

function applySavedSettings(result,section){
  if(result?.settings&&state.data)state.data.settings=result.settings;
  if(section==='runtime')clearSettingsDirty();else clearSectionDirty(section);
  renderSettings(false);
}
function toast(msg){
  const el=$('#toast'); el.textContent=msg; el.classList.add('show');
  clearTimeout(el._t); el._t=setTimeout(()=>el.classList.remove('show'),3500);
}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function n(v,d=0){const x=Number(v);return Number.isFinite(x)?x:d}
function fmtTime(s){if(!s)return '—';try{return new Date(s).toLocaleString()}catch{return s}}
function fmtDuration(seconds){
  if(seconds===null||seconds===undefined||!Number.isFinite(Number(seconds)))return '—';
  let s=Math.max(0,Math.round(Number(seconds))); const d=Math.floor(s/86400);s%=86400;const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60);s%=60;
  if(d)return `${d}天 ${h}时`; if(h)return `${h}时 ${m}分`; if(m)return `${m}分 ${s}秒`; return `${s}秒`;
}
const weekdayNames=['周一','周二','周三','周四','周五','周六','周日'];
function analyticsTargetLabel(id){
  const row=(state.analytics?.targets||[]).find(x=>x.id===id); return row?.label||id||'—';
}
function predictionFor(id){ return (state.analytics?.predictions||[]).find(x=>x.targetId===id)||null; }
function fmtUtcHour(epoch){
  if(!epoch)return '—'; const d=new Date(Number(epoch)*1000);
  return `${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:00Z`;
}
function fmtPercent(v){ return Number.isFinite(Number(v))?`${Number(v).toFixed(1)}%`:'—'; }

function pendingTargetEntry(id){ return state.ui.pendingTargets.get(id)||null; }
function targetById(id){
  const base=state.data?.targets?.[id]||null, pending=pendingTargetEntry(id);
  if(!base&&!pending)return null;
  return Object.assign({},base||{},pending?.patch||{});
}
function targetFor(row){ return targetById(row.id); }
function setTargetPending(id,patch){
  const prev=pendingTargetEntry(id);
  state.ui.pendingTargets.set(id,{patch:Object.assign({},prev?.patch||{},patch||{}),since:prev?.since||Date.now()});
}
function clearTargetPending(id){ state.ui.pendingTargets.delete(id); }
function targetPatchMatches(target,patch){
  if(!target)return false;
  return Object.entries(patch||{}).every(([k,v])=>{
    const actual=target[k];
    if(typeof v==='boolean')return Boolean(actual)===v;
    if(typeof v==='number')return Number(actual)===v;
    return String(actual??'')===String(v??'');
  });
}
function reconcilePendingTargets(fresh){
  if(!fresh?.targets)return;
  for(const [id,p] of [...state.ui.pendingTargets.entries()]){
    if(targetPatchMatches(fresh.targets[id],p.patch)){ state.ui.pendingTargets.delete(id); continue; }
    // A pending edit is a UI overlay, not a permanent fork. If the backend has
    // not acknowledged it within 45s, stop masking server truth and surface it.
    if(Date.now()-p.since>45000){ state.ui.pendingTargets.delete(id); toast(`目标 ${id} 的保存确认超时，已恢复服务器状态`); }
  }
}
function mergedTargets(){
  const out=Object.assign({},state.data?.targets||{});
  for(const [id,p] of state.ui.pendingTargets.entries())out[id]=Object.assign({},out[id]||{},p.patch||{});
  return out;
}
function orderabilityFor(row){
  return (state.data?.orderability||{})[row.id] || {status:'unknown',orderable:null,reason:'尚未扫描 cart options'};
}
function orderabilityHtml(row){
  const o=orderabilityFor(row);
  if(o.status==='orderable')return '<span class="orderability yes">✓ 可下单</span>';
  if(o.status==='availability_only')return `<span class="orderability ghost">⚠ Availability-only</span><div class="muted">cart 缺少 ${esc((o.missingFamilies||[]).join(', ')||'配置 option')}</div>`;
  return '<span class="orderability unknown">○ 待确认</span>';
}

function effectivePayment(row,target){
  if(target && target.inheritPrice===false)return {duration:target.priceDuration||'',mode:target.priceMode||''};
  const d=state.data?.settings?.orderDefaults||{};return {duration:d.priceDuration||'',mode:d.priceMode||''};
}
function durationMonths(duration){
  const m=String(duration||'').toUpperCase().match(/^P(?:(\d+)Y)?(?:(\d+)M)?$/);if(!m)return null;
  const months=(Number(m[1]||0)*12)+Number(m[2]||0);return months>0?months:null;
}
function cachedBillingPrice(row,target){
  const pp=state.data?.paymentProfiles||{}, by=pp.byPlan||{}, rows=by[row.planCode]||[];
  const cfg=effectivePayment(row,target);
  const hit=rows.find(x=>x.duration===cfg.duration&&x.mode===cfg.mode);
  return {cfg,hit,cached:rows.length>0};
}
function billingPreviewHtml(row,target){
  const cfg=effectivePayment(row,target);
  const ord=orderabilityFor(row);
  if(ord.status==='availability_only'){
    const base=cachedBillingPrice(row,target);
    if(base.hit){
      const amount=Number(base.hit.amount||0), cur=base.hit.currency||row.currency||'EUR', months=durationMonths(base.hit.duration);
      const eff=(months&&amount>0)?`<div class="muted">≈ ${esc(cur)} ${(amount/months).toFixed(2)}/mo</div>`:'';
      return `<div class="billing-preview"><strong>${esc(base.hit.price||`${cur} ${amount.toFixed(2)}`)}</strong><div class="muted">${esc(base.hit.duration)} / ${esc(base.hit.mode)}</div>${eff}<div class="price-unsupported">仅基础 Plan；该 availability 配置当前不可下单</div></div>`;
    }
    return '<span class="price-unsupported">Availability-only；无可下单配置周期价</span>';
  }
  const preview=(state.data?.configurationPricePreviews||{})[row.id]||null;
  const same=preview&&preview.duration===cfg.duration&&preview.mode===cfg.mode;
  if(same&&preview.complete){
    const amount=Number(preview.amount||0), cur=preview.currency||row.currency||'EUR', months=durationMonths(preview.duration);
    const eff=(months&&amount>0)?`<div class="muted">≈ ${esc(cur)} ${(amount/months).toFixed(2)}/mo</div>`:'';
    const breakdown=`<div class="muted">Plan ${Number(preview.baseAmount||0).toFixed(2)} + 配置 ${Number(preview.optionAmount||0).toFixed(2)}</div>`;
    return `<div class="billing-preview"><strong>${esc(cur)} ${amount.toFixed(2)}</strong><div class="muted">${esc(preview.duration)} / ${esc(preview.mode)}</div>${eff}${breakdown}<div class="muted">未税 · setup/一次性费另计</div></div>`;
  }
  const base=cachedBillingPrice(row,target);
  if(!base.cached)return '<span class="muted">待读取付款价</span>';
  if(!base.hit)return `<span class="price-unsupported">不支持 ${esc(cfg.duration)}/${esc(cfg.mode)}</span>`;
  const months=durationMonths(base.hit.duration), amount=Number(base.hit.amount||0), cur=base.hit.currency||row.currency||'EUR';
  const eff=(months&&amount>0)?`<div class="muted">≈ ${esc(cur)} ${(amount/months).toFixed(2)}/mo</div>`:'';
  return `<div class="billing-preview"><strong>${esc(base.hit.price||`${cur} ${amount.toFixed(2)}`)}</strong><div class="muted">${esc(base.hit.duration)} / ${esc(base.hit.mode)}</div>${eff}<div class="price-unsupported">仅基础 Plan；配置加价待读取</div></div>`;
}
function monthlyPreview(row){
  return (state.data?.configurationMonthlyPricePreviews||{})[row.id]||null;
}
function configurationMonthlyAmount(row){
  const p=monthlyPreview(row);
  return p&&p.complete&&p.amount!==null&&p.amount!==undefined?Number(p.amount):null;
}
function monthlyPriceHtml(row){
  const ord=orderabilityFor(row);
  if(ord.status==='availability_only'){
    return `<div class="billing-preview"><strong>${esc(row.currency||'EUR')} ${Number(row.price||0).toFixed(2)}</strong>/mo<div class="price-unsupported">仅基础 Plan；availability 配置当前不可下单</div><div class="muted">${esc(ord.reason||'cart option 不存在')}</div></div>`;
  }
  const p=monthlyPreview(row);
  if(p&&p.complete&&p.amount!==null&&p.amount!==undefined){
    const cur=p.currency||row.currency||'EUR', amount=Number(p.amount||0);
    return `<div class="billing-preview"><strong>${esc(cur)} ${amount.toFixed(2)}</strong>/mo<div class="muted">Plan ${Number(p.baseAmount||0).toFixed(2)} + 配置 ${Number(p.optionAmount||0).toFixed(2)}</div><div class="muted">P1M / default · 未税</div></div>`;
  }
  if(p){
    const base=p.baseAmount!==null&&p.baseAmount!==undefined?Number(p.baseAmount):Number(row.price||0);
    return `<div class="billing-preview"><strong>${esc(row.currency||'EUR')} ${base.toFixed(2)}</strong>/mo<div class="price-unsupported">仅基础 Plan；配置月价未知</div><div class="muted">${esc(p.reason||'等待价格扫描')}</div></div>`;
  }
  return `<div class="billing-preview"><strong>${esc(row.currency||'EUR')} ${Number(row.price||0).toFixed(2)}</strong>/mo<div class="price-unsupported">仅基础 Plan；等待价格扫描</div></div>`;
}

function updatePaymentScanButton(){
  const btn=$('#scanPaymentProfiles'); if(!btn||!state.data)return;
  const ready=(state.data.catalog||[]).length>0&&!state.data.runtime?.catalogRefreshing;
  if(btn.dataset.busy==='1')return;
  btn.disabled=!ready;
  btn.textContent=ready?'扫描 OVH 付款组合 + 配置价格':'等待服务器目录…';
}

function strategyDecision(row){
  const st=state.data.settings.dcStrategy, av=(row.datacenters||[]).map(x=>x.toLowerCase());
  const selected=(st.selected||[]).map(x=>x.toLowerCase());
  const priority=(st.priority||[]).map(x=>x.toLowerCase());
  const ordered = xs => [...priority.filter(x=>xs.includes(x)),...xs.filter(x=>!priority.includes(x))];
  if(!av.length)return {dc:'',reason:'缺货'};
  if(st.mode==='any')return {dc:ordered(av)[0],reason:'任意机房'};
  if(st.mode==='fixed')return av.includes(st.fixed)?{dc:st.fixed,reason:'固定机房命中'}:{dc:'',reason:`等待 ${String(st.fixed).toUpperCase()}`};
  if(st.mode==='exclude'){
    const xs=av.filter(x=>!selected.includes(x)); return xs.length?{dc:ordered(xs)[0],reason:'已避开排除机房'}:{dc:'',reason:'仅排除机房有货'};
  }
  if(st.mode==='whitelist'||st.mode==='priority'){
    const xs=ordered(selected).filter(x=>av.includes(x));
    if(xs.length)return {dc:xs[0],reason:'首选命中'};
    if(st.fallback==='any')return {dc:ordered(av)[0],reason:'回退'};
    return {dc:'',reason:'首选无库存'};
  }
  return {dc:'',reason:'策略未命中'};
}

function buildFilterControls(){
  const rows=state.data.catalog||[];
  const rams=[...new Set(rows.map(x=>x.ramGB).filter(x=>Number.isFinite(x)))].sort((a,b)=>a-b);
  $('#ramFilters').innerHTML=rams.map(x=>`<label class="chip"><input type="checkbox" class="ram-filter" value="${x}"> ${x} GB</label>`).join('') || '<span class="muted">暂无内存数据</span>';
  const disks=[...new Set(rows.flatMap(x=>x.diskTypes||[]))];
  $('#diskFilters').innerHTML=disks.map(x=>`<label class="chip"><input type="checkbox" class="disk-filter" value="${esc(x)}"> ${esc(diskLabels[x]||x)}</label>`).join('') || '<span class="muted">暂无硬盘数据</span>';
  document.querySelectorAll('.ram-filter').forEach(x=>{x.checked=state.filter.ram.has(Number(x.value));x.addEventListener('change',()=>{x.checked?state.filter.ram.add(Number(x.value)):state.filter.ram.delete(Number(x.value));renderTable()})});
  document.querySelectorAll('.disk-filter').forEach(x=>{x.checked=state.filter.disk.has(x.value);x.addEventListener('change',()=>{x.checked?state.filter.disk.add(x.value):state.filter.disk.delete(x.value);renderTable()})});
}

function filteredRows(){
  const q=$('#search').value.trim().toLowerCase(), sf=$('#stockFilter').value, of=$('#orderabilityFilter').value;
  const pmin=$('#priceMin').value===''?null:n($('#priceMin').value), pmax=$('#priceMax').value===''?null:n($('#priceMax').value);
  const diskMode=$('#diskMode').value;
  return (state.data.catalog||[]).filter(r=>{
    const text=`${r.name} ${r.planCode} ${r.memory} ${r.storage} ${r.diskLabel}`.toLowerCase();
    if(q&&!text.includes(q))return false;
    const isAvail=r.status==='available' && (r.datacenters||[]).length>0;
    if(sf==='available'&&!isAvail)return false;
    if(sf==='unavailable'&&isAvail)return false;
    const ord=orderabilityFor(r);
    if(of!=='all'&&ord.status!==of)return false;
    if(state.filter.ram.size && !state.filter.ram.has(r.ramGB))return false;
    if(state.filter.disk.size){
      const types=r.diskTypes||[], chosen=[...state.filter.disk];
      const ok=diskMode==='all'?chosen.every(x=>types.includes(x)):chosen.some(x=>types.includes(x));
      if(!ok)return false;
    }
    const cfgMonthly=configurationMonthlyAmount(r);
    if((pmin!==null||pmax!==null)&&cfgMonthly===null)return false;
    const priceForFilter=cfgMonthly;
    if(pmin!==null&&priceForFilter<pmin)return false;
    if(pmax!==null&&priceForFilter>pmax)return false;
    return true;
  });
}

function renderTable(){
  if(!state.data)return;
  const rows=filteredRows(), cap=n(state.data.settings.orderDefaults.catalogMonthlyPriceCap,0);
  $('#serverRows').innerHTML=rows.map(r=>{
    const t=targetFor(r), d=strategyDecision(r), cfgMonthly=configurationMonthlyAmount(r), ord=orderabilityFor(r);
    const priceKnown=cfgMonthly!==null;
    const priceOk=!cap||(priceKnown&&cfgMonthly<=cap);
    const canOrder=ord.status!=='availability_only';
    const eligible=d.dc&&priceOk&&canOrder;
    const review=t?.review;
    const status=review?'REVIEW':(t?.completedQty>=t?.desiredQty&&t?.autoOrder?'完成':(t?.lastState||'—'));
    return `<tr>
      <td><strong>${esc(r.name)}</strong><div class="mono muted">${esc(r.planCode)}</div></td>
      <td>${esc(r.memoryLabel||r.memory||'—')}<div class="mono muted">${esc(r.memory||'')}</div></td>
      <td>${esc(r.diskLabel||r.storage||'—')}<div class="mono muted">${esc(r.storage||'')}</div></td>
      <td>${monthlyPriceHtml(r)}</td>
      <td>${t?(t.inheritPrice!==false?`继承 ${esc(state.data.settings.orderDefaults.priceDuration)}/${esc(state.data.settings.orderDefaults.priceMode)}`:`${esc(t.priceDuration)}/${esc(t.priceMode)}`):`全局 ${esc(state.data.settings.orderDefaults.priceDuration)}/${esc(state.data.settings.orderDefaults.priceMode)}`}${t?.priceValidation==='blocked'?'<div class="decision review">周期不支持</div>':''}</td>
      <td>${billingPreviewHtml(r,t)}</td>
      <td>${(r.datacenters||[]).length?`<span class="stock yes">● ${(r.datacenters||[]).map(x=>x.toUpperCase()).join(', ')}</span>`:'<span class="stock no">○ Sold out</span>'}</td>
      <td>${orderabilityHtml(r)}</td>
      <td class="decision ${eligible?'yes':'no'}">${eligible?'✓ '+d.dc.toUpperCase():'○ '+esc(!canOrder?'availability-only，不参与自动下单':(!priceOk?(priceKnown?'超出配置月价上限':'配置月价未知'):d.reason))}</td>
      <td>${(()=>{const p=predictionFor(r.id);return p?`<span class="watch-pill band-${esc(p.band)}">${Number(p.watchIndex).toFixed(1)}</span><div class="muted">置信 ${Number(p.confidence).toFixed(0)}%</div>`:'—'})()}</td>
      <td><input type="checkbox" class="watch" data-id="${esc(r.id)}" ${t?.watch?'checked':''}></td>
      <td><input type="checkbox" class="auto" data-id="${esc(r.id)}" ${t?.autoOrder?'checked':''} ${(review||!canOrder)?'disabled':''}></td>
      <td><input type="number" class="priority-input priority" data-id="${esc(r.id)}" min="0" max="100" value="${t?.orderPriority??50}" ${t?'':'disabled'}></td>
      <td><input type="number" class="qty" data-id="${esc(r.id)}" min="1" max="50" value="${t?.desiredQty||1}" ${t?'':'disabled'}> / ${t?.completedQty||0}</td>
      <td>${t?.budgetReserveAmount!=null?`${esc(t.budgetCurrency||state.data.budget.currency)} ${Number(t.budgetReserveAmount).toFixed(2)}`:'—'}${t?.budgetValidation==='blocked'?`<div class="budget-block">${esc(t.budgetValidationReason||'预算阻止')}</div>`:''}</td>
      <td class="decision ${review?'review':''}">${esc(status)}${ord.status==='availability_only'?'<div class="ghost-note">Availability-only</div>':(t?.optionResolutionError?'<div class="muted">下单选项待确认</div>':'')}</td>
      <td><button class="advanced" data-id="${esc(r.id)}" ${t?'':'disabled'}>设置</button> <button class="diagnose" data-id="${esc(r.id)}">诊断</button></td>
    </tr>`;
  }).join('');
  const targetView=mergedTargets();
  const watched=Object.values(targetView).filter(x=>x.watch).length;
  const autos=Object.values(targetView).filter(x=>x.autoOrder).length;
  const wanted=Object.values(targetView).filter(x=>x.autoOrder).reduce((a,x)=>a+n(x.desiredQty,1),0);
  const b=state.data.budget||{};
  $('#stats').innerHTML=`<div class="stat">匹配 <strong>${rows.length}</strong> 个配置</div><div class="stat">监控 <strong>${watched}</strong></div><div class="stat">自动下单目标 <strong>${autos}</strong></div><div class="stat">目标总数 <strong>${wanted}</strong> 台</div><div class="stat">预算占用 <strong>${esc(b.currency||'')} ${Number(b.usedReserved||0).toFixed(2)}</strong></div>`;

  document.querySelectorAll('.watch').forEach(el=>el.addEventListener('change',()=>toggleTarget(el.dataset.id,'watch',el.checked)));
  document.querySelectorAll('.auto').forEach(el=>el.addEventListener('change',()=>toggleTarget(el.dataset.id,'autoOrder',el.checked)));
  document.querySelectorAll('.qty').forEach(el=>el.addEventListener('change',()=>updateQty(el.dataset.id,el.value)));
  document.querySelectorAll('.priority').forEach(el=>el.addEventListener('change',()=>updatePriority(el.dataset.id,el.value)));
  document.querySelectorAll('.advanced').forEach(el=>el.addEventListener('click',()=>openAdvanced(el.dataset.id)));
  document.querySelectorAll('.diagnose').forEach(el=>el.addEventListener('click',()=>openDiagnostics(el.dataset.id)));
}
async function openDiagnostics(id){
  const row=state.data.catalog.find(x=>x.id===id);
  $('#diagnosticTitle').textContent=`价格 / Option 诊断 · ${row?.name||id}`;
  $('#diagnosticText').textContent='读取中…'; $('#diagnosticDialog').showModal();
  try{const r=await api('/api/pricing/diagnostics','POST',{id,force:false});$('#diagnosticText').textContent=JSON.stringify(r,null,2)}catch(e){$('#diagnosticText').textContent='诊断读取失败：'+e.message}
}

async function toggleTarget(id,field,value){
  const row=state.data.catalog.find(x=>x.id===id); if(!row)return;
  const t=targetFor(row);
  const payload={planCode:row.planCode,name:row.name,memory:row.memory,storage:row.storage,price:row.price};
  payload.watch=t?.watch||false; payload.autoOrder=t?.autoOrder||false; payload.desiredQty=t?.desiredQty||1;
  payload[field]=value;
  if(field==='autoOrder'&&value)payload.watch=true;
  if(field==='watch'&&!value)payload.autoOrder=false;
  const optimistic={watch:Boolean(payload.watch),autoOrder:Boolean(payload.autoOrder),desiredQty:Number(payload.desiredQty||1)};
  setTargetPending(id,optimistic); renderTable();
  try{
    const result=await api('/api/target','POST',payload);
    if(result?.target)state.data.targets[id]=result.target;
    await refreshState(false);
    toast(field==='watch'?'监控设置已更新':'自动下单目标已更新');
  }catch(e){
    clearTargetPending(id); toast(e.message); await refreshState(false);
  }
}
async function updateQty(id,value){
  const row=state.data.catalog.find(x=>x.id===id), t=targetFor(row); if(!row||!t)return;
  const desiredQty=Math.max(1,Math.min(50,Math.floor(n(value,1))));
  setTargetPending(id,{desiredQty}); renderTable();
  try{
    const result=await api('/api/target','POST',{planCode:row.planCode,name:row.name,memory:row.memory,storage:row.storage,price:row.price,watch:t.watch,autoOrder:t.autoOrder,desiredQty});
    if(result?.target)state.data.targets[id]=result.target;
    await refreshState(false);
  }catch(e){clearTargetPending(id);toast(e.message);await refreshState(false)}
}
async function updatePriority(id,value){
  const row=state.data.catalog.find(x=>x.id===id), t=targetFor(row); if(!row||!t)return;
  const p=Math.max(0,Math.min(100,Math.floor(n(value,50))));
  setTargetPending(id,{orderPriority:p}); renderTable();
  try{
    const result=await api('/api/target','POST',{planCode:row.planCode,name:row.name,memory:row.memory,storage:row.storage,price:row.price,watch:t.watch,autoOrder:t.autoOrder,desiredQty:t.desiredQty,orderPriority:p,budgetReserveOverride:t.budgetReserveOverride||0});
    if(result?.target)state.data.targets[id]=result.target;
    await refreshState(false);
  }catch(e){clearTargetPending(id);toast(e.message);await refreshState(false)}
}

function renderStatus(){
  const d=state.data, rt=d.runtime, s=d.settings;
  const arm=$('#globalArm'); arm.checked=!!s.autoOrderArmed; arm.closest('.arm-switch').classList.toggle('armed',arm.checked);
  const bp=s.budgetPolicy||{}, autoPay=!!s.orderDefaults?.autoPay;
  const finiteBudgetSafe=!autoPay||bp.requireFiniteForAutoPay===false||(!!bp.enabled&&Number(bp.totalLimit||0)>0);
  $('#statusStrip').innerHTML=[
    `<div class="badge ${rt.cliFound?'good':'bad'}">CLI ${rt.cliFound?'✓':'✕'}</div>`,
    `<div class="badge ${rt.authReady?'good':'warn'}">OVH 凭证 ${rt.authReady?'✓':'缺 '+rt.missingCredentials.join(', ')}</div>`,
    `<div class="badge ${rt.catalogRefreshing?'warn':'good'}">目录 ${rt.catalogRefreshing?'刷新中':'已缓存'}</div>`,
    `<div class="badge">目录时间 ${esc(fmtTime(rt.catalogUpdatedAt))}</div>`,
    `<div class="badge">最近轮询 ${esc(fmtTime(rt.lastPollAt))}</div>`,
    autoPay?`<div class="badge ${finiteBudgetSafe?'good':'bad'}">AutoPay 预算 ${finiteBudgetSafe?'✓':'未设置有限总预算'}</div>`:'',
    s.notifications?.telegramEnabled?`<div class="badge ${rt.telegramTokenReady?'good':'warn'}">Telegram Token ${rt.telegramTokenReady?'✓':'未提供'}</div>`:'',
    rt.notificationLastError?`<div class="badge bad">通知错误：${esc(rt.notificationLastError)}</div>`:'',
    d.demo?'<div class="badge warn">DEMO：下单被后端阻止</div>':''
  ].join('');
  updatePaymentScanButton();
}
function sectionDirty(section){ return state.ui.dirtySections.has(section); }
function dirtyHintId(section){
  return ({runtime:'settingsDirtyHint',budget:'budgetDirtyHint',notifications:'notificationDirtyHint',strategy:'strategyDirtyHint'})[section]||'';
}
function updateDirtyHint(section){
  const id=dirtyHintId(section); if(!id)return;
  const h=$('#'+id); if(!h)return;
  if(sectionDirty(section)){
    h.textContent=section==='strategy'?'● 正在保存…':'● 有未保存修改';
    h.classList.add('settings-dirty');
  }else{
    h.textContent=''; h.classList.remove('settings-dirty');
  }
}
function markSectionDirty(section){ state.ui.dirtySections.add(section); updateDirtyHint(section); }
function clearSectionDirty(section){ state.ui.dirtySections.delete(section); updateDirtyHint(section); }
function markSettingsDirty(){ markSectionDirty('runtime'); }
function clearSettingsDirty(){ clearSectionDirty('runtime'); }

function renderSettings(force=false){
  const s=state.data.settings, st=s.dcStrategy, dcs=state.data.runtime.knownDatacenters||[];
  const initial=!state.ui.settingsInitialized;

  if(force||initial||!sectionDirty('runtime')){
    $('#exePath').value=s.exe||''; $('#endpoint').value=s.endpoint; $('#country').value=s.country;
    $('#pollInterval').value=s.pollIntervalSeconds; $('#catalogInterval').value=s.catalogRefreshSeconds;
    $('#priceDuration').value=s.orderDefaults.priceDuration||''; $('#priceMode').value=s.orderDefaults.priceMode||'';
    renderGlobalPaymentProfiles(s.orderDefaults.priceDuration||'',s.orderDefaults.priceMode||'');
    syncPaymentPresetUi(false);
    $('#autoPay').checked=!!s.orderDefaults.autoPay; $('#orderPriceCap').value=s.orderDefaults.catalogMonthlyPriceCap??'';
  }

  const bp=s.budgetPolicy||{};
  if(force||initial||!sectionDirty('budget')){
    $('#budgetEnabled').checked=!!bp.enabled; $('#budgetCurrency').value=bp.currency||'EUR';
    $('#budgetTotal').value=bp.totalLimit??0; $('#budgetBaseline').value=bp.baselineReserved??0;
    $('#budgetPerOrderCap').value=bp.perOrderReserveCap??0; $('#budgetBufferPercent').value=bp.bufferPercent??10;
    $('#budgetBufferFixed').value=bp.bufferFixed??0;
  }
  renderBudgetSummary();

  const nt=s.notifications||{};
  if(force||initial||!sectionDirty('notifications')){
    $('#notifyEnabled').checked=nt.enabled!==false; $('#notifyDesktop').checked=nt.desktop!==false;
    $('#telegramEnabled').checked=!!nt.telegramEnabled; $('#telegramChatId').value=nt.telegramChatId||'';
    $('#telegramTokenEnv').value=nt.telegramTokenEnv||'KSC_TELEGRAM_BOT_TOKEN'; $('#heartbeatHours').value=nt.heartbeatHours??6;
    renderNotificationControls();
  }
  renderNotificationSummary();

  if(force||initial||!sectionDirty('strategy')){
    $('#dcMode').value=st.mode; $('#fallback').value=st.fallback;
    $('#fixedDc').innerHTML=dcs.map(x=>`<option value="${esc(x)}">${esc(x.toUpperCase())}</option>`).join('');
    $('#fixedDc').value=st.fixed;
    renderDcControls();
  }

  state.ui.settingsInitialized=true;
}
function renderBudgetSummary(){
  const b=state.data.budget||{}, rem=b.remaining==null?'不限':`${esc(b.currency)} ${Number(b.remaining).toFixed(2)}`;
  const bp=state.data.settings?.budgetPolicy||{};
  const autoPaySafety=bp.requireFiniteForAutoPay!==false?' · 🛡 AutoPay 要求有限总预算':'';
  $('#budgetSummary').innerHTML=`${b.enabled?'🟠 已启用':'⚪ 未启用'} · 币种 ${esc(b.currency||'EUR')} · 已占用 <strong>${Number(b.usedReserved||0).toFixed(2)}</strong> · 剩余 <strong>${rem}</strong>${autoPaySafety}${b.foreignEntryCount?` · <span class="budget-block">${b.foreignEntryCount} 条其他币种账本未计入</span>`:''}`;
  const rows=b.entries||[];
  $('#budgetLedger').innerHTML=rows.length?rows.map(x=>`<div class="ledger-row"><span>${esc(fmtTime(x.time))}</span><span class="mono">${esc(x.targetId)}</span><span>基础 ${esc(x.currency)} ${Number(x.baseAmount||0).toFixed(2)}</span><span>占用 ${esc(x.currency)} ${Number(x.reservedAmount||0).toFixed(2)}</span></div>`).join(''):'<div class="muted">暂无 v0.3 预算账本记录</div>';
}
function renderNotificationControls(){
  const nt=state.data.settings.notifications||{}, enabled=new Set(nt.events||[]);
  $('#notificationEvents').innerHTML=Object.entries(notificationEventLabels).map(([k,v])=>`<label class="chip"><input class="notify-event" type="checkbox" value="${k}" ${enabled.has(k)?'checked':''}> ${esc(v)}</label>`).join('');
  document.querySelectorAll('.notify-event').forEach(x=>x.addEventListener('change',()=>markSectionDirty('notifications')));
}
function renderNotificationSummary(){
  const nt=state.data.settings.notifications||{}, rt=state.data.runtime||{};
  $('#notificationSummary').innerHTML=`${nt.enabled!==false?'🟢 通知启用':'⚪ 通知关闭'} · 桌面 ${nt.desktop!==false?'开启':'关闭'} · Telegram ${nt.telegramEnabled?'开启':'关闭'} · Token ${rt.telegramTokenReady?'已加载':'未加载'}${rt.notificationLastError?` · <span class="budget-block">${esc(rt.notificationLastError)}</span>`:''}`;
  const rows=state.data.notifications||[];
  $('#notificationHistory').innerHTML=rows.length?rows.slice(0,20).map(x=>`<div class="notify-history-row ${esc(x.severity||'')}"><span>${esc(fmtTime(x.time))}</span><strong>${esc(x.event)}</strong><span>${esc(x.title)} · ${esc(x.message)}</span></div>`).join(''):'<div class="muted">暂无通知记录</div>';
}
function selectedNotificationEvents(){ return [...document.querySelectorAll('.notify-event:checked')].map(x=>x.value); }

function renderGlobalPaymentProfiles(currentDuration,currentMode){
  const data=state.data?.paymentProfiles||{}, profiles=data.profiles||[], sel=$('#paymentPreset');
  const currentKey=`${currentDuration}|||${currentMode}`;
  let options=profiles.map(x=>`<option value="${esc(x.duration)}|||${esc(x.mode)}">${esc(x.duration)} / ${esc(x.mode)} · ${x.supportCount}/${data.catalogPlanCount||'?'} plans${x.sampleDescription?' · '+esc(x.sampleDescription):''}</option>`);
  if(!profiles.some(x=>`${x.duration}|||${x.mode}`===currentKey) && currentDuration && currentMode){
    options.unshift(`<option value="${esc(currentKey)}">当前 ${esc(currentDuration)} / ${esc(currentMode)}（缓存中未发现）</option>`);
  }
  options.push('<option value="custom">自定义 duration / mode</option>');
  sel.innerHTML=options.join('');
  sel.value=[...sel.options].some(o=>o.value===currentKey)?currentKey:'custom';
  const custom=sel.value==='custom'; $('#priceDuration').disabled=!custom; $('#priceMode').disabled=!custom;
  $('#paymentScanStatus').textContent=`已缓存 ${data.cachedPlanCount||0}/${data.catalogPlanCount||0} 个 Plan 的付款价；扫描后会汇总当前目录全部实际组合。`;
}
function syncPaymentPresetUi(applyPreset=true){
  const preset=$('#paymentPreset').value;
  if(applyPreset&&preset!=='custom'){
    const [duration,mode]=preset.split('|||'); $('#priceDuration').value=duration||''; $('#priceMode').value=mode||'';
  }
  const custom=preset==='custom'; $('#priceDuration').disabled=!custom; $('#priceMode').disabled=!custom;
}

function renderDcControls(){
  const st=state.data.settings.dcStrategy, dcs=state.data.runtime.knownDatacenters||[];
  $('#fixedWrap').style.display=st.mode==='fixed'?'flex':'none';
  $('#fallback').disabled=!(st.mode==='priority'||st.mode==='whitelist');
  $('#dcChecks').style.display=(st.mode==='any'||st.mode==='fixed')?'none':'flex';
  $('#priorityBox').style.display=st.mode==='priority'?'block':'none';
  $('#dcChecks').innerHTML=dcs.map(dc=>`<label class="chip"><input class="dc-check" type="checkbox" value="${esc(dc)}" ${(st.selected||[]).includes(dc)?'checked':''}> ${esc(dc.toUpperCase())}</label>`).join('');
  document.querySelectorAll('.dc-check').forEach(x=>x.addEventListener('change',saveStrategyFromUi));
  const pri=(st.priority||[]).filter(x=>dcs.includes(x)); dcs.forEach(x=>{if(!pri.includes(x))pri.push(x)});
  $('#priorityList').innerHTML=pri.map((dc,i)=>`<div class="priority-item"><strong>${esc(dc.toUpperCase())}</strong><button class="pup" data-dc="${dc}" ${i===0?'disabled':''}>↑</button><button class="pdown" data-dc="${dc}" ${i===pri.length-1?'disabled':''}>↓</button></div>`).join('');
  document.querySelectorAll('.pup,.pdown').forEach(b=>b.addEventListener('click',async e=>{e.preventDefault();const cur=[...(state.data.settings.dcStrategy.priority||[])],i=cur.indexOf(b.dataset.dc),j=b.classList.contains('pup')?i-1:i+1;if(i>=0&&j>=0&&j<cur.length){[cur[i],cur[j]]=[cur[j],cur[i]];state.data.settings.dcStrategy.priority=cur;await saveStrategyFromUi()}}));
  let summary=`当前：${st.mode}`;
  if(st.mode==='fixed')summary+=` · 只接受 ${String(st.fixed).toUpperCase()}`;
  if(['priority','whitelist','exclude'].includes(st.mode))summary+=` · 选择 ${((st.selected||[]).map(x=>x.toUpperCase()).join(', ')||'无')}`;
  if(['priority','whitelist'].includes(st.mode))summary+=` · ${st.fallback==='any'?'允许回退':'不回退'}`;
  $('#strategySummary').textContent=summary;
}
async function saveStrategyFromUi(){
  const st=JSON.parse(JSON.stringify(state.data.settings.dcStrategy));
  st.mode=$('#dcMode').value; st.fixed=$('#fixedDc').value; st.fallback=$('#fallback').value;
  st.selected=[...document.querySelectorAll('.dc-check:checked')].map(x=>x.value);
  markSectionDirty('strategy');
  try{
    const result=await api('/api/settings','POST',{dcStrategy:st,orderDefaults:{catalogMonthlyPriceCap:n($('#orderPriceCap').value,0)}},12000);
    // Apply the POST response directly. Do not clear the draft and then wait on
    // /api/state; that was the v0.16 window where old values could redraw.
    applySavedSettings(result,'strategy');
    toast('机房策略已保存');
    void refreshAnalytics();
  }catch(e){
    // Keep the section dirty so the user's current selections are not overwritten.
    toast(e.message);
  }
}

function setPriceProfileOptions(prices,currentDuration='',currentMode=''){
  const sel=$('#priceProfile');
  const rows=prices||[];
  sel.innerHTML=rows.length?rows.map((x,i)=>`<option value="${i}" data-duration="${esc(x.duration)}" data-mode="${esc(x.mode)}" data-price="${esc(x.price||'')}" data-desc="${esc(x.description||'')}">${esc(x.duration)} / ${esc(x.mode)} · ${esc(x.price||'')}</option>`).join(''):'<option value="">没有可用付款组合</option>';
  const hit=rows.findIndex(x=>x.duration===currentDuration&&x.mode===currentMode);
  if(hit>=0)sel.value=String(hit);
  updatePriceProfileDetail();
}
function updatePriceProfileDetail(){
  const opt=$('#priceProfile').selectedOptions[0];
  if($('#inheritPrice').checked){
    const d=state.data.settings.orderDefaults; $('#priceProfileDetail').textContent=`继承全局：${d.priceDuration} / ${d.priceMode}。真实下单前仍会检查该 Plan 是否支持。`; return;
  }
  if(!opt||opt.value===''){ $('#priceProfileDetail').textContent='尚未读取/选择本型号付款组合。'; return; }
  $('#priceProfileDetail').textContent=`${opt.dataset.duration} / ${opt.dataset.mode} · ${opt.dataset.price||''}${opt.dataset.desc?' · '+opt.dataset.desc:''}`;
}
async function loadPricesForDialog(){
  const id=$('#dialogTargetId').value,t=targetById(id); if(!t)return;
  try{
    $('#loadPrices').disabled=true; $('#loadPrices').textContent='读取中...';
    const r=await api('/api/prices','POST',{planCode:t.planCode});
    setPriceProfileOptions(r.prices,t.priceDuration||'',t.priceMode||'');
    state.data=await api('/api/state'); renderTable();
    toast(`读取到 ${r.prices.length} 个付款组合`);
  }catch(e){toast(e.message)}finally{$('#loadPrices').disabled=false;$('#loadPrices').textContent='读取付款周期'}
}
function openAdvanced(id){
  const t=targetById(id); if(!t)return;
  $('#dialogTargetId').value=id; $('#dialogTitle').textContent=`高级设置 · ${t.name}`;
  $('#orderMemoryOption').value=t.orderMemoryOption||''; $('#orderStorageOption').value=t.orderStorageOption||'';
  $('#inheritPrice').checked=t.inheritPrice!==false;
  $('#priceProfile').innerHTML=`<option value="">${t.inheritPrice!==false?'继承全局':'当前 '+esc(t.priceDuration||'')+' / '+esc(t.priceMode||'')+'（点读取付款周期校验）'}</option>`;
  $('#priceProfile').disabled=$('#inheritPrice').checked;
  $('#targetAutoPay').checked=!!t.autoPay; $('#targetPriority').value=t.orderPriority??50; $('#targetBudgetReserve').value=t.budgetReserveOverride??0;
  $('#reviewText').textContent=t.review?String(t.reviewReason||'REVIEW'):'';
  $('#clearReview').disabled=!t.review; updatePriceProfileDetail();
  $('#advancedDialog').showModal();
}
async function saveAdvanced(){
  const id=$('#dialogTargetId').value,t=targetById(id); if(!t)return;
  const inherit=$('#inheritPrice').checked;
  let duration=t.priceDuration||'', mode=t.priceMode||'';
  if(!inherit){
    const opt=$('#priceProfile').selectedOptions[0];
    if(!opt||!opt.dataset.duration||!opt.dataset.mode){toast('请先读取并选择该型号的付款周期');return}
    duration=opt.dataset.duration; mode=opt.dataset.mode;
  }
  const patch={orderPriority:Math.max(0,Math.min(100,Math.floor(n($('#targetPriority').value,50)))),budgetReserveOverride:Math.max(0,n($('#targetBudgetReserve').value,0)),orderMemoryOption:$('#orderMemoryOption').value.trim(),orderStorageOption:$('#orderStorageOption').value.trim(),inheritPrice:inherit,priceDuration:duration,priceMode:mode,autoPay:$('#targetAutoPay').checked};
  setTargetPending(id,patch);
  try{
    const result=await api('/api/target','POST',{planCode:t.planCode,name:t.name,memory:t.memory,storage:t.storage,price:t.price,watch:t.watch,autoOrder:t.autoOrder,desiredQty:t.desiredQty,...patch});
    if(result?.target)state.data.targets[id]=result.target;
    $('#advancedDialog').close(); await refreshState(false); toast('高级设置已保存');
  }catch(e){clearTargetPending(id);toast(e.message);await refreshState(false)}
}
function renderPrediction(){
  const a=state.analytics;if(!a)return;
  const p=a.selectedPrediction;
  if(!p){
    $('#watchIndexSummary').innerHTML='<div class="muted">当前范围没有足够的补货历史。</div>';
    $('#watchComponents').innerHTML=''; $('#watchForecast').innerHTML='<div class="muted">暂无预测曲线</div>';
  }else{
    const peak=p.nextPeak;
    $('#watchIndexSummary').innerHTML=`<div class="watch-score"><strong>${Number(p.watchIndex).toFixed(1)}</strong><span>/100</span></div><div><div class="watch-label">${esc(p.label||p.targetId)}</div><div class="muted">${esc(p.band)} · 置信度 ${Number(p.confidence).toFixed(1)}% · ${p.sampleCount} 个窗口</div><div class="muted">${peak?`未来峰值 ${fmtUtcHour(peak.epoch)} · ${Number(peak.watchIndex).toFixed(1)}`:'未来峰值 —'}</div></div>`;
    const c=p.components||{};
    const comps=[['时段活跃度',c.slotActivity],['补货到点程度',c.cadenceDue],['机房策略匹配',c.dcFit],['捕获余量',c.captureMargin]];
    $('#watchComponents').innerHTML=comps.map(([name,val])=>`<div class="watch-comp"><span>${esc(name)}</span><div class="watch-comp-bar"><i style="width:${Math.max(0,Math.min(100,Number(val)||0))}%"></i></div><strong>${Number(val||0).toFixed(1)}</strong></div>`).join('')+`<div class="watch-meta">中位补货间隔：${esc(fmtDuration(p.medianGapSeconds))} · 距上次补货：${esc(fmtDuration(p.elapsedSinceLastStartSeconds))} · 中位观测窗口：${esc(fmtDuration(p.medianObservedSeconds))} · 衰减半衰期：${esc(p.halfLifeDays)} 天</div>`;
    const fc=p.forecast||[],max=Math.max(1,...fc.map(x=>Number(x.watchIndex)||0));
    $('#watchForecast').innerHTML=fc.length?`<div class="forecast-bars">${fc.map((x,i)=>`<div class="forecast-col" title="${fmtUtcHour(x.epoch)} · Watch ${Number(x.watchIndex).toFixed(1)} · 时段 ${Number(x.slotActivity).toFixed(1)} · 到点 ${Number(x.cadenceDue).toFixed(1)}"><div class="forecast-bar"><i style="height:${Math.max(4,100*Number(x.watchIndex)/max)}%"></i></div><span>${i%3===0?String(new Date(Number(x.epoch)*1000).getUTCHours()).padStart(2,'0'):''}</span></div>`).join('')}</div><div class="forecast-axis">UTC 小时 · 相对曲线高度按当前 24h 峰值归一</div>`:'<div class="muted">暂无预测曲线</div>';
  }
  const rows=a.predictions||[];
  $('#predictionRows').innerHTML=rows.length?rows.map(x=>{const c=x.components||{},peak=x.nextPeak;return `<tr><td>${x.rank}</td><td>${esc(x.label||x.targetId)}</td><td><strong>${Number(x.watchIndex).toFixed(1)}</strong><div class="muted">${esc(x.band)}</div></td><td>${Number(x.confidence).toFixed(1)}%</td><td>${Number(c.slotActivity||0).toFixed(1)}</td><td>${Number(c.cadenceDue||0).toFixed(1)}</td><td>${Number(c.dcFit||0).toFixed(1)}</td><td>${Number(c.captureMargin||0).toFixed(1)}</td><td>${peak?`${fmtUtcHour(peak.epoch)} · ${Number(peak.watchIndex).toFixed(1)}`:'—'}</td></tr>`}).join(''):'<tr><td colspan="9" class="muted">当前范围没有预测样本</td></tr>';
}

function renderAnalytics(){
  const a=state.analytics;if(!a)return;
  const s=a.summary||{}, hot=s.hottestSlot;
  const cards=[
    ['补货窗口',s.episodeCount??0],['已闭合窗口',s.closedEpisodeCount??0],['当前开放',s.currentlyOpenCount??0],
    ['中位观测时长',fmtDuration(s.medianObservedSeconds)],['可用于时长统计',s.durationEligibleCount??0],
    ['跨重启不连续',s.continuityUncertainClosedCount??0],['最活跃机房',s.mostActiveDatacenter?String(s.mostActiveDatacenter).toUpperCase():'—'],
    ['高频时段',hot?`${weekdayNames[hot.weekday]} ${String(hot.hour).padStart(2,'0')}:00 · ${hot.count}次`:'—']
  ];
  $('#analyticsStats').innerHTML=cards.map(x=>`<div class="analytics-stat"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');
  renderPrediction();

  const targetSel=$('#analyticsTarget'), current=targetSel.value;
  const opts=['<option value="">全部历史目标</option>',...(a.targets||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.label)} · ${x.episodeCount}次</option>`)].join('');
  if(targetSel.dataset.optionsKey!==opts){targetSel.innerHTML=opts;targetSel.dataset.optionsKey=opts;targetSel.value=[...targetSel.options].some(o=>o.value===current)?current:'';}

  const heat=a.heatmap||Array.from({length:7},()=>Array(24).fill(0));
  const max=Math.max(1,...heat.flat().map(x=>Number(x)||0)); let cells='<div></div>';
  for(let h=0;h<24;h++)cells+=`<div class="heat-label">${h}</div>`;
  for(let d=0;d<7;d++){
    cells+=`<div class="heat-label">${weekdayNames[d]}</div>`;
    for(let h=0;h<24;h++){const v=Number(heat[d]?.[h]||0),alpha=v?Math.min(.92,.12+.8*v/max):.035;cells+=`<div class="heat-cell" style="--heat-alpha:${alpha}" title="${weekdayNames[d]} ${String(h).padStart(2,'0')}:00 · ${v} 次">${v||''}</div>`;}
  }
  $('#stockHeatmap').innerHTML=cells;

  const dc=Object.entries(a.datacenterCounts||{}),dcMax=Math.max(1,...dc.map(x=>Number(x[1])||0));
  $('#dcFrequency').innerHTML=dc.length?dc.map(([name,count])=>`<div class="dc-row"><strong>${esc(String(name).toUpperCase())}</strong><div class="dc-bar"><i style="width:${Math.max(3,100*Number(count)/dcMax)}%"></i></div><span>${count}</span></div>`).join(''):'<div class="muted">当前范围没有机房出现记录</div>';

  const rows=a.recentEpisodes||[];
  $('#episodeRows').innerHTML=rows.length?rows.map(ep=>{
    const dur=ep.closed?fmtDuration(ep.observedSeconds):fmtDuration(ep.currentObservedSeconds);
    const cls=ep.closed?'duration-closed':'duration-open';
    const minus=ep.uncertaintyMinusSeconds,plus=ep.uncertaintyPlusSeconds;
    let unc=(minus||plus)?`-${minus??'?'}s / +${plus??'?'}s`:'历史未知';
    if(ep.continuityUncertain)unc+=` · 跨重启，不计时长统计`;
    return `<tr><td>${esc(analyticsTargetLabel(ep.targetId))}</td><td>${esc(fmtTime(ep.start))}</td><td>${ep.closed?esc(fmtTime(ep.end)):'<span class="duration-open">仍在开放/未观测到关闭</span>'}</td><td class="${cls}">${esc(dur)}</td><td>${esc((ep.datacenters||[]).map(x=>String(x).toUpperCase()).join(', ')||'—')}</td><td>${esc(unc)}</td><td>${(ep.dcTransitions||[]).length}</td></tr>`;
  }).join(''):'<tr><td colspan="7" class="muted">当前范围没有可重建的补货窗口</td></tr>';
  $('#analyticsNote').textContent=`时长统计只使用已经检测到 SOLD_OUT 且没有跨越 Commander 重启盲区的窗口。Watch Index 是历史经验排序，不是补货概率，也不会触发下单；模型时钟固定使用 UTC。v0.5 起每个边界记录轮询间隔。当前轮询间隔 ${a.currentPollIntervalSeconds||'—'} 秒。`;
  syncReplayTargets();
}
async function refreshAnalytics(){
  if(state.ui.analyticsRefresh)return state.ui.analyticsRefresh;
  const target=$('#analyticsTarget').value||'',days=$('#analyticsLookback').value||'30';
  const task=(async()=>{
    try{
      state.analytics=await api(`/api/analytics?targetId=${encodeURIComponent(target)}&lookbackDays=${encodeURIComponent(days)}`,'GET',null,12000);
      renderAnalytics();renderTable();return state.analytics;
    }catch(e){
      $('#analyticsStats').innerHTML=`<div class="budget-block">统计读取失败：${esc(e.message)}</div>`;
      return null;
    }
  })();
  state.ui.analyticsRefresh=task;
  try{return await task}finally{if(state.ui.analyticsRefresh===task)state.ui.analyticsRefresh=null}
}
async function exportAnalytics(){
  const target=$('#analyticsTarget').value||'',days=$('#analyticsLookback').value||'30';
  try{
    const blob=await apiBlob(`/api/analytics/export?targetId=${encodeURIComponent(target)}&lookbackDays=${encodeURIComponent(days)}`,30000);
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`kimsufi-stock-history-${new Date().toISOString().replace(/[:.]/g,'-')}.csv`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){toast(e.message)}
}

function syncReplayTargets(){
  if(!state.analytics)return;
  const sel=$('#replayTarget'), current=sel.value;
  const opts=['<option value="">请选择目标</option>',...(state.analytics.targets||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.label)} · ${x.episodeCount}次</option>`)].join('');
  if(sel.dataset.optionsKey!==opts){
    sel.innerHTML=opts;sel.dataset.optionsKey=opts;
    if([...sel.options].some(o=>o.value===current))sel.value=current;
    else if($('#analyticsTarget').value && [...sel.options].some(o=>o.value===$('#analyticsTarget').value))sel.value=$('#analyticsTarget').value;
    else if(sel.options.length>1)sel.selectedIndex=1;
  }
  const dcs=state.data?.runtime?.knownDatacenters||[];
  const fixed=$('#replayFixedDc'), old=fixed.value;
  fixed.innerHTML=dcs.map(x=>`<option value="${esc(x)}">${esc(String(x).toUpperCase())}</option>`).join('');
  if(dcs.includes(old))fixed.value=old;
  else if(dcs.includes('gra'))fixed.value='gra';
  updateReplayDcUi();
}
function updateReplayDcUi(){
  const mode=$('#replayDcMode').value;
  $('#replayFixedWrap').style.display=mode==='fixed'?'flex':'none';
  $('#replaySelectedWrap').style.display=['whitelist','priority','exclude'].includes(mode)?'flex':'none';
  $('#replayFallbackWrap').style.display=['whitelist','priority'].includes(mode)?'flex':'none';
}
function parseReplayIntervals(){
  const vals=$('#replayIntervals').value.split(/[，,\s]+/).map(x=>Math.floor(Number(x))).filter(x=>Number.isFinite(x)&&x>=1&&x<=3600);
  return [...new Set(vals)].sort((a,b)=>a-b).slice(0,20);
}
function replayDcPayload(){
  const mode=$('#replayDcMode').value;
  if(mode==='current')return {mode:'current'};
  const selected=$('#replaySelectedDcs').value.split(/[，,\s]+/).map(x=>x.trim().toLowerCase()).filter(Boolean);
  return {mode,fixed:$('#replayFixedDc').value,selected,priority:selected,fallback:$('#replayFallback').value};
}
function replayRateClass(v){v=Number(v);return v>=90?'replay-good':v>=65?'replay-warn':'replay-bad'}
function renderReplay(){
  const r=state.replay;
  if(!r){$('#replayRows').innerHTML='<tr><td colspan="9" class="muted">暂无回放结果</td></tr>';return}
  const rec=r.recommendation||{};
  const cards=[
    ['原始闭合窗口',r.episodeCountRaw??0],['可比较窗口',r.comparableEpisodeCount??0],['跨重启跳过',r.skippedContinuityUncertain??0],
    ['DC 证据未知跳过',r.dcUnknownExcluded??0],['最佳名义捕获',fmtPercent(r.bestNominalCaptureRatePercent)],['模型','解析相位回放']
  ];
  $('#replaySummary').innerHTML=cards.map(x=>`<div class="analytics-stat"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');
  if(rec.intervalSeconds){
    $('#replayRecommendation').innerHTML=`推荐 <strong>${rec.intervalSeconds} 秒</strong> · 名义捕获 ${fmtPercent(rec.nominalCaptureRatePercent)} · ${Number(rec.callsPerDayPerTarget||0).toLocaleString()} 次/天/目标。判据：选择仍保留最佳名义捕获率至少 ${Number(rec.retainPercent||0).toFixed(1)}% 的最慢测试间隔${r.maxCallsPerDay?`，并满足 ≤ ${Number(r.maxCallsPerDay).toLocaleString()} 次/天`:''}。`;
  }else{
    $('#replayRecommendation').innerHTML=`<span class="replay-bad">没有测试间隔满足当前 API 调用上限。</span>`;
  }
  const rows=r.rows||[];
  $('#replayRows').innerHTML=rows.length?rows.map(x=>`<tr class="${x.recommended?'replay-recommended':''}"><td><strong>${x.intervalSeconds}s</strong></td><td>${Number(x.callsPerDayPerTarget).toLocaleString()}</td><td>${x.comparableEpisodeCount}</td><td>${x.dcEligibleEpisodeCount} · ${fmtPercent(x.dcEligibleRatePercent)}</td><td class="${x.conservativeCaptureRatePercent==null?'muted':replayRateClass(x.conservativeCaptureRatePercent)}">${x.conservativeCaptureRatePercent==null?'—':fmtPercent(x.conservativeCaptureRatePercent)}</td><td class="${replayRateClass(x.nominalCaptureRatePercent)}"><strong>${fmtPercent(x.nominalCaptureRatePercent)}</strong><div class="muted">≈ ${Number(x.nominalExpectedCaptureCount||0).toFixed(2)} 窗口</div></td><td class="${x.optimisticCaptureRatePercent==null?'muted':replayRateClass(x.optimisticCaptureRatePercent)}">${x.optimisticCaptureRatePercent==null?'—':fmtPercent(x.optimisticCaptureRatePercent)}</td><td>${fmtPercent(x.boundCoveragePercent)} · ${x.boundKnownEpisodeCount}/${x.comparableEpisodeCount}</td><td>${x.recommended?'⭐ 推荐':''}</td></tr>`).join(''):'<tr><td colspan="9" class="muted">当前条件没有可比较窗口</td></tr>';
  const selected=rows.find(x=>x.recommended)||rows[0];
  const details=selected?.details||[];
  $('#replayDetailRows').innerHTML=details.length?details.map(x=>`<tr><td>${esc(fmtTime(x.start))}</td><td>${esc(fmtDuration(x.observedSeconds))}</td><td>${esc((x.datacenters||[]).map(d=>String(d).toUpperCase()).join(', ')||'—')}</td><td class="${x.dcEligible?'replay-good':'replay-bad'}">${x.dcUnknown?'证据未知':x.dcEligible?'可用':'不匹配'}</td><td>${x.conservativeCapturePercent==null?'—':fmtPercent(x.conservativeCapturePercent)}</td><td><strong>${fmtPercent(x.nominalCapturePercent)}</strong></td><td>${x.optimisticCapturePercent==null?'—':fmtPercent(x.optimisticCapturePercent)}</td><td>${x.startPollSeconds??'?'}s / ${x.endPollSeconds??'?'}s</td></tr>`).join(''):'<tr><td colspan="8" class="muted">暂无窗口明细</td></tr>';
  $('#replayNote').textContent=`回放模型 ${r.model?.name||'strategy-replay'}：对固定轮询周期内所有可能相位做解析计算；DC_CHANGE 会按时间段参与计算。保守/乐观边界仅在历史记录含轮询周期元数据时可用。回放只覆盖已观测窗口，不能恢复历史 watcher 从未发现的补货。`;
}
async function runReplay(){
  const target=$('#replayTarget').value;
  if(!target){toast('请先选择一个有历史样本的目标');return}
  const intervals=parseReplayIntervals();
  if(!intervals.length){toast('至少输入一个 1–3600 秒的轮询间隔');return}
  const payload={targetId:target,lookbackDays:Math.max(0,Math.floor(n($('#replayLookback').value,30))),intervals,retainPercent:Math.max(50,Math.min(100,n($('#replayRetain').value,95))),maxCallsPerDay:Math.max(0,Math.floor(n($('#replayMaxCalls').value,0))),dcStrategy:replayDcPayload()};
  $('#replayRecommendation').textContent='正在回放…';
  try{state.replay=await api('/api/replay','POST',payload);renderReplay()}
  catch(e){toast(e.message);$('#replayRecommendation').innerHTML=`<span class="replay-bad">回放失败：${esc(e.message)}</span>`}
}
function renderPortfolioReplay(){
  const r=state.portfolioReplay;
  if(!r){$('#portfolioRows').innerHTML='<tr><td colspan="9" class="muted">暂无组合回放结果</td></tr>';return}
  const cards=[
    ['模拟目标',r.simulatedTargetCount??0],['轮询相位',r.phaseCount??0],['平均采购',Number(r.averagePortfolioPurchases||0).toFixed(2)],
    ['平均预算使用',r.budget>0?`${esc(r.currency)} ${Number(r.averagePortfolioBudgetUsed||0).toFixed(2)}`:'无限预算'],['平均捕获机会',Number(r.averageCapturedOpportunities||0).toFixed(2)],['同周期竞争组',Number(r.averageSameCycleCompetitionGroups||0).toFixed(2)]
  ];
  $('#portfolioSummary').innerHTML=cards.map(x=>`<div class="analytics-stat"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');
  const rows=r.targets||[];
  $('#portfolioRows').innerHTML=rows.length?rows.map(x=>`<tr><td>${esc(x.label||x.id)}</td><td><strong>${x.priority}</strong></td><td>${x.desiredQty}</td><td>${x.reserveAmount==null?'<span class="replay-warn">未知</span>':`${esc(r.currency)} ${Number(x.reserveAmount).toFixed(2)}`}</td><td>${x.historicalEpisodeCount}</td><td><strong>${Number(x.averagePurchases||0).toFixed(3)}</strong></td><td>${fmtPercent(x.purchasePhaseRatePercent)}</td><td>${Number(x.averageBudgetBlocked||0).toFixed(3)}</td><td>${Number(x.averageReserveUnknownBlocked||0).toFixed(3)}</td></tr>`).join(''):'<tr><td colspan="9" class="muted">没有同时满足“当前自动下单目标 + 有历史窗口”的目标。</td></tr>';
  const skipped=(r.skippedNoHistory||[]).length;
  $('#portfolioNote').textContent=`模型 ${r.model?.name||'portfolio-replay'}：使用当前 DC 策略、priority 与 desiredQty；预算来源 ${r.budgetSource}，${r.budget>0?`${r.currency} ${Number(r.budget).toFixed(2)}`:'无限'}。同一轮询时刻的机会按 priority 从高到低处理；更早发生的低优先级历史机会仍可能先消耗预算。${skipped?`另有 ${skipped} 个自动下单目标因没有该历史范围内的窗口而跳过。`:''}`;
}
async function runPortfolioReplay(){
  const raw=$('#portfolioBudget').value.trim();
  const payload={intervalSeconds:Math.max(1,Math.min(3600,Math.floor(n($('#portfolioInterval').value,30)))),lookbackDays:Math.max(0,Math.floor(n($('#portfolioLookback').value,30)))};
  if(raw!=='')payload.budget=Math.max(0,n(raw,0));
  try{state.portfolioReplay=await api('/api/replay/portfolio','POST',payload);renderPortfolioReplay()}
  catch(e){toast(e.message);$('#portfolioNote').innerHTML=`<span class="replay-bad">组合回放失败：${esc(e.message)}</span>`}
}

function csvCell(v){const s=String(v??'');return /[",\n]/.test(s)?`"${s.replace(/"/g,'""')}"`:s}
function parseNumberList(text,min=0,max=1e9){
  return [...new Set(String(text||'').split(/[，,\s]+/).map(x=>Number(x)).filter(x=>Number.isFinite(x)&&x>=min&&x<=max))];
}
function optimizerBudgetLabel(v,currency){return Number(v)>0?`${currency} ${Number(v).toFixed(2)}`:'不限'}
function renderOptimizer(){
  const r=state.optimizer;
  if(!r){$('#optimizerRows').innerHTML='<tr><td colspan="10" class="muted">暂无优化结果</td></tr>';return}
  const cards=[
    ['理论组合',Number(r.theoreticalCandidateCount||0).toLocaleString()],
    ['实际评估',Number(r.evaluatedCandidateCount||0).toLocaleString()],
    ['Pareto 前沿',Number(r.paretoCount||0).toLocaleString()],
    ['目标数',`${r.simulatedTargetCount}/${r.autoOrderTargetCount}`],
    ['最佳加权完成度',fmtPercent(r.bestWeightedFulfillmentPercent)]
  ];
  $('#optimizerSummary').innerHTML=cards.map(x=>`<div class="analytics-stat"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');
  const rec=r.recommendation;
  if(rec){
    $('#optimizerRecommendation').innerHTML=`推荐：<strong>${rec.intervalSeconds}s</strong> · ${esc(rec.dcLabel)} · ${esc(rec.priorityProfile)} · 预算 ${esc(rec.budgetLabel)} · 加权完成度 <strong>${fmtPercent(rec.weightedFulfillmentPercent)}</strong> · 平均买到 ${Number(rec.averagePortfolioPurchases||0).toFixed(3)} 台 · ${Number(rec.callsPerDayTotal||0).toLocaleString()} API/天。它保留至少 ${Number(rec.retainPercent||0).toFixed(1)}% 的最佳加权完成度后，优先选择调用更少、预算占用更低的 Pareto 点。`;
  }else $('#optimizerRecommendation').innerHTML='<span class="replay-bad">没有可推荐方案。</span>';
  const rows=r.pareto||[];
  const recId=rec?.id;
  $('#optimizerRows').innerHTML=rows.length?rows.map((x,i)=>`<tr class="${x.id===recId?'replay-recommended':''}"><td>${i+1}</td><td><strong>${x.intervalSeconds}s</strong></td><td>${esc(x.dcLabel)}</td><td><span title="${esc((x.priorityOrder||[]).join(' > '))}">${esc(x.priorityProfile)}</span></td><td>${esc(x.budgetLabel||optimizerBudgetLabel(x.budget,r.currency))}</td><td class="${replayRateClass(x.weightedFulfillmentPercent)}"><strong>${fmtPercent(x.weightedFulfillmentPercent)}</strong></td><td>${Number(x.averagePortfolioPurchases||0).toFixed(3)}</td><td>${Number(x.callsPerDayTotal||0).toLocaleString()}</td><td>${esc(r.currency)} ${Number(x.averageBudgetUsed||0).toFixed(2)}</td><td>${x.id===recId?'⭐ 推荐':'Pareto'}</td></tr>`).join(''):'<tr><td colspan="10" class="muted">没有 Pareto 结果</td></tr>';
  const skipped=(r.skippedNoHistory||[]).length;
  $('#optimizerNote').textContent=`模型 ${r.model?.name||'strategy-optimizer'}：搜索 ${r.dcCandidateCount} 个 DC 策略 × ${r.priorityProfileCount} 个 priority profile × ${r.budgetCandidates?.length||0} 个预算 × ${r.intervals?.length||0} 个轮询间隔。${r.searchTruncated?'候选空间超过上限，本轮已截断；提高“最大评估候选数”可继续搜索。':'候选空间未截断。'}${skipped?` ${skipped} 个自动下单目标因没有该范围内的历史窗口未参与。`:''} 只重放已观察到的库存窗口，不修改真实设置。`;
}
async function runOptimizer(){
  const intervals=parseNumberList($('#optimizerIntervals').value,1,3600).map(x=>Math.floor(x));
  if(!intervals.length){toast('至少输入一个有效轮询间隔');return}
  const budgets=parseNumberList($('#optimizerBudgets').value,0,1e9);
  const payload={
    lookbackDays:Math.max(0,Math.floor(n($('#optimizerLookback').value,30))),
    intervals,
    dcSearch:$('#optimizerDcSearch').value,
    prioritySearch:$('#optimizerPrioritySearch').value,
    retainPercent:Math.max(50,Math.min(100,n($('#optimizerRetain').value,95))),
    maxCandidates:Math.max(50,Math.min(20000,Math.floor(n($('#optimizerMaxCandidates').value,5000))))
  };
  if($('#optimizerBudgets').value.trim()!=='')payload.budgets=budgets;
  $('#optimizerRecommendation').textContent='正在搜索策略空间…';
  try{state.optimizer=await api('/api/optimizer','POST',payload);renderOptimizer()}
  catch(e){toast(e.message);$('#optimizerRecommendation').innerHTML=`<span class="replay-bad">优化失败：${esc(e.message)}</span>`}
}
function exportOptimizer(){
  const r=state.optimizer;if(!r){toast('请先运行优化器');return}
  const rows=r.pareto||[];
  const cols=['intervalSeconds','dcLabel','priorityProfile','budgetLabel','weightedFulfillmentPercent','averagePortfolioPurchases','callsPerDayTotal','averageBudgetUsed'];
  const csv=[cols.join(',')].concat(rows.map(x=>cols.map(k=>`"${String(x[k]??'').replace(/"/g,'""')}"`).join(','))).join('\r\n');
  const blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`kimsufi-pareto-${new Date().toISOString().replace(/[:.]/g,'-')}.csv`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function exportReplay(){
  const r=state.replay;if(!r){toast('请先运行回放');return}
  const head=['IntervalSeconds','CallsPerDayPerTarget','ComparableEpisodes','DcEligibleEpisodes','DcEligibleRatePercent','ConservativeCaptureRatePercent','NominalCaptureRatePercent','OptimisticCaptureRatePercent','BoundCoveragePercent','Recommended'];
  const lines=[head.join(','),...(r.rows||[]).map(x=>[x.intervalSeconds,x.callsPerDayPerTarget,x.comparableEpisodeCount,x.dcEligibleEpisodeCount,x.dcEligibleRatePercent,x.conservativeCaptureRatePercent??'',x.nominalCaptureRatePercent,x.optimisticCaptureRatePercent??'',x.boundCoveragePercent,x.recommended?'yes':'no'].map(csvCell).join(','))];
  const blob=new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`kimsufi-strategy-replay-${new Date().toISOString().replace(/[:.]/g,'-')}.csv`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

async function refreshState(rebuild=true,forceSettings=false){
  if(state.ui.stateRefresh)return state.ui.stateRefresh;
  const task=(async()=>{
    try{
      const oldCatalogLen=state.data?.catalog?.length||0;
      const fresh=await api('/api/state','GET',null,8000);
      reconcilePendingTargets(fresh);
      state.data=fresh;
      state.ui.lastStateError='';
      renderStatus();
      renderSettings(forceSettings);
      if(rebuild||oldCatalogLen!==state.data.catalog.length)buildFilterControls();
      renderTable();
      if(state.data.runtime.catalogError)toast('目录错误：'+state.data.runtime.catalogError);
      return fresh;
    }catch(e){
      const msg=String(e?.message||e);
      if(state.ui.lastStateError!==msg){state.ui.lastStateError=msg;toast(msg)}
      return null;
    }
  })();
  state.ui.stateRefresh=task;
  try{return await task}finally{if(state.ui.stateRefresh===task)state.ui.stateRefresh=null}
}

$('#search').addEventListener('input',renderTable); $('#stockFilter').addEventListener('change',renderTable); $('#orderabilityFilter').addEventListener('change',renderTable);
$('#priceMin').addEventListener('input',renderTable); $('#priceMax').addEventListener('input',renderTable); $('#diskMode').addEventListener('change',renderTable);
$('#resetFilters').addEventListener('click',()=>{$('#search').value='';$('#stockFilter').value='all';$('#orderabilityFilter').value='all';$('#priceMin').value='';$('#priceMax').value='';state.filter.ram.clear();state.filter.disk.clear();buildFilterControls();renderTable()});
$('#refreshCatalog').addEventListener('click',async()=>{await api('/api/catalog/refresh','POST',{});toast('已请求刷新目录');setTimeout(()=>refreshState(true),800)});
$('#pollNow').addEventListener('click',async()=>{await api('/api/poll','POST',{});toast('已请求立即轮询');setTimeout(()=>refreshState(false),700)});
$('#globalArm').addEventListener('change',async e=>{
  if(e.target.checked && !confirm('确认武装自动下单？命中库存、机房策略和价格上限后，后台会调用 --auto-pay（若目标启用）并真实提交订单。')){e.target.checked=false;return}
  try{await api('/api/settings','POST',{autoOrderArmed:e.target.checked});await refreshState(false);toast(e.target.checked?'自动下单已武装':'自动下单已解除')}
  catch(err){toast(err.message)}
});
$('#paymentPreset').addEventListener('change',()=>{syncPaymentPresetUi(true);markSettingsDirty()});
$('#scanPaymentProfiles').addEventListener('click',async()=>{
  const btn=$('#scanPaymentProfiles'),draftD=$('#priceDuration').value.trim(),draftM=$('#priceMode').value.trim();
  try{
    btn.dataset.busy='1';btn.disabled=true;btn.textContent='扫描付款组合与配置价格…';
    const r=await api('/api/prices/scan','POST',{force:true},180000);
    state.data=await api('/api/state');renderGlobalPaymentProfiles(draftD,draftM);syncPaymentPresetUi(false);markSettingsDirty();renderTable();
    const cs=r.configurationScan||{};
    toast(`发现 ${r.profiles.length} 种付款组合；Plan ${r.cachedPlanCount}/${r.catalogPlanCount}；配置价格 ${cs.cachedPairCount||0}/${cs.pairCount||0}`);
  }
  catch(e){toast(e.message)}finally{btn.dataset.busy='0';updatePaymentScanButton()}
});
$('#runtimeSettingsPanel').addEventListener('input',e=>{if(e.target.id!=='paymentPreset')markSettingsDirty()});
$('#runtimeSettingsPanel').addEventListener('change',e=>{if(!['paymentPreset'].includes(e.target.id))markSettingsDirty()});
$('#budgetSettingsPanel').addEventListener('input',()=>markSectionDirty('budget'));
$('#budgetSettingsPanel').addEventListener('change',()=>markSectionDirty('budget'));
$('#notificationSettingsPanel').addEventListener('input',e=>{
  if(!['saveNotifications','testNotifications'].includes(e.target.id))markSectionDirty('notifications');
});
$('#notificationSettingsPanel').addEventListener('change',e=>{
  if(!['saveNotifications','testNotifications'].includes(e.target.id))markSectionDirty('notifications');
});
$('#dcMode').addEventListener('change',saveStrategyFromUi); $('#fixedDc').addEventListener('change',saveStrategyFromUi); $('#fallback').addEventListener('change',saveStrategyFromUi); $('#orderPriceCap').addEventListener('change',saveStrategyFromUi);
$('#saveNotifications').addEventListener('click',async()=>{
  try{
    const result=await api('/api/settings','POST',{notifications:{
      enabled:$('#notifyEnabled').checked, desktop:$('#notifyDesktop').checked,
      telegramEnabled:$('#telegramEnabled').checked, telegramChatId:$('#telegramChatId').value.trim(),
      telegramTokenEnv:($('#telegramTokenEnv').value.trim()||'KSC_TELEGRAM_BOT_TOKEN'),
      heartbeatHours:Math.max(0,n($('#heartbeatHours').value,6)), events:selectedNotificationEvents(), silentEvents:['HEARTBEAT']
    }},12000);
    applySavedSettings(result,'notifications');
    toast('通知设置已保存');
  }catch(e){toast(e.message)}
});
$('#testNotifications').addEventListener('click',async()=>{
  try{await api('/api/notifications/test','POST',{message:'来自 Web 控制台的测试通知'});await refreshState(false);toast('测试通知已生成')}
  catch(e){toast(e.message)}
});
$('#saveBudget').addEventListener('click',async()=>{
  try{
    const result=await api('/api/settings','POST',{budgetPolicy:{
      enabled:$('#budgetEnabled').checked,
      currency:($('#budgetCurrency').value.trim()||'EUR').toUpperCase(),
      totalLimit:Math.max(0,n($('#budgetTotal').value,0)),
      baselineReserved:Math.max(0,n($('#budgetBaseline').value,0)),
      perOrderReserveCap:Math.max(0,n($('#budgetPerOrderCap').value,0)),
      bufferPercent:Math.max(0,n($('#budgetBufferPercent').value,0)),
      bufferFixed:Math.max(0,n($('#budgetBufferFixed').value,0))
    }},12000);
    applySavedSettings(result,'budget');
    toast('预算策略已保存');
  }catch(e){toast(e.message)}
});
$('#saveSettings').addEventListener('click',async()=>{
  try{
    const oldEndpoint=state.data?.settings?.endpoint,oldCountry=state.data?.settings?.country;
    const hadProfiles=(state.data?.paymentProfiles?.cachedPlanCount||0)>0;
    const newEndpoint=$('#endpoint').value,newCountry=$('#country').value.trim().toUpperCase();
    const result=await api('/api/settings','POST',{exe:$('#exePath').value.trim(),endpoint:newEndpoint,country:newCountry,pollIntervalSeconds:n($('#pollInterval').value,30),catalogRefreshSeconds:n($('#catalogInterval').value,900),orderDefaults:{priceDuration:$('#priceDuration').value.trim(),priceMode:$('#priceMode').value.trim(),autoPay:$('#autoPay').checked,catalogMonthlyPriceCap:n($('#orderPriceCap').value,0)}},12000);
    applySavedSettings(result,'runtime');
    if(oldEndpoint===newEndpoint&&oldCountry===newCountry&&hadProfiles){
      try{await api('/api/prices/config-scan','POST',{force:true},180000)}catch(e){toast('运行参数已保存；配置价格刷新失败：'+e.message)}
    }
    setTimeout(()=>{void refreshState(true)},1200);
    toast(oldEndpoint!==newEndpoint||oldCountry!==newCountry?'运行参数已保存；Endpoint/Country 已变化，请重新扫描付款组合':'运行参数已保存');
  }catch(e){toast(e.message)}
});
$('#saveAdvanced').addEventListener('click',saveAdvanced);
$('#loadPrices').addEventListener('click',loadPricesForDialog);
$('#inheritPrice').addEventListener('change',()=>{$('#priceProfile').disabled=$('#inheritPrice').checked;updatePriceProfileDetail()});
$('#priceProfile').addEventListener('change',updatePriceProfileDetail);
$('#resolveOptions').addEventListener('click',async()=>{try{const id=$('#dialogTargetId').value;await api('/api/target/resolve-options','POST',{id});await refreshState(false);openAdvanced(id);toast('已重新解析下单选项')}catch(e){toast(e.message)}});
$('#clearReview').addEventListener('click',async()=>{if(!confirm('确认已人工核对 OVH 订单/支付状态，并清除该目标 REVIEW 锁？'))return;try{const id=$('#dialogTargetId').value;await api('/api/target/clear-review','POST',{id});$('#advancedDialog').close();await refreshState(false);toast('REVIEW 已清除')}catch(e){toast(e.message)}});
$('#analyticsTarget').addEventListener('change',refreshAnalytics); $('#analyticsLookback').addEventListener('change',refreshAnalytics);
$('#replayDcMode').addEventListener('change',updateReplayDcUi); $('#runReplay').addEventListener('click',runReplay); $('#exportReplay').addEventListener('click',exportReplay); $('#runPortfolioReplay').addEventListener('click',runPortfolioReplay); $('#runOptimizer').addEventListener('click',runOptimizer); $('#exportOptimizer').addEventListener('click',exportOptimizer);
$('#refreshAnalytics').addEventListener('click',refreshAnalytics); $('#exportAnalytics').addEventListener('click',exportAnalytics);

$('#authConnect').addEventListener('click',async()=>{
  const token=$('#apiTokenInput').value.trim(); if(!token){setAuthMessage('Token 不能为空。','error');syncAuthConnectButton();return}
  setAuthMessage('正在验证…');
  const v=await validateApiToken(token);
  if(!v.ok){setAuthMessage(v.status===401?'Token 无效，请检查后重试。':'无法验证 Token，请检查网络或后端状态。','error');return}
  state.auth.token=token;state.auth.authenticated=true;state.auth.storedTokenInvalid=false;
  setStoredApiToken(token,$('#rememberApiToken').checked);
  $('#apiTokenInput').value='';syncAuthConnectButton();setAuthMessage('');updateAuthUi();$('#authDialog').close();finishAuthWait();
  void refreshState(true);void refreshAnalytics();
});
$('#authForget').addEventListener('click',()=>{
  const wasAuthenticated=state.auth.authenticated;
  setStoredApiToken('',false);state.auth.token='';state.auth.authenticated=!state.auth.required;state.auth.storedTokenInvalid=false;updateAuthUi();
  $('#apiTokenInput').value='';syncAuthConnectButton();
  if(state.auth.required){
    beginAuthWait();
    showAuthDialog(wasAuthenticated?'已注销并清除本地 Token。请输入新 Token 重新认证。':'已保存 Token 已清除，请输入新 Token。',true);
  }
});
$('#authStatusBtn').addEventListener('click',()=>{
  if(state.auth.required){
    state.auth.storedTokenInvalid=!state.auth.authenticated&&hasStoredApiToken();
    showAuthDialog('',!state.auth.authenticated);
  }
});$('#authClose').addEventListener('click',()=>closeAuthDialog());
$('#authCancel').addEventListener('click',()=>closeAuthDialog());
$('#authDialog').addEventListener('cancel',e=>{
  e.preventDefault();
  closeAuthDialog();
});
$('#authDialog').addEventListener('click',e=>{
  if(e.target===$('#authDialog'))closeAuthDialog();
});
$('#apiTokenInput').addEventListener('input',syncAuthConnectButton);
$('#apiTokenInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!$('#authConnect').disabled){e.preventDefault();$('#authConnect').click()}});
$('#authTokenToggle').addEventListener('click',()=>{
  const i=$('#apiTokenInput'),b=$('#authTokenToggle');
  const showing=i.type==='text'; i.type=showing?'password':'text';
  b.textContent=showing?'👁':'🙈'; b.setAttribute('aria-label',showing?'显示 Token':'隐藏 Token'); b.title=showing?'显示 Token':'隐藏 Token';
  i.focus();
});

(async()=>{
  try{await initAuth();await refreshState(true);await refreshAnalytics();}
  catch(e){toast('初始化失败：'+e.message);return}
  setInterval(()=>{void refreshState(false)},3000);
  setInterval(()=>{void refreshAnalytics()},15000);
})();
})();