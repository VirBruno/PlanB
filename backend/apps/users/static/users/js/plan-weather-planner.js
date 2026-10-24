/* Plan B · Comparador meteorológico por provincia/localidad. No modifica datos del plan. */
(() => {
  "use strict";
  const planner = document.querySelector('[data-plan-weather]');
  if (!planner) return;

  const province = planner.querySelector('[data-weather-province]');
  const query = planner.querySelector('[data-weather-query]');
  const searchForm = planner.querySelector('[data-weather-search-form]');
  const searchButton = planner.querySelector('[data-weather-search]');
  const cityResults = planner.querySelector('[data-weather-cities]');
  const message = planner.querySelector('[data-weather-message]');
  const forecast = planner.querySelector('[data-weather-forecast]');
  const cityTitle = planner.querySelector('[data-weather-city-title]');
  const daysContainer = planner.querySelector('[data-weather-days]');
  const selectedContainer = planner.querySelector('[data-weather-selected]');
  const symbols = { sun: '☀️', 'sun-cloud': '🌤️', cloud: '☁️', rain: '🌧️', snow: '❄️', storm: '⛈️' };
  let currentDays = [];
  let requestSerial = 0;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const setMessage = (value) => { message.textContent = value; message.hidden = !value; };
  const dateLabel = (value, options) => new Intl.DateTimeFormat('es-AR', options).format(new Date(`${value}T12:00:00`));
  const temperature = day => `${day.temperature_min}° / ${day.temperature_max}°`;
  const rainLabel = value => value === null ? 'Lluvia: sin datos' : `Lluvia: ${value}%`;
  const windLabel = value => value === null ? 'Viento: sin datos' : `Viento máx.: ${value} km/h`;

  const clearResults = () => {
    cityResults.replaceChildren();
    cityResults.hidden = true;
    daysContainer.replaceChildren();
    selectedContainer.replaceChildren();
    forecast.hidden = true;
    currentDays = [];
  };
  const syncControls = () => {
    query.disabled = !province.value;
    searchButton.disabled = !province.value || query.value.trim().length < 3;
  };
  const getJson = async url => {
    const response = await fetch(url, {
      credentials: 'same-origin', headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    let data;
    try { data = await response.json(); } catch { throw new Error('No pudimos leer la respuesta del servicio.'); }
    if (!response.ok) throw new Error(data.message || 'No pudimos consultar el servicio meteorológico.');
    return data;
  };

  const selectDay = index => {
    const day = currentDays[index];
    if (!day) return;
    daysContainer.querySelectorAll('button').forEach((button, i) => {
      button.classList.toggle('is-selected', i === index);
      button.setAttribute('aria-pressed', String(i === index));
    });
    selectedContainer.replaceChildren();
    const top = el('div', 'pb-climate-selected-head');
    top.append(el('span', 'pb-climate-selected-symbol', symbols[day.icon] || '☁️'));
    const heading = el('div');
    heading.append(el('strong', '', dateLabel(day.date, { weekday: 'long', day: 'numeric', month: 'long' })));
    heading.append(el('span', '', day.condition));
    top.append(heading);
    const metrics = el('div', 'pb-climate-selected-metrics');
    metrics.append(el('span', '', `🌡️ ${temperature(day)}`));
    metrics.append(el('span', '', `☔ ${rainLabel(day.rain_probability)}`));
    metrics.append(el('span', '', `↗ ${windLabel(day.wind_max)}`));
    selectedContainer.append(top, metrics);
  };

  const renderForecast = data => {
    clearResults();
    currentDays = data.days || [];
    cityTitle.textContent = `${data.city}, ${data.province}`;
    currentDays.forEach((day, index) => {
      const button = el('button', 'pb-climate-day');
      button.type = 'button';
      button.setAttribute('aria-label', `${dateLabel(day.date, { weekday: 'long', day: 'numeric', month: 'long' })}: ${day.condition}, ${temperature(day)}`);
      button.append(el('span', 'pb-climate-day-week', dateLabel(day.date, { weekday: 'short' })));
      button.append(el('span', 'pb-climate-day-date', dateLabel(day.date, { day: 'numeric', month: 'short' })));
      button.append(el('span', 'pb-climate-day-symbol', symbols[day.icon] || '☁️'));
      button.append(el('span', 'pb-climate-day-temp', temperature(day)));
      button.append(el('span', 'pb-climate-day-rain', day.rain_probability === null ? 'Lluvia s/d' : `☔ ${day.rain_probability}%`));
      button.addEventListener('click', () => selectDay(index));
      daysContainer.append(button);
    });
    forecast.hidden = false;
    if (currentDays.length) selectDay(0);
    setMessage('');
  };

  const chooseCity = async city => {
    const serial = ++requestSerial;
    clearResults();
    setMessage(`Consultando el clima de ${city.name}…`);
    try {
      const params = new URLSearchParams({ provincia: province.value, localidad_id: String(city.id) });
      const data = await getJson(`${planner.dataset.forecastUrl}?${params}`);
      if (serial !== requestSerial) return;
      if (data.status !== 'available' || !Array.isArray(data.days)) throw new Error('Todavía no hay pronóstico para esa localidad.');
      renderForecast(data);
    } catch (error) {
      if (serial === requestSerial) setMessage(error.message || 'No pudimos consultar el clima.');
    }
  };

  province.addEventListener('change', () => {
    ++requestSerial;
    query.value = '';
    clearResults();
    syncControls();
    setMessage(province.value ? 'Escribí al menos tres letras de una localidad.' : 'Seleccioná provincia y localidad para explorar el pronóstico.');
  });
  query.addEventListener('input', () => {
    ++requestSerial;
    clearResults();
    syncControls();
    setMessage('');
  });
  searchForm.addEventListener('submit', async event => {
    event.preventDefault();
    syncControls();
    if (searchButton.disabled) return;
    const serial = ++requestSerial;
    const currentProvince = province.value;
    const currentQuery = query.value.trim();
    clearResults();
    setMessage('Buscando localidades…');
    searchButton.disabled = true;
    try {
      const params = new URLSearchParams({ provincia: currentProvince, q: currentQuery });
      const result = await getJson(`${planner.dataset.citiesUrl}?${params}`);
      if (serial !== requestSerial) return;
      if (!Array.isArray(result.cities) || !result.cities.length) {
        setMessage('No encontramos localidades con ese nombre en la provincia elegida. Probá con otro nombre.');
        return;
      }
      result.cities.forEach(city => {
        const button = el('button', 'pb-climate-city-choice', `${city.name} · ${city.province}`);
        button.type = 'button';
        button.addEventListener('click', () => chooseCity(city));
        cityResults.append(button);
      });
      cityResults.hidden = false;
      setMessage('Elegí una localidad de los resultados.');
    } catch (error) {
      if (serial === requestSerial) setMessage(error.message || 'No pudimos buscar localidades.');
    } finally {
      if (serial === requestSerial) syncControls();
    }
  });
  syncControls();
})();
