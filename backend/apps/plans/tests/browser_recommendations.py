"""Chrome local + Leaflet real + respuestas simuladas. Sin servicios externos.

Desde backend: python -m apps.plans.tests.browser_recommendations
Sigue las herramientas de browser_expenses/browser_proposal_calendar, sin paquetes nuevos.
"""
import base64
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import Thread
import time
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings_test"
import django
django.setup()

from django.contrib.staticfiles import finders
from django.test import Client
from apps.plans.tests.test_recommendations_ui import confirmed_plan, confirmed_proposal
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID

HARNESS = r"""
<script>
// Antes de los scripts defer: no se consultan endpoints ni tiles remotos.
window.recCalls = [];
window.recPayload = {status: 'not_generated', items: []};
window.recFail = false;
window.recDelay = null;
window.recPostDelay = null;
window.recPostPayload = null;
window.recItem = (index = 0) => ({
  provider_place_id: 'poi-' + index, name: ['Café de la Plaza', 'El Porteño', 'Rincón del Encuentro', 'La Esquina', 'Café Güemes'][index],
  categories: ['catering.cafe'], address: 'Calle ' + (10 + index) + ', La Plata',
  latitude: -34.92 + index * .003, longitude: -57.95 + index * .004, distance_meters: [220, 850, 1400, 3200, 0][index],
  rank: index + 1, website: index ? null : 'https://cafe.example/', opening_hours: index ? null : 'Mo-Fr 08:00-18:00', media: null,
  google_search_url: 'https://www.google.com/search?q=Caf%C3%A9',
  google_maps_url: 'https://www.google.com/maps/search/?api=1&query=Caf%C3%A9'
});
window.fetch = async (url, options = {}) => {
  window.recCalls.push({url: String(url), method: options.method || 'GET',
    fields: options.body ? [...options.body.keys()] : [], credentials: options.credentials});
  if (window.recDelay) await window.recDelay;
  if (options.method === 'POST' && window.recPostDelay) await window.recPostDelay;
  if (window.recFail) return new Response('{}', {status: 503});
  if (options.method === 'POST' && window.recPostPayload) window.recPayload = window.recPostPayload;
  if ((options.method || 'GET') === 'POST' && window.recPayload.status === 'not_generated') {
    window.recPayload = {status: 'ready', items: Array.from({length: 5}, (_, i) => window.recItem(i))};
  }
  return new Response(JSON.stringify(window.recPayload), {headers: {'Content-Type': 'application/json'}});
};
window.addEventListener('load', async () => {
  document.querySelector('[data-tool="sugerencias"]')?.click();
  const checks = [];
  const assert = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const wait = async predicate => {
    for (let i = 0; i < 160; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
    throw Error('Tiempo de espera: ' + predicate.toString());
  };
  const original = document.querySelector('[data-recommendations]');
  const blank = original.cloneNode(true);
  blank.removeAttribute('data-recommendations-initialized');
  const state = root => root.querySelector('[data-suggestions-feedback]').dataset.state;
  const cards = root => root.querySelectorAll('.pb-suggestion');
  const make = async (payload, fail = false) => {
    window.recPayload = payload; window.recFail = fail;
    const root = blank.cloneNode(true);
    root.dataset.url += '?case=' + checks.length + '-' + Math.random();
    document.querySelectorAll('[data-recommendations]').forEach(node => { node.hidden = true; });
    original.parentElement.insertBefore(root, original);
    window.PlanBRecommendations.init();
    await wait(() => state(root) !== 'loading');
    return root;
  };
  try {
    // Leaflet real; sólo reemplazar el transporte de tiles por un PNG en memoria.
    const tileFactory = L.tileLayer;
    const markerFactory = L.marker;
    window.recMarkers = [];
    L.marker = (...args) => { const marker = markerFactory(...args); window.recMarkers.push(marker); return marker; };
    L.tileLayer = (url, options) => {
      assert(url === 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', 'Tiles OSM configurados');
      assert(options.referrerPolicy === 'strict-origin-when-cross-origin', 'Tiles envían Referer');
      const tile = '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e9e5db"/><path d="M0 60H256M0 188H256M60 0V256M188 0V256" stroke="#fdfcf8" stroke-width="12"/><path d="M0 124H256M124 0V256" stroke="#d8d5cb" stroke-width="3"/></svg>';
      return tileFactory('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(tile), options);
    };
    await wait(() => state(original) === 'ready');
    assert(window.recCalls.filter(c => c.method === 'POST').length === 1, 'not_generated genera una sola vez');
    assert(window.recCalls.filter(c => c.method === 'GET').length === 1, 'Primero consulta snapshot');
    window.PlanBRecommendations.init(); window.PlanBRecommendations.init();
    assert(window.recCalls.length === 2, 'Doble inicialización no duplica requests');
    assert(window.recCalls[1].fields.join() === 'csrfmiddlewaretoken' && window.recCalls[1].credentials === 'same-origin', 'POST sólo envía CSRF y sesión');
    assert(cards(original).length === 5, 'Renderiza cinco cards sin fotos');
    assert(original.textContent.includes('Café') && !original.textContent.includes('catering.cafe'), 'Categoría legible');
    for (const value of ['220 m', '850 m', '1,4 km', '3,2 km', '0 m']) assert(original.textContent.includes(value), 'Distancia ' + value);
    const prior = window.recCalls.length;
    original.querySelector('[data-select-place="1"]').click();
    assert(cards(original)[1].classList.contains('is-selected'), 'Card selecciona su marcador');
    // El primer mapa pudo crearse antes de instalar el interceptor de markers.
    let root = await make({status: 'ready', items: Array.from({length: 5}, (_, i) => window.recItem(i))});
    assert(root.querySelectorAll('.pb-suggestion-marker').length === 5, 'Leaflet real muestra cinco marcadores');
    assert(root.querySelector('.leaflet-control-attribution').textContent.includes('OpenStreetMap'), 'Atribución visible');
    const lastMarker = window.recMarkers.at(-1);
    lastMarker.fire('click');
    assert(cards(root)[4].classList.contains('is-selected') && document.activeElement.dataset.selectPlace === '4', 'Marcador identifica card y foco');
    lastMarker._map.zoomIn(1, {animate: false});
    assert(window.recCalls.length === prior + 1, 'Seleccionar o mover mapa no solicita recomendaciones');
    const external = [...root.querySelectorAll('.pb-suggestion-link')];
    assert(external.some(a => a.textContent === 'Ver en Google') && external.some(a => a.textContent === 'Abrir en Maps'), 'Ambos enlaces Google');
    assert(external.every(a => a.target === '_blank' && a.rel.includes('noopener') && a.rel.includes('noreferrer')), 'Enlaces externos seguros');
    for (let count = 1; count <= 5; count++) {
      const before = window.recCalls.length;
      root = await make({status: 'ready', items: Array.from({length: count}, (_, i) => window.recItem(i))});
      assert(cards(root).length === count && root.querySelectorAll('.pb-suggestion-marker').length === count, 'Mapa/cards con ' + count + ' resultados');
      assert(window.recCalls.length === before + 1 && window.recCalls.at(-1).method === 'GET', 'ready sólo reutiliza snapshot');
    }
    root = await make({status: 'ready', items: [{...window.recItem(), address: null, website: null,
      opening_hours: null, categories: ['unknown.category'], latitude: null, longitude: true}]});
    assert(cards(root).length === 1 && root.textContent.includes('Dirección no disponible') && root.textContent.includes('Lugar de interés'), 'Opcionales nulos y categoría desconocida');
    assert(!root.querySelector('.leaflet-container') && root.textContent.includes('No hay coordenadas'), 'Sin coordenadas válidas no crea marcadores');
    root = await make({status: 'ready', items: [window.recItem(), {...window.recItem(1), latitude: 91}]});
    assert(cards(root).length === 2 && root.querySelectorAll('.pb-suggestion-marker').length === 1, 'Mapa filtra coordenadas inválidas, conserva cards');
    const unsafe = {...window.recItem(), name: '<img src=x onerror=alert(1)>', address: '<' + 'script>alert(1)<' + '/script>',
      website: 'javascript:alert(1)', google_search_url: 'https://evil.invalid/', opening_hours: '<b>No HTML</b>'};
    root = await make({status: 'ready', items: [unsafe]});
    assert(root.textContent.includes('<img src=x') && !root.querySelector('[data-suggestions-list]').querySelector('img, script, b') && root.querySelectorAll('.pb-suggestion-link').length === 1, 'Datos escapados y enlaces inválidos descartados');
    root = await make({status: 'ready', items: []});
    assert(root.textContent.includes('No encontramos lugares compatibles') && root.querySelector('[data-suggestions-results]').hidden, 'Cero resultados distinto de error');
    const duplicateURL = original.dataset.url + '?duplicate-components';
    window.recPayload = {status: 'not_generated', items: []};
    const duplicateRoots = [blank.cloneNode(true), blank.cloneNode(true)];
    const beforeDuplicate = window.recCalls.length;
    duplicateRoots.forEach(node => { node.dataset.url = duplicateURL; original.parentElement.append(node); });
    window.PlanBRecommendations.init();
    await wait(() => duplicateRoots.every(node => state(node) === 'ready'));
    assert(window.recCalls.length === beforeDuplicate + 2, 'Dos componentes del mismo plan comparten GET y POST');
    duplicateRoots.forEach(node => { node.hidden = true; });
    const beforePending = window.recCalls.length;
    root = await make({status: 'pending', items: [], updated_at: new Date().toISOString()});
    assert(window.recCalls.length === beforePending + 1 && root.querySelector('[data-suggestions-retry]').hidden, 'pending reciente no genera ni habilita reemplazo');
    await new Promise(resolve => setTimeout(resolve, 100));
    assert(window.recCalls.length === beforePending + 1, 'Sin polling mientras está pending');
    root = await make({status: 'pending', items: [], updated_at: new Date(Date.now() - 180000).toISOString()});
    assert(!root.querySelector('[data-suggestions-retry]').hidden, 'pending antiguo ofrece reintento explícito');
    const beforeFailed = window.recCalls.length;
    root = await make({status: 'failed', error_code: 'timeout', items: [window.recItem()]});
    assert(window.recCalls.length === beforeFailed + 1 && cards(root).length === 1, 'failed no reintenta solo ni oculta snapshot anterior');
    window.recPayload = {status: 'ready', items: [window.recItem()]};
    let release; window.recDelay = new Promise(resolve => { release = resolve; });
    const retryForm = root.querySelector('[data-suggestions-retry-form]');
    retryForm.requestSubmit(); retryForm.requestSubmit();
    assert(state(root) === 'loading' && root.querySelector('[data-suggestions-retry]').disabled, 'Reintento muestra loading y bloquea doble envío');
    release(); window.recDelay = null;
    await wait(() => state(root) === 'ready');
    assert(window.recCalls.at(-1).url.includes('/reintentar/') && window.recCalls.at(-1).fields.join() === 'csrfmiddlewaretoken', 'Reintento explícito sin payload arbitrario');
    window.recFail = true;
    root.querySelector('[data-suggestions-refresh]').click();
    await wait(() => state(root) === 'unavailable');
    assert(cards(root).length === 1 && document.querySelector('#plan-title'), 'Error conserva cards y resto del detalle');
    window.recFail = false;
    const previousSummary = 'Para este plan buscamos cafeterías cerca del punto acordado.';
    const nextSummary = 'Para esta salida buscamos bares y lugares para bailar cerca del punto acordado.';
    const summaryNode = root => root.querySelector('[data-suggestions-summary]');
    root = await make({status: 'ready', items: [window.recItem()], ai_summary: previousSummary});
    assert(!summaryNode(root).hidden && summaryNode(root).textContent === previousSummary, 'Summary general visible como texto');
    assert(summaryNode(root).compareDocumentPosition(root.querySelector('[data-suggestions-results]'))
      & Node.DOCUMENT_POSITION_FOLLOWING, 'Summary antes de cards y mapa');
    root = await make({status: 'ready', items: [window.recItem()]});
    assert(summaryNode(root).hidden && summaryNode(root).getBoundingClientRect().height === 0, 'Sin summary no deja bloque vacío');
    root = await make({status: 'ready', items: [window.recItem()], ai_summary: 'x'.repeat(321)});
    assert(summaryNode(root).hidden && cards(root).length === 1, 'Summary excesivo no afecta cards');
    root = await make({status: 'ready', items: [window.recItem()], ai_summary: '<img src=x onerror="window.recXSS=true">'});
    assert(!summaryNode(root).querySelector('img') && !window.recXSS, 'Summary nunca se interpreta como HTML');
    root = await make({status: 'failed', has_snapshot: true, items: [window.recItem()], ai_summary: previousSummary});
    assert(cards(root).length === 1 && summaryNode(root).textContent === previousSummary
      && root.textContent.includes('última versión disponible')
      && root.querySelector('[data-suggestions-retry]').textContent === 'Reintentar actualización', 'Fallo conserva snapshot, summary y acción de actualización');
    root = await make({status: 'pending', has_snapshot: true, items: [window.recItem()], ai_summary: previousSummary,
      updated_at: new Date().toISOString()});
    assert(cards(root).length === 1 && !summaryNode(root).hidden
      && root.textContent.includes('Estamos actualizando'), 'Pending conserva datos y anuncia actualización');
    const beforeStale = window.recCalls.length;
    let releaseUpdate;
    window.recPostDelay = new Promise(resolve => { releaseUpdate = resolve; });
    window.recPostPayload = {status: 'ready', items: [window.recItem(1)], ai_summary: nextSummary, can_update: false};
    const staleRoot = make({status: 'ready', has_snapshot: true, is_stale: true, can_update: true,
      items: [window.recItem()], ai_summary: previousSummary});
    await wait(() => window.recCalls.length === beforeStale + 2);
    root = [...document.querySelectorAll('[data-recommendations]')].find(node => !node.hidden);
    assert(state(root) === 'loading' && cards(root).length === 1 && summaryNode(root).textContent === previousSummary
      && !root.querySelector('[data-suggestions-map]').hidden, 'Snapshot y mapa siguen visibles durante recálculo automático');
    releaseUpdate(); window.recPostDelay = null;
    root = await staleRoot;
    assert(state(root) === 'ready' && summaryNode(root).textContent === nextSummary
      && cards(root)[0].textContent.includes('El Porteño'), 'Éxito reemplaza lugares y summary');
    assert(window.recCalls.length === beforeStale + 2, 'Stale hace un GET y un POST sin polling');
    window.recPostPayload = null;
    const beforeLegacy = window.recCalls.length;
    root = await make({status: 'ready', can_refresh: true, items: [window.recItem()]});
    assert(window.recCalls.length === beforeLegacy + 1
      && !root.querySelector('[data-suggestions-retry]').hidden
      && root.querySelector('[data-suggestions-retry]').textContent === 'Actualizar sugerencias', 'Legacy ofrece actualización explícita sin generación automática');
    window.recPostPayload = {status: 'ready', can_refresh: false, items: [window.recItem()], ai_summary: nextSummary};
    root.querySelector('[data-suggestions-retry-form]').requestSubmit();
    await wait(() => state(root) === 'ready');
    assert(window.recCalls.length === beforeLegacy + 2 && root.querySelector('[data-suggestions-retry]').hidden,
      'Actualización explícita legacy genera una vez y retira la acción');
    window.recPostPayload = null;
    const realLeaflet = window.L; window.L = null;
    root = await make({status: 'ready', items: [window.recItem()]});
    assert(cards(root).length === 1 && root.textContent.includes('El mapa no está disponible'), 'Fallo del mapa no bloquea cards');
    window.L = realLeaflet;
    root = await make({status: 'ready', ai_summary: previousSummary, items: Array.from({length: 5}, (_, i) => window.recItem(i))});
    await document.fonts.ready;
    document.documentElement.dataset.theme = 'light';
    const light = getComputedStyle(root).backgroundColor;
    document.documentElement.dataset.theme = 'dark';
    assert(getComputedStyle(root).backgroundColor !== light, 'Sección adapta light/dark');
    assert(root.scrollWidth <= root.clientWidth + 1 && document.documentElement.scrollWidth <= innerWidth + 1, 'Sin scroll horizontal');
    const columns = getComputedStyle(root.querySelector('[data-suggestions-results]')).gridTemplateColumns.split(' ').length;
    assert(columns === (innerWidth > 1000 ? 2 : 1), 'Layout desktop/mobile coherente');
    const list = root.querySelector('[data-suggestions-list]');
    const panel = root.querySelector('.pb-suggestions-map-panel');
    assert(original.closest('[data-tool-panel="sugerencias"]') && !original.closest('[data-plan-drawer]'),
      'Sugerencias en workspace y fuera del historial');
    if (innerWidth > 1000) {
      assert(list.scrollHeight > list.clientHeight && getComputedStyle(list).overflowY === 'auto', 'Desktop usa listado con scroll propio');
      assert(Math.abs(list.getBoundingClientRect().height - panel.getBoundingClientRect().height) < 3, 'Listado y mapa equilibrados en altura');
      const beforeScroll = window.recCalls.length;
      const mapTop = panel.getBoundingClientRect().top;
      list.scrollTop = list.scrollHeight;
      assert(list.scrollTop > 0 && Math.abs(panel.getBoundingClientRect().top - mapTop) < 1, 'Scroll de cards conserva el mapa visible');
      root.querySelector('[data-select-place="4"]').focus();
      assert(document.activeElement.dataset.selectPlace === '4', 'Última card accesible por teclado');
      assert(window.recCalls.length === beforeScroll, 'Scroll interno no consume recomendaciones');
      list.scrollTop = 0;
    } else {
      assert(getComputedStyle(list).maxHeight === 'none' && getComputedStyle(list).overflowY === 'visible',
        'Mobile conserva listado completo sin scroll anidado');
    }
    const result = document.createElement('pre'); result.id = 'browser-results';
    result.textContent = JSON.stringify({ok: true, width: innerWidth, checks}); document.body.append(result);
  } catch (error) {
    const result = document.createElement('pre'); result.id = 'browser-results';
    result.textContent = JSON.stringify({ok: false, width: innerWidth, checks, error: error.message}); document.body.append(result);
  }
});
</script>
"""


