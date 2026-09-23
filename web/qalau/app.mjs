import {loadOptions, loadScenarios, selectProfiles} from './api.mjs';
import {groups, services as serviceDescriptions} from './services.mjs';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const money = value => new Intl.NumberFormat('ru-RU').format(value);
const date = value => new Date(value + 'T12:00:00').toLocaleDateString('ru-RU', {day:'numeric', month:'long', year:'numeric'});
const check = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>';
let options, services = [], scenarios = [], ready = false;
let group = 0, screen = 'services', lastQuery = null, ticket = 0;

function query() {
  return Object.fromEntries([...new FormData($('#search'))].map(([key, value]) =>
    [key, ['hours', 'budget'].includes(key) ? Number(value) : value]));
}
function findServices(value) {
  const term = value.trim().toLocaleLowerCase('ru');
  return services.filter(service => `${service.name} ${service.aliases}`.toLocaleLowerCase('ru').includes(term));
}
function applyScenario(scenario) {
  for (const [key, value] of Object.entries(scenario.query)) $('#' + key).value = value;
  group = services.find(service => service.name === $('#category').value)?.group ?? 0;
  $('#service-search').value = '';
  renderServices();
  updateChosen();
}
function updateChosen() {
  const service = services.find(service => service.name === $('#category').value)
    ?? {name: $('#category').value, description: '', group: -1};
  $('#selected-name').textContent = service.name;
  $('#chosen-name').textContent = service.name;
  $('#chosen-description').textContent = service.description;
  const illustration = $('#chosen-illustration');
  const asset = service.group === 1 ? 'photo' : ['Декоратор', 'Флорист'].includes(service.name)
    ? 'decor' : ['Ведущий', 'Ведущий церемонии', 'Лайв-бэнд'].includes(service.name) ? 'host' : null;
  illustration.hidden = !asset;
  if (asset) {
    illustration.src = 'assets/generated/service-' + asset + '.png';
    illustration.dataset.kind = asset;
  } else {
    illustration.removeAttribute('src');
    delete illustration.dataset.kind;
  }
  $('#context-summary').textContent = `${$('#city').value} · ${$('#format').value}`;
  $('#dirty').hidden = !lastQuery || JSON.stringify(lastQuery) === JSON.stringify(query());
}
function renderServices() {
  const term = $('#service-search').value;
  $('#groups').innerHTML = groups.map((name, index) => `<button type="button" data-group="${index}" aria-pressed="${!term.trim() && group === index}">${esc(name)}<small>${services.filter(s => s.group === index).length}</small></button>`).join('');
  const shown = term.trim() ? findServices(term) : services.filter(service => service.group === group);
  $('#group-title').textContent = term.trim() ? 'Результаты поиска' : groups[group];
  $('#service-count').textContent = `Услуг: ${shown.length}`;
  $('#service-list').innerHTML = shown.length ? shown.map(service => {
    const count = options.counts[$('#city').value]?.[service.name] ?? 0;
    const selected = service.name === $('#category').value;
    return `<button type="button" class="service" data-service="${esc(service.name)}" aria-pressed="${selected}"><span class="service-name">${esc(service.name)}<span class="choice-mark" aria-hidden="true">${selected ? check : ''}</span></span><span class="service-description">${esc(service.description)}</span><span class="service-data">Профилей: ${count}${count ? '' : ' · Пока нет в городе'}</span></button>`;
  }).join('') : '<div class="no-services"><h3>Такой услуги не нашли</h3><p>Попробуйте другое название или вернитесь к группам.</p><button type="button" class="text-button" data-clear>Сбросить поиск</button></div>';
}
function go(next, focus = true) {
  if (next === 'results' && !lastQuery) return;
  screen = next;
  for (const id of ['services', 'conditions', 'results']) $('#' + id + '-screen').hidden = id !== next;
  $$('[data-step]').forEach(button => {
    if (!button.closest('.journey')) return;
    button.removeAttribute('aria-current');
    if (button.dataset.step === next) button.setAttribute('aria-current', 'step');
    if (button.dataset.step === 'results') button.disabled = !lastQuery;
  });
  updateChosen();
  if (focus) {
    $('#' + {services:'service-title', conditions:'conditions-title', results:'results-title'}[next]).focus({preventScroll:true});
    window.scrollTo({top:0, behavior:'instant'});
  }
}
function validate() {
  const q = query();
  let first = null;
  for (const id of ['date', 'budget', 'hours']) {
    let text = '';
    if (id === 'date' && (!q.date || q.date < options.calendar_start || q.date > options.calendar_end))
      text = `Выберите дату с ${date(options.calendar_start)} по ${date(options.calendar_end)}.`;
    if (id === 'budget' && (!Number.isInteger(q.budget) || q.budget <= 0)) text = 'Укажите целый бюджет больше нуля.';
    if (id === 'hours' && (!Number.isInteger(q.hours) || ($('#hours').value !== '' && q.hours < 1) || q.hours > 24))
      text = 'Укажите от 1 до 24 целых часов или оставьте поле пустым.';
    $('#' + id + '-error').textContent = text;
    $('#' + id).setAttribute('aria-invalid', String(!!text));
    if (text && !first) first = id;
  }
  if (!first) return true;
  go('conditions', false);
  if (first === 'hours') $('#extra').open = true;
  $('#' + first).focus();
  return false;
}
const reasonLabels = {busy:'заняты на выбранную дату', budget:'начальная цена выше бюджета', format:'не работают с этим форматом', language:'не работают на выбранном языке', hours:'не подходят по длительности'};
const fields = {busy:['date', 'Изменить дату'], budget:['budget', 'Изменить бюджет'], format:['format', 'Изменить формат'], language:['language', 'Изменить язык'], hours:['hours', 'Изменить длительность']};
const explanationSources = {llm:'языковая модель', template:'шаблон'};
const semanticBackends = {embeddings:'эмбеддинги', lexical:'лексическая'};
const editButton = (field, label) => `<button class="text-button" data-edit="${field}">${label} <span aria-hidden="true">↗</span></button>`;
function edit(field) {
  go(['city', 'format', 'category'].includes(field) ? 'services' : 'conditions', false);
  if (['hours', 'language'].includes(field)) $('#extra').open = true;
  const input = $(field === 'category' ? '#service-search' : '#' + field);
  input.focus();
  input.scrollIntoView({block:'center'});
}
function card(profile, index) {
  const facts = profile.facts;
  return `<article class="vendor card" data-profile-id="${esc(profile.id)}">
    <div><span class="rank" aria-label="Место в подборе">${index + 1}</span><h3 class="vendor-name">${esc(profile.name)}</h3><p class="vendor-meta">${esc(profile.category)} · ${esc(profile.city)}</p></div>
    <div class="badges">${profile.badges.map(badge => `<span>${esc(badge)}</span>`).join('')}</div>
    <div class="price"><small>от</small> ${money(profile.price)} ₸<p>Начальная цена</p></div>
    <div class="available">${check}<span>Свободен · ${date(facts.free_on_date)}<small>По данным календаря</small></span></div>
    <div class="why"><h4>Почему подходит</h4><p class="explanation">${esc(profile.explanation)}</p></div>
    <dl class="facts"><div><dt>Запас бюджета</dt><dd>${facts.budget_headroom_pct} %</dd></div><div><dt>Формат</dt><dd>${esc(facts.format_matched)}</dd></div><div><dt>Языки</dt><dd>${esc(facts.languages_matched.join(', ')) || 'Не указаны'}</dd></div><div><dt>Длительность</dt><dd>${facts.max_hours === null ? 'Без привязки к часам' : 'До ' + facts.max_hours + ' часов'}</dd></div></dl>
    <p class="source">Источник объяснения: ${esc(explanationSources[profile.explanation_source] ?? profile.explanation_source)}</p>
  </article>`;
}
function renderResult(result) {
  $('#results-title').textContent = 'Результат подбора.';
  $('#results-subtitle').textContent = `Подходящих профилей: ${result.total}. В категории и городе: ${result.pool}.`;
  const reasons = Object.entries(result.reasons).filter(([, count]) => count);
  const banner = `<div id="banner" class="outcome-banner" data-outcome="${esc(result.outcome)}"><h2 id="outcome-title">${esc(result.outcome_title_ru)}</h2>${result.shortfall_note ? `<p id="shortfall">${esc(result.shortfall_note)}</p>` : ''}</div>`;
  let content = result.state === 'success' ? `<div class="comparison">${result.items.map(card).join('')}</div>` : '<div class="empty">';
  if (result.state === 'absent') content += `<div class="empty-actions">${editButton('category', 'Выбрать другую услугу')}${editButton('city', 'Изменить город')}</div></div>`;
  if (result.state === 'filtered') content += `<ul>${reasons.map(([key, count]) => `<li>${count} — ${reasonLabels[key]}</li>`).join('')}</ul><p>У одного профиля может быть несколько причин.</p><div class="empty-actions">${reasons.map(([key]) => editButton(...fields[key])).join('')}</div></div>`;
  const rejected = result.rejections.length ? `<details class="rejections"><summary>Почему остальные не попали (${result.rejections.length})</summary><ul id="rejections-list">${result.rejections.map(item => `<li><strong>${esc(item.name)}</strong>: ${item.reasons.map(reason => esc(reason.label)).join('; ')}</li>`).join('')}</ul></details>` : '';
  const sources = [...new Set(result.items.map(item => item.explanation_source))];
  const footer = `<p id="result-footer" class="result-footer" data-semantic-backend="${esc(result.semantic_backend)}" data-explanation-source="${esc(sources.join(', '))}">Семантика: ${esc(semanticBackends[result.semantic_backend] ?? result.semantic_backend)} · ответ за ${money(Math.round(result.timing_ms))} мс · источник объяснений: ${sources.map(source => esc(explanationSources[source] ?? source)).join(', ') || 'нет карточек'}</p>`;
  $('#result-content').innerHTML = banner + content + rejected + footer;
  $('#price-note').hidden = result.state !== 'success';
}
async function run() {
  if (!ready || !validate()) return;
  const id = ++ticket, q = query();
  lastQuery = q;
  go('results');
  $('#submit').disabled = true;
  $('#result-content').setAttribute('aria-busy', 'true');
  $('#results-title').textContent = 'Подбираем варианты…';
  $('#results-subtitle').textContent = 'Проверяем условия и доступность в каталоге.';
  $('#query-summary').innerHTML = [q.category, q.city, q.format, date(q.date), `до ${money(q.budget)} ₸`, q.language, q.hours ? q.hours + ' ч' : ''].filter(Boolean).map(text => `<span>${esc(text)}</span>`).join('');
  $('#price-note').hidden = true;
  $('#result-content').innerHTML = '<div class="loading"><span class="spinner" aria-hidden="true"></span>Подбираем подрядчиков по вашим условиям</div>';
  try {
    const result = await selectProfiles(q);
    if (id !== ticket) return;
    renderResult(result);
  } catch (error) {
    if (id !== ticket) return;
    $('#results-title').textContent = 'Не получилось завершить подбор.';
    $('#results-subtitle').textContent = 'Параметры сохранены.';
    $('#result-content').innerHTML = `<div id="banner" class="outcome-banner" data-outcome="error" role="alert"><h2 id="outcome-title">${esc(error.message)}</h2></div><div class="empty-actions"><button class="primary" data-action="retry">Повторить подбор</button></div>`;
  } finally {
    if (id === ticket) {
      $('#submit').disabled = false;
      $('#result-content').removeAttribute('aria-busy');
      updateChosen();
    }
  }
}

