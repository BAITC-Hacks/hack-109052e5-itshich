// The server owns catalogue data, selection, ordering, facts and explanations.
async function request(path, init) {
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new Error('Не удалось связаться с сервером. Повторите попытку.');
  }
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error('Сервер вернул непонятный ответ. Повторите попытку.');
  }
  if (!response.ok) {
    throw new Error(typeof body.detail === 'string' ? body.detail
      : 'Не удалось обработать запрос. Проверьте условия и повторите попытку.');
  }
  return body;
}

export function loadOptions() {
  return request('/api/meta');
}

export async function loadScenarios() {
  const demos = await request('/api/demo');
  return demos.map(demo => ({
    ...demo,
    query: {
      city: demo.request.city,
      date: demo.request.event_date,
      format: demo.request.event_format,
      category: demo.request.category,
      budget: demo.request.budget_kzt,
      hours: demo.request.duration_hours ?? '',
      language: demo.request.language ?? '',
    },
  }));
}

const states = {matched: 'success', no_category_in_city: 'absent', none_eligible: 'filtered'};
const reasonKeys = {
  busy_on_date: 'busy', over_budget: 'budget', format_not_supported: 'format',
  language_not_supported: 'language', duration_exceeds_max: 'hours',
};

export async function selectProfiles(query) {
  const result = await request('/api/match', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      city: query.city, event_date: query.date, event_format: query.format,
      category: query.category, budget_kzt: Number(query.budget),
      duration_hours: query.hours ? Number(query.hours) : null,
      language: query.language || null,
    }),
  });
  const reasons = {busy: 0, budget: 0, format: 0, language: 0, hours: 0};
  for (const rejection of result.rejections) {
    for (const reason of rejection.reasons) {
      const key = reasonKeys[reason.code];
      if (key) reasons[key]++;
    }
  }
  return {
    ...result,
    state: states[result.outcome],
    items: result.cards.map(card => ({
      ...card,
      price: card.price_from_kzt,
      badges: [card.synthetic && 'Синтетический профиль',
        card.price_imputed && 'Цена проставлена', card.city_imputed && 'Город проставлен'].filter(Boolean),
    })),
    reasons,
    pool: result.pool_size,
    total: result.eligible_count,
  };
}
