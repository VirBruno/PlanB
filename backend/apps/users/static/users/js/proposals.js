(() => {
  if (!window.L) return;

  document.querySelectorAll("[data-proposal-preview]").forEach((element) => {
    const latitude = Number(element.dataset.lat);
    const longitude = Number(element.dataset.lon);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
    const map = L.map(element, {
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      zoomControl: false,
      attributionControl: true,
    }).setView([latitude, longitude], 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      referrerPolicy: "strict-origin-when-cross-origin",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    L.marker([latitude, longitude]).addTo(map);
  });

  const form = document.querySelector("[data-proposal-form]");
  const mapElement = document.querySelector("[data-proposal-map]");
  if (!form || !mapElement) return;

  const positionInput = document.getElementById(mapElement.dataset.positionInput);
  const searchInput = document.getElementById("id_city");
  const results = document.querySelector("[data-city-results]");
  const status = document.querySelector("[data-location-status]");
  const defaultCenter = [-34.6037, -58.3816];
  const map = L.map(mapElement).setView(defaultCenter, 12);
  let marker = null;

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    referrerPolicy: "strict-origin-when-cross-origin",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const setPoint = (latitude, longitude, zoom = 15) => {
    const point = [Number(latitude), Number(longitude)];
    if (!point.every(Number.isFinite)) return;
    if (!marker) marker = L.marker(point, { draggable: true }).addTo(map);
    else marker.setLatLng(point);
    marker.off("dragend");
    marker.on("dragend", (event) => {
      const { lat, lng } = event.target.getLatLng();
      positionInput.value = JSON.stringify({ type: "Point", coordinates: [lng, lat] });
    });
    positionInput.value = JSON.stringify({ type: "Point", coordinates: [point[1], point[0]] });
    map.setView(point, zoom);
  };

  try {
    const initial = JSON.parse(mapElement.dataset.initialPosition || "null");
    if (initial?.type === "Point" && initial.coordinates?.length === 2) {
      setPoint(initial.coordinates[1], initial.coordinates[0]);
    }
  } catch {
    positionInput.value = "";
  }

  map.on("click", ({ latlng }) => setPoint(latlng.lat, latlng.lng));

  document.querySelector("[data-use-location]").addEventListener("click", () => {
    if (!navigator.geolocation) {
      status.textContent = "Este navegador no ofrece geolocalización.";
      return;
    }
    status.textContent = "Buscando tu ubicación…";
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setPoint(coords.latitude, coords.longitude);
        status.textContent = "Ubicación actual seleccionada.";
      },
      () => { status.textContent = "No pudimos obtener tu ubicación. Revisá el permiso del navegador."; },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  });

  document.querySelector("[data-city-search]").addEventListener("click", async () => {
    const city = searchInput.value.trim();
    results.replaceChildren();
    if (!city) {
      status.textContent = "Escribí una ciudad para buscar.";
      searchInput.focus();
      return;
    }
    status.textContent = "Buscando ciudades…";
    try {
      const query = new URLSearchParams({ q: city, format: "jsonv2", limit: "5", "accept-language": "es" });
      const response = await fetch(`https://nominatim.openstreetmap.org/search?${query}`);
      if (!response.ok) throw new Error("search failed");
      const places = await response.json();
      if (!places.length) {
        status.textContent = "No encontramos esa ciudad.";
        return;
      }
      for (const place of places) {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = place.display_name;
        button.addEventListener("click", () => {
          setPoint(place.lat, place.lon, 13);
          status.textContent = "Ciudad seleccionada. Ajustá el punto exacto en el mapa.";
          results.replaceChildren();
        });
        item.append(button);
        results.append(item);
      }
      status.textContent = "Elegí un resultado y ajustá el punto en el mapa.";
    } catch {
      status.textContent = "No pudimos buscar la ciudad. Volvé a intentar.";
    }
  });
})();