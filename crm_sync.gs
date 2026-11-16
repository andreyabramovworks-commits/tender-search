/** Bitrix24 deal sync for the manager AI-анализ sheet. Install in its Apps Script project. */
const CRM_MANAGER_BOOK = '10vC8qDqyGDCxf5dwZsrigr4MIriJ9q-3j_x62iuRaaA';
const CRM_SOURCE_BOOK = '1-bZ7tvZ2f0yGy8_lue_v8nrWJh-3xGqCvAEZATn6X2g';
const CRM_TAB = 'AI-анализ';
const CRM_SOURCE_TAB = 'Тендеры';
const CRM_MANAGERS = {
  'Ораева Алина': 40, 'Сметанин Олег': 48,
  'Колесникова Анастасия': 18, 'Колесников Данил': 68,
  'Прокопьев Александр': 120,
};
const CRM_REGISTRY_FIELD = 'UF_CRM_1775828080057';
const CRM_FIELDS = {
  object: 'UF_CRM_1775828178357', customer: 'UF_CRM_1775828279108',
  deadline: 'UF_CRM_1775828555673', source: 'UF_CRM_1775828570890',
};
const CRM_HEADERS = {
  A: 'ID TenderLand', B: 'Объект закупки', C: 'Окончание подачи заявок',
  F: 'Ссылка на первоисточник', G: 'Решение менеджера', H: 'Менеджер',
  S: 'Доказательства и ссылки', V: 'Статус CRM',
  W: 'Кнопка для создания картотчки в битрикс',
};

function crmManagerSheet_() {
  const sheet = SpreadsheetApp.openById(CRM_MANAGER_BOOK).getSheetByName(CRM_TAB);
  if (!sheet) throw new Error('В менеджерской таблице нет вкладки AI-анализ');
  if (sheet.getMaxColumns() < 23) sheet.insertColumnsAfter(sheet.getMaxColumns(), 23 - sheet.getMaxColumns());
  const headers = sheet.getRange(1, 1, 1, 23).getDisplayValues()[0];
  for (const [letter, title] of Object.entries(CRM_HEADERS)) {
    const index = letter.charCodeAt(0) - 65;
    if (headers[index] && headers[index] !== title) {
      throw new Error(`Колонка ${letter} занята: ${headers[index]}`);
    }
    if (!headers[index] && (letter === 'V' || letter === 'W')) sheet.getRange(1, index + 1).setValue(title);
    else if (!headers[index]) throw new Error(`Нет заголовка ${title} в колонке ${letter}`);
  }
  return sheet;
}

function crmApi_(method, params) {
  const base = PropertiesService.getScriptProperties().getProperty('BITRIX_WEBHOOK_URL');
  if (!/^https:\/\/[^/]+\.bitrix24\.ru\/rest\/\d+\/[^/]+\/?$/.test(base || '')) {
    throw new Error('Укажите BITRIX_WEBHOOK_URL в свойствах скрипта');
  }
  const response = UrlFetchApp.fetch(base.replace(/\/$/, '') + '/' + method + '.json', {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(params || {}),
    muteHttpExceptions: true,
  });
  const body = JSON.parse(response.getContentText());
  if (response.getResponseCode() >= 400 || body.error) {
    throw new Error(`Bitrix24 ${method}: ${body.error || response.getResponseCode()}`);
  }
  return body.result;
}

function crmSourceById_(id) {
  const sheet = SpreadsheetApp.openById(CRM_SOURCE_BOOK).getSheetByName(CRM_SOURCE_TAB);
  if (!sheet) throw new Error('Нет исходной вкладки Тендеры');
  const headers = sheet.getRange(1, 1, 1, 19).getDisplayValues()[0];
  if (headers[0] !== 'ID TenderLand' || headers[1] !== 'Название' ||
      headers[2] !== 'Номер закупки' || headers[10] !== 'Заказчик') {
    throw new Error('Структура исходной таблицы изменилась');
  }
  const finder = sheet.getRange(2, 1, Math.max(sheet.getLastRow() - 1, 1), 1)
    .createTextFinder(id).matchEntireCell(true);
  const hits = finder.findAll();
  if (hits.length !== 1) throw new Error(`ID ${id} должен иметь ровно одну строку в Тендерах`);
  return sheet.getRange(hits[0].getRow(), 1, 1, 19).getDisplayValues()[0];
}

function crmDeadlineFuture_(text) {
  const raw = String(text || '').trim();
  const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const ru = raw.match(/^(\d{2})\.(\d{2})\.(\d{4})/);
  let day = '';
  if (match && /(?:Z|[+-]\d{2}:\d{2})$/i.test(raw)) {
    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) day = Utilities.formatDate(parsed, 'Europe/Moscow', 'yyyy-MM-dd');
  } else if (match) day = `${match[1]}-${match[2]}-${match[3]}`;
  else if (ru) day = `${ru[3]}-${ru[2]}-${ru[1]}`;
  return !!day && day > Utilities.formatDate(new Date(), 'Europe/Moscow', 'yyyy-MM-dd');
}

