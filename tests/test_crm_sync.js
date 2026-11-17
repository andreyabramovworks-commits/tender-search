const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const context = vm.createContext({
  Utilities: {formatDate: (value, zone) => {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(value);
    const get = type => parts.find(part => part.type === type).value;
    return `${get('year')}-${get('month')}-${get('day')}`;
  }},
  Date: class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : ['2026-11-11T12:00:00+03:00'])); }
  },
});
vm.runInContext(fs.readFileSync('crm_sync.gs', 'utf8'), context);

assert.equal(vm.runInContext("crmDeadlineFuture_('2026-11-10T23:59:00+03:00')", context), false);
assert.equal(vm.runInContext("crmDeadlineFuture_('2026-11-11T23:59:00+03:00')", context), false);
assert.equal(vm.runInContext("crmDeadlineFuture_('2026-11-13T14:00:00+03:00')", context), true);
assert.equal(vm.runInContext("crmDeadlineFuture_('2026-11-12T00:30:00+05:00')", context), false);
assert.equal(vm.runInContext("crmDocumentLinks_('Источник карточки: https://docs.google.com/spreadsheets/d/source\\nТЗ: https://docs.google.com/document/d/file')", context).length, 1);
context.testRow = Array(23).fill('');
context.testRow[0] = '2834719617';
context.testRow[1] = 'Закупка и доставка мобильных ограждений';
context.testRow[2] = '2026-11-13T14:00:00+03:00';
context.testRow[3] = '11 656 742,8';
context.testRow[5] = 'https://bidzaar.com/process/example';
context.testRow[6] = 'Подходит';
context.testRow[7] = 'Ораева Алина';
context.testRow[18] = 'ТЗ: https://docs.google.com/document/d/file';
context.testSource = Array(19).fill('');
context.testSource[1] = 'Закупка и доставка мобильных ограждений';
context.testSource[2] = '394-135';
context.testSource[10] = 'ООО Заказчик';
context.testSource[14] = 'RUB';
context.testSource[17] = '2026-11-06T19:20:35+03:00';
vm.runInContext('crmSourceById_ = () => testSource', context);
context.testSheet = {getRange: () => ({getDisplayValues: () => [context.testRow]})};
const candidate = vm.runInContext('crmCandidate_(testSheet, 26, "2834719617")', context);
assert.equal(candidate.fields.ASSIGNED_BY_ID, 40);
assert.equal(candidate.fields.STAGE_ID, 'C2:PREPAYMENT_INVOICE');
assert.equal(candidate.fields.UF_CRM_1775828279108, 'ООО Заказчик');
assert.equal(candidate.fields.BEGINDATE, '2026-11-06');
assert.equal(candidate.fields.OPPORTUNITY, 11656742.8);
assert.equal(candidate.fields.CURRENCY_ID, 'RUB');
assert.equal(candidate.fields.COMMENTS.includes('docs.google.com/document/d/file'), true);
assert.equal('CLOSEDATE' in candidate.fields, false);
assert.equal('UF_CRM_1774965362471' in candidate.fields, false);
console.log('Apps Script eligibility and document-link checks passed');