$('#continue').addEventListener('click', () => go('conditions'));
$('#service-search').addEventListener('input', renderServices);
$('#service-search').addEventListener('keydown', event => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  const matches = findServices(event.target.value);
  if (matches.length === 1) {
    $('#category').value = matches[0].name;
    renderServices();
    updateChosen();
  }
});
$('#search').addEventListener('submit', event => {event.preventDefault(); if (screen === 'services') go('conditions'); else run();});
$('#search').addEventListener('input', updateChosen);
$('#city').addEventListener('change', () => {renderServices(); updateChosen();});
document.addEventListener('click', event => {
  if (event.target.closest('[data-action="reload"]')) location.reload();
  if (!ready) return;
  const step = event.target.closest('[data-step]');
  if (step && !step.disabled) {event.preventDefault(); go(step.dataset.step);}
  const groupButton = event.target.closest('[data-group]');
  if (groupButton) {
    if (groupButton.closest('footer')) go('services');
    group = Number(groupButton.dataset.group);
    $('#service-search').value = '';
    renderServices();
    $('#groups [data-group="' + group + '"]').focus();
  }
  const service = event.target.closest('[data-service]');
  if (service) {
    $('#category').value = service.dataset.service;
    renderServices(); updateChosen();
    $$('[data-service]').find(button => button.dataset.service === $('#category').value)?.focus();
  }
  if (event.target.closest('[data-clear]')) {$('#service-search').value = ''; renderServices(); $('#service-search').focus();}
  const editTarget = event.target.closest('[data-edit]');
  if (editTarget) edit(editTarget.dataset.edit);
  if (event.target.closest('[data-action="retry"]')) run();
  const scenario = event.target.closest('[data-scenario]');
  if (scenario) {
    applyScenario(scenarios[Number(scenario.dataset.scenario)]);
    for (const id of ['date', 'budget', 'hours']) {$('#' + id + '-error').textContent = ''; $('#' + id).removeAttribute('aria-invalid');}
    run();
  }
  if (event.target.closest('[data-action="reset"]')) {
    ticket++;
    lastQuery = null;
    $('#submit').disabled = false;
    $('#result-content').removeAttribute('aria-busy');
    if (scenarios.length) applyScenario(scenarios[0]);
    go('services');
  }
});