function crmDocumentLinks_(text) {
  const urls = [];
  for (const line of String(text || '').split('\n')) {
    if (line.includes('Источник карточки:')) continue;
    for (const hit of line.matchAll(/https:\/\/(?:docs\.google\.com|drive\.google\.com)\/[^\s<>"']+/g)) {
      const url = hit[0].replace(/[.,;)]+$/, '');
      if (!urls.includes(url)) urls.push(url);
    }
  }
  return urls;
}

function crmCandidate_(sheet, rowNumber, expectedId) {
  const values = sheet.getRange(rowNumber, 1, 1, 23).getDisplayValues()[0];
  const id = String(values[0]).trim();
  if (!id || (expectedId && id !== expectedId)) throw new Error('Строка тендера изменилась');
  if (values[6].trim() !== 'Подходит') throw new Error('Решение менеджера не «Подходит»');
  const manager = values[7].trim();
  if (!Object.prototype.hasOwnProperty.call(CRM_MANAGERS, manager)) {
    throw new Error('Менеджер не указан или отсутствует в сопоставлении Bitrix24');
  }
  if (values[21].trim()) return {id, already: values[21].trim()};
  if (!crmDeadlineFuture_(values[2])) throw new Error('Окончание подачи заявок сегодня или уже прошло');
  const source = crmSourceById_(id);
  let object = values[1].trim();
  let customer = source[10].trim();
  const marker = object.match(/(?:^|;)\s*заказчик\s*:\s*/i);
  if (marker) {
    const suffix = object.slice(marker.index + marker[0].length);
    customer = suffix.split(/;\s*(?:закупка|лот)\b/i)[0].trim().replace(/;$/, '');
    object = object.slice(0, marker.index).trim().replace(/;$/, '');
  }
  const sourceUrl = values[5].trim() || source[5].trim();
  if (!object || !customer || !/^https?:\/\//.test(sourceUrl) || !source[1] || !source[2]) {
    throw new Error('Не хватает объекта, заказчика, первоисточника, названия или номера закупки');
  }
  const links = crmDocumentLinks_(values[18]);
  const fields = {
    TITLE: '[RTM Radar - METAL] ' + source[1], CATEGORY_ID: 2,
    STAGE_ID: 'C2:PREPAYMENT_INVOICE', ASSIGNED_BY_ID: CRM_MANAGERS[manager],
    [CRM_REGISTRY_FIELD]: source[2], [CRM_FIELDS.object]: object,
    [CRM_FIELDS.customer]: customer, [CRM_FIELDS.deadline]: values[2],
    [CRM_FIELDS.source]: sourceUrl,
    COMMENTS: `ID TenderLand: ${id}\nДокументы:\n${links.join('\n')}`,
  };
  const start = source[17].match(/^(\d{4}-\d{2}-\d{2})/);
  if (start) fields.BEGINDATE = start[1];
  return {id, registry: source[2], fields};
}

function crmProcessRow_(sheet, rowNumber, expectedId) {
  const candidate = crmCandidate_(sheet, rowNumber, expectedId);
  if (candidate.already) return candidate.already;
  const found = crmApi_('crm.deal.list', {
    filter: {[CRM_REGISTRY_FIELD]: candidate.registry, CATEGORY_ID: 2},
    select: ['ID', 'TITLE', 'COMMENTS', CRM_REGISTRY_FIELD],
  });
  let id;
  if (found.length) {
    const matching = found.filter(deal => String(deal.COMMENTS || '').includes(`ID TenderLand: ${candidate.id}`));
    if (matching.length !== 1 || found.length !== 1) {
      throw new Error('В CRM есть сделка с тем же реестровым номером; проверьте вручную');
    }
    id = matching[0].ID;
  } else {
    try {
      id = crmApi_('crm.deal.add', {fields: candidate.fields, params: {REGISTER_SONET_EVENT: 'N'}});
    } catch (error) {
      // An uncertain response may follow a successful create. The next run recovers by ID in COMMENTS.
      throw new Error(`Создание не подтверждено; повторная проверка CRM нужна: ${error.message}`);
    }
  }
  const deal = crmApi_('crm.deal.get', {id});
  if (String(deal[CRM_REGISTRY_FIELD]) !== candidate.registry ||
      String(deal.ASSIGNED_BY_ID) !== String(candidate.fields.ASSIGNED_BY_ID) ||
      deal.STAGE_ID !== candidate.fields.STAGE_ID) {
    throw new Error(`Сделка #${id} создана, но данные не совпали; проверьте её вручную`);
  }
  const currentId = String(sheet.getRange(rowNumber, 1).getDisplayValue()).trim();
  if (currentId !== candidate.id) throw new Error(`Сделка #${id} создана, но строка переместилась`);
  const statusCell = sheet.getRange(rowNumber, 22);
  if (statusCell.getDisplayValue().trim()) throw new Error(`Сделка #${id} создана, но статус строки уже занят`);
  const status = `В CRM: сделка #${id}`;
  statusCell.setValue(status);
  SpreadsheetApp.flush();
  if (statusCell.getDisplayValue() !== status) throw new Error(`Сделка #${id} создана, но статус не подтвердился`);
  sheet.getRange(rowNumber, 23).setValue('В CRM');
  return status;
}

function crmWithLock_(callback) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try { return callback(); } finally { lock.releaseLock(); }
}

