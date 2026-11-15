/* Optional planning. Applying an estimate edits the draft, never funds or publishes it. */
(() => {
  const planner = document.getElementById('listingBudgetPlanner');
  if (!planner) return;
  const get = id => document.getElementById(id);
  const target = get(planner.dataset.targetInput);
  const reward = get(planner.dataset.rewardInput);
  const calculate = get('listingPlanCalculate'), apply = get('listingPlanApply');
  const result = get('listingPlanResult'), message = get('listingPlanMessage');
  const money = cents => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(cents/100);
  let revision = 0, saved = null;
  const state = () => ({budget:get('listingPlanBudget').value,target_responses:target.value,
    study_type:planner.dataset.studyType || get('taskTypeSelect')?.value || 'survey',
    audience:get('listingPlanAudience').value,urgency_level:get('urgencyInput').value,
    incentive_type:get('incentiveInput').value});
  function invalidate() {revision++;saved=null;result.hidden=true;apply.hidden=true;message.textContent='';calculate.disabled=false;}
  planner.addEventListener('input',invalidate);
  target.addEventListener('input',invalidate);
  get('taskTypeSelect')?.addEventListener('change',invalidate);
  planner.closest('form').addEventListener('click',event=>{
    if(event.target.closest('[data-v],[data-value]'))invalidate();
  });
  calculate.addEventListener('click',async()=>{
    const input=state(), current=++revision;
    saved=null;result.hidden=true;apply.hidden=true;
    if(!['cash','gift_card'].includes(input.incentive_type)) {message.textContent='No per-person reward. The platform minimum still applies.';return;}
    calculate.disabled=true;message.textContent='Calculating…';
    try {
      const response=await fetch('/api/wallet/budget-estimate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)});
      const data=await response.json();
      if(current!==revision || JSON.stringify(input)!==JSON.stringify(state()))return;
      if(!response.ok)throw new Error(data.detail || 'Estimate unavailable.');
      if(data.status==='below_minimum') {message.textContent=`Budget too low. Minimum: ${money(data.minimum_budget_cents)}.`;return;}
      const q=data.quote;
      get('listingPlanReward').textContent=money(q.reward_per_person_cents);
      get('listingPlanFee').textContent=money(q.target_responses*q.recruitment_fee_per_person_cents);
      get('listingPlanTotal').textContent=money(q.total_cents);
      result.hidden=false;
      message.textContent=data.manual_quote_required?'Niche audience: confirm a manual quote before funding. General-audience reference only.':'';
      if(!data.manual_quote_required){saved={input,quote:q};apply.hidden=false;}
    } catch(error){if(current===revision)message.textContent=error.message;}
    finally{if(current===revision)calculate.disabled=false;}
  });
  get('listingPlannerForm').addEventListener('submit',event=>{event.preventDefault();calculate.click();});
  apply.addEventListener('click',()=>{
    if(!saved || JSON.stringify(saved.input)!==JSON.stringify(state())) {invalidate();return;}
    reward.value=(saved.quote.reward_per_person_cents/100).toFixed(2);
    reward.dispatchEvent(new Event('input',{bubbles:true}));
    apply.hidden=true;message.textContent='';planner.open=false;
    reward.focus({preventScroll:true});
  });
})();