async function init() {
  $('#search').inert = true;
  try {
    [options, scenarios] = await Promise.all([loadOptions(), loadScenarios()]);
    services = options.categories.map(name => serviceDescriptions.find(service => service.name === name)
      ?? {name, group:0, description:'Услуга из каталога', aliases:''});
    for (const [id, key] of [['city', 'cities'], ['format', 'formats'], ['language', 'languages']]) {
      $('#' + id).replaceChildren(...(id === 'language' ? [new Option('Не имеет значения', '')] : []), ...options[key].map(value => new Option(value, value)));
    }
    $('#date').min = options.calendar_start;
    $('#date').max = options.calendar_end;
    $('#date-help').textContent = `Календарь: ${date(options.calendar_start)} — ${date(options.calendar_end)}`;
    $('#category').value = services[0]?.name ?? '';
    if (!services.length) throw new Error('В каталоге пока нет услуг. Попробуйте позже.');
    if (scenarios.length) applyScenario(scenarios[0]);
    else {$('#date').value = options.calendar_start; renderServices();}
    const labels = {dense:'Три варианта', rare:'Редкая категория', empty_no_category:'Нет категории', empty_none_eligible:'Никто не подходит', date_pair_a:'Первая дата', date_pair_b:'Другая дата'};
    $('#demos').innerHTML = scenarios.map((scenario, index) => `<button type="button" data-scenario="${index}" title="${esc(scenario.note ?? '')}">${esc(labels[scenario.name] ?? scenario.name)}</button>`).join('');
    ready = true;
    $('#search').inert = false;
    $('#init-status').hidden = true;
    go('services', false);
    const example = new URLSearchParams(location.search).get('example');
    if (example) {
      const scenario = scenarios.find(item => item.name === example) ?? scenarios[Number(example) - 1];
      if (scenario) {applyScenario(scenario); await run();}
    }
  } catch (error) {
    $('#init-status').innerHTML = `${esc(error.message)} <button type="button" class="text-button" data-action="reload">Повторить загрузку</button>`;
    $('#init-status').setAttribute('role', 'alert');
  }
}
init();
