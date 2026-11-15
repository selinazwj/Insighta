const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/niche_request.js','utf8');
function fixture(checked) {
  function el(){return {hidden:false,disabled:false,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};}
  const ids=Object.fromEntries(['nicheRecruitment','nicheDetails','nicheRequestDetails','nicheSubmitSummary','submitLabel','submitBtn','wizardTitle','wizardNext','wizardBack','listingPlanAudience'].map(id=>[id,el()]));
  const form=el(),payment=el(),labels=Array.from({length:5},el),classes=new Set();
  ids.nicheRecruitment.form=form;ids.nicheRecruitment.checked=checked;
  const context=vm.createContext({document:{getElementById:id=>ids[id],querySelector:()=>payment,querySelectorAll:()=>labels,
    body:{classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);}}}}});
  vm.runInContext("const WIZARD_TITLES=['Basics','Audience','Review','Budget','Payment & publish'];let currentWizardStep=5;",context);
  vm.runInContext(source,context);
  return {ids,form,payment,labels,classes};
}
test('niche requests require audience details and replace payment with request submission',()=>{
  const {ids,payment,labels,classes}=fixture(true);
  assert.equal(payment.hidden,true);assert.equal(ids.nicheSubmitSummary.hidden,false);
  assert.equal(ids.nicheDetails.required,true);assert.equal(ids.nicheDetails.disabled,false);
  assert.equal(ids.submitLabel.textContent,'Submit requirements');assert.equal(labels[4].textContent,'Submit');
  assert(classes.has('niche-request-mode'));
});
test('switching to standard recruitment restores payment and removes hidden-field validation',()=>{
  const {ids,payment}=fixture(true);ids.nicheRecruitment.checked=false;ids.nicheRecruitment.listeners.change();
  assert.equal(payment.hidden,false);assert.equal(ids.nicheSubmitSummary.hidden,true);
  assert.equal(ids.nicheDetails.required,false);assert.equal(ids.nicheDetails.disabled,true);
  assert.equal(ids.submitLabel.textContent,'Pay & Publish');
});
