const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync('app/static/listing_budget.js','utf8');
function setup(fetch){
 const fields=new Map();const get=id=>{if(!fields.has(id))fields.set(id,{value:'',hidden:false,disabled:false,textContent:'',handlers:{},addEventListener(k,fn){this.handlers[k]=fn},dispatchEvent(e){this.dispatched=e.type},focus(){this.focused=true}});return fields.get(id)};
 const planner=get('listingBudgetPlanner');planner.dataset={rewardInput:'reward',targetInput:'target',studyType:'interview'};planner.closest=()=>get('outerForm');
 get('target').value='20';get('listingPlanBudget').value='1000';get('listingPlanAudience').value='broad';get('incentiveInput').value='cash';get('urgencyInput').value='within_1_week';
 vm.runInNewContext(source,{document:{getElementById:get},fetch,Intl,Event});return{get,run:()=>get('listingPlanCalculate').handlers.click()};
}
const data={status:'estimated',manual_quote_required:false,quote:{reward_per_person_cents:3500,recruitment_fee_per_person_cents:1000,target_responses:20,total_cents:99000}};
const response=d=>({ok:true,json:async()=>d});
test('applying planner reward only edits draft and passes actual type/count/timeline',async()=>{
 const calls=[];const ui=setup(async(url,options)=>{calls.push(url);const body=JSON.parse(options.body);assert.equal(body.study_type,'interview');assert.equal(body.target_responses,'20');assert.equal(body.urgency_level,'within_1_week');return response(data)});
 await ui.run();ui.get('listingPlanApply').handlers.click();assert.equal(ui.get('reward').value,'35.00');assert.equal(ui.get('reward').dispatched,'input');assert.equal(ui.get('listingBudgetPlanner').open,false);assert.deepEqual(calls,['/api/wallet/budget-estimate']);
});
test('niche reference cannot be applied as a confirmed reward',async()=>{
 const ui=setup(async()=>response({...data,manual_quote_required:true,status:'manual_quote_required'}));await ui.run();assert.equal(ui.get('listingPlanApply').hidden,true);ui.get('listingPlanApply').handlers.click();assert.equal(ui.get('reward').value,'');
});
test('changed participant count discards pending estimate',async()=>{
 let resolve;const ui=setup(()=>new Promise(r=>resolve=r));const request=ui.run();ui.get('target').value='30';ui.get('target').handlers.input();resolve(response(data));await request;assert.equal(ui.get('listingPlanResult').hidden,true);assert.equal(ui.get('reward').value,'');
});
test('changed programmatic timeline cannot apply a stale estimate',async()=>{
 const ui=setup(async()=>response(data));await ui.run();ui.get('urgencyInput').value='flexible';ui.get('listingPlanApply').handlers.click();assert.equal(ui.get('reward').value,'');
});