def main(*, proposals=None, plan_fixture=None, group_role="member", all_scripts=False):
    from websockets.sync.client import connect
    chrome = Path(os.environ.get("PLANB_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    if not chrome.is_file():
        raise SystemExit("Chrome no encontrado; definir PLANB_CHROME.")
    output = Path(tempfile.mkdtemp(prefix="planb-recommendations-browser-"))
    client = Client()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/fixture":
                body = client.get(f"/planes/{PLAN_ID}/").content.decode()
                body = re.sub(r'<script\b[^>]*src="([^"]+)"[^>]*>\s*</script>',
                    lambda m: m[0] if (all_scripts and any(name in m[1] for name in (
                        "leaflet.js", "plan-recommendations.js", "plan-confirmed-workspace.js", "plan-expenses.js",
                        "plan-weather-planner.js", "proposal-weather.js", "proposals.js")))
                    or (not all_scripts and any(name in m[1] for name in (
                        "leaflet.js", "plan-recommendations.js", "plan-confirmed-workspace.js"))) else "", body)
                body = re.sub(r'<link\b[^>]*href="https?://[^>]+>', "", body)
                payload, mime = body.replace("</head>", HARNESS + "</head>").encode(), "text/html; charset=utf-8"
            elif path.startswith("/static/"):
                asset = finders.find(path.removeprefix("/static/"))
                if not asset:
                    self.send_error(404)
                    return
                payload = Path(asset).read_bytes()
                mime = "text/css" if path.endswith(".css") else "application/javascript" if path.endswith(".js") else "image/png"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def log_message(self, *args):
            pass

    with ExitStack() as stack:
        fixtures = {
            "apps.users.services.session_service.current_user": {"id": USER_ID, "username": "Ana"},
            "apps.users.services.session_service.get_access_token": "browser-jwt",
            "apps.notifications.services.unread_count": 0,
            "apps.plans.views.plan_service.get_plan": plan_fixture or confirmed_plan(),
            "apps.plans.views.group_service.get_group": {"id": GROUP_ID, "name": "Amigos", "role": group_role},
            "apps.plans.views.proposal_service.list_proposals": proposals if proposals is not None else [confirmed_proposal()],
            "apps.plans.views.profile_service.usernames_for_ids": {USER_ID: "Ana"},
            "apps.plans.views.election_service.voted_proposal": None,
        }
        for target, value in fixtures.items():
            stack.enter_context(patch(target, return_value=value))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        for width, height in ((320, 900), (390, 900), (820, 1000), (1366, 768), (1440, 900), (1920, 1080)):
            profile = output / f"profile-{width}"
            process = subprocess.Popen([str(chrome), "--headless", "--disable-gpu", "--no-sandbox",
                "--no-proxy-server", "--disable-background-networking",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                f"--user-data-dir={profile}", "--no-first-run", "--remote-debugging-port=0", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                port_file = profile / "DevToolsActivePort"
                port = None
                for _ in range(160):
                    try:
                        port = int(port_file.read_text().splitlines()[0])
                        break
                    except (OSError, ValueError, IndexError):
                        pass  # Chrome puede mantener el archivo bloqueado al crearlo en Windows.
                    time.sleep(.05)
                if port is None:
                    raise SystemExit("Chrome no publicó el puerto de depuración a tiempo.")
                with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                    target = next(t for t in json.load(response) if t["type"] == "page")
                with connect(target["webSocketDebuggerUrl"], open_timeout=5) as connection:
                    sequence = 0

                    def cdp(method, params=None):
                        nonlocal sequence
                        sequence += 1
                        connection.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                        while True:
                            message = json.loads(connection.recv(timeout=15))
                            if message.get("id") == sequence:
                                if "error" in message:
                                    raise RuntimeError(message["error"])
                                return message.get("result", {})

                    cdp("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
                    cdp("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/fixture"})
                    raw = None
                    for _ in range(450):
                        raw = cdp("Runtime.evaluate", {"expression": "document.getElementById('browser-results')?.textContent",
                                                       "returnByValue": True}).get("result", {}).get("value")
                        if raw:
                            break
                        time.sleep(.05)
                    for theme in ("light", "dark"):
                        clip = cdp("Runtime.evaluate", {"expression": """(() => {
                          document.documentElement.dataset.theme = '""" + theme + """';
                          const r = [...document.querySelectorAll('[data-recommendations]')].find(r => !r.hidden).getBoundingClientRect();
                          return {x:r.x + scrollX, y:r.y + scrollY, width:r.width, height:r.height, scale:1};
                        })()""", "returnByValue": True})["result"]["value"]
                        time.sleep(.2)  # Esperar la transición de superficies entre temas.
                        if all_scripts or not clip.get("width"):
                            clip = {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}
                        data = cdp("Page.captureScreenshot", {"captureBeyondViewport": True, "clip": clip})["data"]
                        (output / f"suggestions-{width}-{theme}.png").write_bytes(base64.b64decode(data))
                        if all_scripts and raw and json.loads(raw).get("ok"):
                            for stage in ("compact", "drawer"):
                                cdp("Runtime.evaluate", {"expression": f"window.layoutCapture('{stage}')"})
                                time.sleep(.1)
                                data = cdp("Page.captureScreenshot", {"captureBeyondViewport": False})["data"]
                                (output / f"confirmed-{width}-{stage}-{theme}.png").write_bytes(base64.b64decode(data))
                            cdp("Runtime.evaluate", {"expression": "window.layoutCapture('sugerencias')"})
                    if not raw:
                        raise SystemExit(f"Sin resultado del navegador: {output}")
                    report = json.loads(raw)
                    (output / f"report-{width}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps({"ok": report["ok"], "width": width, "checks": len(report["checks"]),
                                      **({"error": report["error"]} if "error" in report else {})}, ensure_ascii=False))
                    if not report["ok"]:
                        raise SystemExit(f"Falló prueba de navegador. Capturas: {output}")
            finally:
                process.terminate()
                process.wait(timeout=10)
    print(f"Capturas locales: {output}")


if __name__ == "__main__":
    main()
