const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/static/budget_calculator.js'),'utf8');
function setup(fetch) {
  const elements=new Map();
  const get=id=>{
    if(!elements.has(id))elements.set(id,{value:'',hidden:false,disabled:false,textContent:'',handlers:{},addEventListener(type,fn){this.handlers[type]=fn},scrollIntoView(){this.scrolled=true},focus(){this.focused=true}});
    return elements.get(id);
  };
  get('calculatorBudget').value='500';get('calculatorParticipants').value='20';
  get('calculatorStudyType').value='survey';get('calculatorAudience').value='broad';
  vm.runInNewContext(source,{document:{getElementById:get},fetch,Intl});
  return {get,submit:()=>get('budgetCalculatorForm').handlers.submit({preventDefault(){}})};
}
const data={status:'estimated',quote:{reward_per_person_cents:2000,recruitment_fee_per_person_cents:500,target_responses:20,rate_bps:2000,minimum_fee_cents:500,minimum_applied:true,total_cents:50000},funding:{cash_balance_cents:0,promo_applied_cents:0},baseline_topup_cents:50000,suggested_topup_cents:50000,manual_quote_required:false,remaining_budget_cents:0};
const reply=body=>({ok:true,json:async()=>body});
test('estimate and apply fill amount without charging or opening checkout',async()=>{
  const calls=[];const ui=setup(async(url,options)=>{calls.push(url);assert.equal(JSON.parse(options.body).budget,'500');return reply(data)});
  await ui.submit();
  assert.equal(ui.get('calculatorTopup').textContent,'$500.00');
  ui.get('calculatorUseTopup').handlers.click();
  assert.equal(ui.get('topupAmount').value,'500.00');
  assert.equal(ui.get('topupAmount').focused,true);
  assert.deepEqual(calls,['/api/wallet/budget-estimate']);
});
test('niche audiences hide top-up action and show manual quote warning',async()=>{
  const ui=setup(async()=>reply({...data,status:'manual_quote_required',manual_quote_required:true,suggested_topup_cents:null}));
  await ui.submit();
  assert.equal(ui.get('calculatorNicheNote').hidden,false);
  assert.equal(ui.get('calculatorUseTopup').hidden,true);
  assert.match(ui.get('calculatorMessage').textContent,/Confirm a niche quote/);
});
test('changed inputs discard an older response and clear its funding recommendation',async()=>{
  let resolve;const ui=setup(()=>new Promise(r=>resolve=r));
  const pending=ui.submit();
  ui.get('calculatorBudget').value='100';ui.get('budgetCalculatorForm').handlers.input();
  resolve(reply(data));await pending;
  assert.equal(ui.get('calculatorResults').hidden,true);
  assert.equal(ui.get('calculateBudgetButton').disabled,false);
  ui.get('calculatorUseTopup').handlers.click();
  assert.equal(ui.get('topupAmount').value,'');
});