/** Called by one time-driven trigger every 15 minutes. */
function syncApprovedTendersToBitrix() {
  return crmWithLock_(() => {
    const sheet = crmManagerSheet_();
    refreshCrmButtons();
    const rows = sheet.getRange(2, 1, Math.max(sheet.getLastRow() - 1, 1), 22).getDisplayValues();
    const report = {created: 0, skipped: 0, errors: []};
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      if (!row[0] || row[6] !== 'Подходит' || !row[7] || row[21]) continue;
      if (!crmDeadlineFuture_(row[2])) { report.skipped++; continue; }
      try { crmProcessRow_(sheet, i + 2, row[0]); report.created++; }
      catch (error) { report.errors.push({tenderId: row[0], message: error.message}); }
    }
    console.log(JSON.stringify(report));
    return report;
  });
}

/** A styled W-cell link opens this web app for the exact TenderLand ID. */
function doGet(event) {
  const id = String((event && event.parameter && event.parameter.id) || '').trim();
  const signature = String((event && event.parameter && event.parameter.sig) || '');
  let message;
  try {
    if (!/^\d+$/.test(id) || signature !== crmSignature_(id)) throw new Error('Ссылка кнопки недействительна');
    message = crmWithLock_(() => {
      const sheet = crmManagerSheet_();
      const hits = sheet.getRange(2, 1, Math.max(sheet.getLastRow() - 1, 1), 1)
        .createTextFinder(id).matchEntireCell(true).findAll();
      if (hits.length !== 1) throw new Error('Тендер не найден однозначно');
      return crmProcessRow_(sheet, hits[0].getRow(), id);
    });
  } catch (error) { message = error.message; }
  return HtmlService.createHtmlOutput(`<p>${String(message).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}</p>`);
}

function crmSignature_(id) {
  const secret = PropertiesService.getScriptProperties().getProperty('CRM_BUTTON_SECRET');
  if (!secret) throw new Error('Не настроен секрет кнопок CRM');
  return Utilities.computeHmacSha256Signature(id, secret)
    .map(byte => ('0' + (byte & 255).toString(16)).slice(-2)).join('');
}

/** Run after publishing this project as an organization-only web app. */
function setupCrmIntegration() {
  const props = PropertiesService.getScriptProperties();
  const appUrl = props.getProperty('CRM_WEB_APP_URL');
  if (!/^https:\/\/script\.google\.com\/macros\/s\/.+\/exec$/.test(appUrl || '')) {
    throw new Error('Укажите URL развёрнутого веб-приложения в CRM_WEB_APP_URL');
  }
  if (!props.getProperty('CRM_BUTTON_SECRET')) props.setProperty('CRM_BUTTON_SECRET', Utilities.getUuid());
  crmApi_('crm.category.list', {entityTypeId: 2});
  const sheet = crmManagerSheet_();
  const triggers = ScriptApp.getProjectTriggers();
  if (!triggers.some(t => t.getHandlerFunction() === 'syncApprovedTendersToBitrix')) {
    ScriptApp.newTrigger('syncApprovedTendersToBitrix').timeBased().everyMinutes(15).create();
  }
  refreshCrmButtons();
  return `Готово: ${sheet.getName()}, CRM таймер каждые 15 минут`;
}

/** Run after new manager rows are appended; safe to rerun. */
function refreshCrmButtons() {
  const sheet = crmManagerSheet_();
  const appUrl = PropertiesService.getScriptProperties().getProperty('CRM_WEB_APP_URL');
  if (!appUrl) throw new Error('Не задан CRM_WEB_APP_URL');
  const count = sheet.getLastRow() - 1;
  if (count < 1) return;
  const ids = sheet.getRange(2, 1, count, 1).getDisplayValues();
  const statuses = sheet.getRange(2, 22, count, 1).getDisplayValues();
  const formulas = ids.map(([id], i) => {
    if (!/^\d+$/.test(id) || statuses[i][0]) return [statuses[i][0] ? 'В CRM' : ''];
    const url = `${appUrl}?id=${encodeURIComponent(id)}&sig=${crmSignature_(id)}`;
    return [`=HYPERLINK("${url}";"Отправить в CRM")`];
  });
  const range = sheet.getRange(2, 23, count, 1);
  range.setValues(formulas);
  range.setBackground('#1a73e8').setFontColor('#ffffff').setFontWeight('bold')
    .setHorizontalAlignment('center');
  sheet.setColumnWidth(23, 165);
}
