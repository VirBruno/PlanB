/* Datos meteorológicos obtenidos de Django (no del proveedor directamente). */
(() => {
  const elements = document.querySelectorAll("[data-proposal-weather]");
  if (!elements.length) return;
  const symbols = { sun: "☀️", "sun-cloud": "🌤️", cloud: "☁️", rain: "🌧️", snow: "❄️", storm: "⛈️" };

  const load = async (element) => {
    if (element.dataset.weatherLoaded) return;
    element.dataset.weatherLoaded = "true";
    const status = element.querySelector("[data-weather-status]");
    const details = element.querySelector("[data-weather-details]");
    try {
      const response = await fetch(element.dataset.weatherUrl, {
        credentials: "same-origin", headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("No disponible");
      const data = await response.json();
      if (data.status !== "available") {
        status.textContent = data.message || "Todavía no hay pronóstico para esa fecha.";
        return;
      }
      const put = (selector, value) => {
        const target = element.querySelector(selector);
        if (target) target.textContent = value;
      };
      put("[data-weather-symbol]", symbols[data.icon] || "☁️");
      put("[data-weather-condition]", data.condition);
      put("[data-weather-temperature]", `Mín. ${data.temperature_min} °C · Máx. ${data.temperature_max} °C`);
      put("[data-weather-rain]", data.rain_probability === null ? "☂ Lluvia: s/d" : `☂ Prob. de lluvia: ${data.rain_probability}%`);
      put("[data-weather-wind]", data.wind_max === null ? "Viento: s/d" : `↗ Viento máx.: ${data.wind_max} km/h`);
      status.hidden = true;
      details.hidden = false;
    } catch {
      status.textContent = "No pudimos cargar el pronóstico. Probá más tarde.";
    }
  };

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(({ target, isIntersecting }) => {
        if (isIntersecting) { observer.unobserve(target); load(target); }
      });
    }, { rootMargin: "220px" });
    elements.forEach((element) => observer.observe(element));
  } else {
    elements.forEach(load);
  }
})();
