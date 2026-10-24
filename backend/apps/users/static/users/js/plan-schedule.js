/* Plan B — lugar, fecha, horario y clima automático en planes. */
(() => {
  'use strict';
  const symbols = { sun: '☀️', 'sun-cloud': '🌤️', cloud: '☁️', rain: '🌧️', snow: '❄️', storm: '⛈️' };
  const asyncJson = async url => {
    const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store' });
    let data;
    try { data = await response.json(); } catch { throw new Error('No pudimos leer la respuesta del pronóstico.'); }
    if (!response.ok) throw new Error(data.message || 'No se pudo consultar el clima.');
    return data;
  };
  const show = (host, heading, message, data) => {
    const title = host.querySelector('[data-schedule-headline]');
    const text = host.querySelector('[data-schedule-message]');
    const icon = host.querySelector('[data-schedule-symbol]');
    const metrics = host.querySelector('[data-schedule-metrics]');
    title.textContent = heading;
    const previewWeather = host.closest('.pb-create-plan')?.querySelector('[data-preview-weather]');
    if (previewWeather) previewWeather.textContent = heading;
    text.textContent = message;
    metrics.replaceChildren();
    metrics.hidden = !data;
    icon.textContent = data ? (symbols[data.icon] || '☁️') : '🌤️';
    if (data) {
      const values = [
        `🌡️ ${data.temperature} °C`,
        data.rain_probability == null ? '☔ Lluvia: s/d' : `☔ Lluvia: ${data.rain_probability}%`,
        data.wind == null ? '↗ Viento: s/d' : `↗ Viento: ${data.wind} km/h`,
      ];
      values.forEach(value => {
        const span = document.createElement('span');
        span.textContent = value;
        metrics.append(span);
      });
    }
  };
  const forecast = async (host, url, params, serial, currentSerial) => {
    show(host, 'Consultando el pronóstico…', 'Un momento, buscamos las condiciones para el día y horario elegidos.');
    try {
      const data = await asyncJson(`${url}?${new URLSearchParams(params)}`);
      if (serial !== currentSerial()) return;
      if (data.status === 'available') {
        show(host, `${data.condition} · ${data.city}`, `Pronóstico para las ${data.time} hs. (hora de Argentina).`, data);
      } else {
        show(host, 'Todavía no hay pronóstico', data.message || 'No hay datos disponibles para esa fecha.');
      }
    } catch (error) {
      if (serial === currentSerial()) show(host, 'El clima no está disponible', error.message || 'Intentá nuevamente más tarde.');
    }
  };
  const setupForm = root => {
    const date = root.querySelector('[name="plan_date"]');
    const hour = root.querySelector('[name="plan_time"]');
    const province = root.querySelector('[name="weather_province"]');
    const cityId = root.querySelector('[name="weather_city_id"]');
    const citySearch = root.querySelector('[data-city-search]');
    const cityButton = root.querySelector('[data-city-button]');
    const cityResults = root.querySelector('[data-city-results]');
    const picked = root.querySelector('[data-city-picked]');
    const host = root.querySelector('[data-schedule-forecast]');
    let counter = 0;
    let debounce;
    const createForm = root.closest('[data-plan-create-form]');
    const page = root.closest('.pb-create-plan');
    const preview = selector => page?.querySelector(selector);
    const planName = createForm?.querySelector('[name="name"]');
    const description = createForm?.querySelector('[name="description"]');
    const status = createForm?.querySelector('[name="status"]');
    const updatePreview = () => {
      const title = preview('[data-preview-name]');
      const details = preview('[data-preview-description]');
      const when = preview('[data-preview-when]');
      const location = preview('[data-preview-location]');
      const state = preview('[data-preview-state]');
      if (title) title.textContent = planName?.value.trim() || 'Tu próximo plan';
      if (details) details.textContent = description?.value.trim() || 'Acá vas a ver tu idea mientras la organizás.';
      if (state && status) state.textContent = status.value === 'false' ? 'Inactivo' : 'Activo';
      if (when) {
        const dateLabel = date.value ? new Intl.DateTimeFormat('es-AR', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(`${date.value}T12:00:00`)) : '';
        when.textContent = [dateLabel, hour.value ? `${hour.value} hs` : ''].filter(Boolean).join(' · ') || 'A definir';
      }
      if (location) location.textContent = cityId.value && citySearch.value.trim()
        ? `${citySearch.value.trim()} · ${province.options[province.selectedIndex]?.textContent?.trim() || ''}`
        : 'A definir';
    };
    [planName, description, status].filter(Boolean).forEach(input => input.addEventListener('input', updatePreview));
    const clearResults = () => { cityResults.replaceChildren(); cityResults.hidden = true; };
    const selectedLabel = (name = '') => {
      picked.textContent = name ? `📍 Ubicación seleccionada: ${name}` : '';
      picked.hidden = !name;
      updatePreview();
    };
    const missing = () => {
      show(host, 'Tu clima, al elegir dónde y cuándo', 'Completá fecha, hora, provincia y seleccioná una localidad para ver el pronóstico.');
    };
    const updateForecast = () => {
      updatePreview();
      const serial = ++counter;
      if (!date.value || !hour.value || !province.value || !cityId.value) {
        missing();
        return;
      }
      const params = { provincia: province.value, localidad_id: cityId.value, fecha: date.value, hora: hour.value };
      forecast(host, root.dataset.weatherUrl, params, serial, () => counter);
    };
    const invalidateCity = () => {
      cityId.value = '';
      selectedLabel();
      clearResults();
      ++counter;
      updatePreview();
      missing();
    };
    province.addEventListener('change', () => { clearTimeout(debounce); citySearch.value = ''; invalidateCity(); });
    citySearch.addEventListener('input', () => {
      clearTimeout(debounce);
      invalidateCity();
      if (citySearch.value.trim().length >= 3 && province.value) {
        debounce = setTimeout(search, 500);
      }
    });
    [date, hour].forEach(input => input.addEventListener('change', updateForecast));
    const search = async () => {
      clearTimeout(debounce);
      const query = citySearch.value.trim();
      const provinceCode = province.value;
      if (!provinceCode) { show(host, 'Elegí una provincia', 'Seleccioná la provincia antes de buscar localidad.'); return; }
      if (query.length < 3) { show(host, 'Ingresá una localidad', 'Escribí al menos tres letras.'); return; }
      const serial = ++counter;
      clearResults();
      cityButton.disabled = true;
      show(host, 'Buscando localidades…', 'Elegí la localidad correcta cuando aparezca.');
      try {
        const data = await asyncJson(`${root.dataset.citiesUrl}?${new URLSearchParams({ provincia: provinceCode, q: query })}`);
        if (serial !== counter) return;
        if (!Array.isArray(data.cities) || !data.cities.length) {
          show(host, 'Sin resultados', 'No encontramos localidades en esa provincia. Probá otra búsqueda.');
          return;
        }
        data.cities.forEach(city => {
          const button = document.createElement('button');
          button.type = 'button'; button.className = 'pb-schedule-city-choice';
          button.textContent = `${city.name} · ${city.province}`;
          button.addEventListener('click', () => {
            cityId.value = String(city.id);
            citySearch.value = city.name;
            selectedLabel(`${city.name}, ${city.province}`);
            clearResults();
            updateForecast();
          });
          cityResults.append(button);
        });
        cityResults.hidden = false;
        show(host, 'Elegí una localidad', 'Seleccioná uno de los resultados para consultar el clima.');
      } catch (error) {
        if (serial === counter) show(host, 'No pudimos buscar', error.message);
      } finally {
        cityButton.disabled = false;
      }
    };
    cityButton.addEventListener('click', search);
    citySearch.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); search(); }
    });
    if (cityId.value && province.value) {
      const name = root.dataset.initialCityName || '';
      citySearch.value = name;
      selectedLabel(`${name}, ${province.options[province.selectedIndex]?.textContent || ''}`);
      updateForecast();
    } else missing();
    updatePreview();
  };
  document.querySelectorAll('[data-plan-schedule-form]').forEach(setupForm);
  document.querySelectorAll('[data-plan-schedule-summary]').forEach(host => {
    const params = {
      provincia: host.dataset.province, localidad_id: host.dataset.cityId,
      fecha: host.dataset.date, hora: host.dataset.time,
    };
    forecast(host, host.dataset.weatherUrl, params, 0, () => 0);
  });
})();
