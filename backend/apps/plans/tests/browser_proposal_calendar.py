"""Prueba opcional con Chrome local: DOM/JS reales, fixtures y red externa bloqueada.

Desde backend: python -m apps.plans.tests.browser_proposal_calendar
No usa .env, Supabase, servidor Django de producción ni dependencias adicionales.
"""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from contextlib import ExitStack
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_test'
import django
django.setup()

from django.contrib.staticfiles import finders
from django.test import Client
from django.utils import timezone

from apps.plans.tests.test_services import plan_row, proposal_row, USER_ID, PLAN_ID, GROUP_ID
from apps.users.tests.test_calendar import event


HARNESS = r"""
<script>
window.addEventListener('load', async () => {
  const checks = [];
  const assert = (value, name) => { if (!value) throw new Error(name); checks.push(name); };
  const wait = async (predicate) => {
    for (let i = 0; i < 160; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw new Error('Tiempo de espera: ' + predicate.toString());
  };
  const open = document.querySelector('[data-open-proposal-calendar]');
  const dialog = document.querySelector('[data-proposal-calendar]');
  const day = document.querySelector('[data-proposal-date]');
  const hour = document.querySelector('[data-proposal-time]');
  const content = document.querySelector('[data-calendar-content]');
  const loaded = month => wait(() => content.querySelector(`[data-selector-month="${month}"]`));
  let submitted = false;
  document.querySelector('form[data-proposal-form]').addEventListener('submit', e => { e.preventDefault(); submitted = true; });
  try {
    assert(!open.hidden, 'Acceso visible');
    assert(day.value === '2026-11-14' && hour.value === '21:30', 'Edición prellenada en hora local');
    open.click(); await loaded('2026-11');
    assert(content.querySelector('[data-calendar-select="2026-11-14"]').getAttribute('aria-pressed') === 'true', 'Día seleccionado marcado');
    content.querySelector('[data-calendar-month="2026-12"]').click(); await loaded('2026-12');
    assert(content.textContent.includes('Navidad'), 'Navegación conserva feriados');
    content.querySelector('[data-calendar-month=""]').click(); await loaded('2026-10');
    assert(content.querySelector('.is-today'), 'Hoy vuelve al mes actual');
    content.querySelector('[data-calendar-month="2026-11"]').click(); await loaded('2026-11');
    content.querySelector('[data-calendar-select="2026-11-20"]').click();
    await wait(() => !dialog.open);
    assert(day.value === '2026-11-20' && hour.value === '21:30', 'Selección conserva 21:30');
    assert(!submitted, 'Selección no envía formulario');
    open.click(); await loaded('2026-11');
    dialog.querySelector('[data-close-proposal-calendar]').click(); await wait(() => !dialog.open);
    assert(day.value === '2026-11-20' && hour.value === '21:30', 'Cerrar no cambia fecha ni hora');
    day.value = ''; hour.value = '';
    open.click(); await loaded('2026-10');
    content.querySelector('[data-calendar-select="2026-10-28"]').click(); await wait(() => !dialog.open);
    assert(day.value === '2026-10-28' && hour.value === '', 'Crear desde vacío no inventa hora');
    day.value = '2026-11-14'; hour.value = '21:30';
    open.click(); await loaded('2026-11');
    const weekend = content.querySelector('[data-calendar-select="2026-11-14"]');
    assert(weekend.classList.contains('is-weekend') && weekend.textContent.includes('+4 más'), 'Fin de semana y celda densa');
    assert(weekend.querySelectorAll('.pb-calendar-event').length === 2, 'Máximo dos eventos por celda');
    const holiday = content.querySelector('[data-calendar-select="2026-11-23"]');
    assert(holiday.classList.contains('is-holiday') && holiday.querySelector('.pb-calendar-event time'), 'Feriado y horario coexisten');
    assert(!content.querySelector('a[href*="/planes/"]'), 'Eventos sin redirección');
    weekend.querySelector('.pb-calendar-event time').click(); await wait(() => !dialog.open);
    assert(day.value === '2026-11-14' && hour.value === '21:30', 'Click informativo selecciona el día, no la hora del evento');
    const realFetch = window.fetch;
    window.fetch = async () => new Response('No disponible', {status: 503});
    open.click(); await wait(() => !dialog.querySelector('[data-calendar-retry]').hidden);
    assert(dialog.querySelector('[data-calendar-status]').textContent.includes('No pudimos'), 'Fallo visible sin falso calendario vacío');
    assert(day.value === '2026-11-14' && hour.value === '21:30', 'Fallo no modifica formulario');
    window.fetch = realFetch;
    dialog.querySelector('[data-calendar-retry]').click(); await loaded('2026-11');
    document.documentElement.dataset.theme = 'light';
    const light = getComputedStyle(content.querySelector('.is-holiday')).backgroundColor;
    document.documentElement.dataset.theme = 'dark';
    const dark = getComputedStyle(content.querySelector('.is-holiday')).backgroundColor;
    assert(light !== dark, 'Feriados adaptados a light/dark');
    assert(dialog.scrollWidth <= dialog.clientWidth + 1, 'Modal sin overflow horizontal');
    assert(content.scrollWidth <= content.clientWidth + 1, 'Calendario sin overflow horizontal');
    assert(dialog.getBoundingClientRect().width < window.innerWidth, 'Modal compacto dentro del viewport');
    const result = document.createElement('pre'); result.id = 'browser-results';
    result.textContent = JSON.stringify({ok: true, checks, width: innerWidth, scrollY,
      viewport: {top: visualViewport.offsetTop, height: visualViewport.height, scale: visualViewport.scale},
      modal: dialog.getBoundingClientRect().toJSON()});
    document.body.append(result);
  } catch (error) {
    const result = document.createElement('pre'); result.id = 'browser-results';
    result.textContent = JSON.stringify({ok: false, checks, error: error.message, width: innerWidth});
    document.body.append(result);
  }
});
</script>
"""


def main():
    # Ya instalado por Supabase; esta herramienta opcional no instala paquetes.
    from websockets.sync.client import connect

    chrome = Path(os.environ.get('PLANB_CHROME', r'C:\Program Files\Google\Chrome\Application\chrome.exe'))
    if not chrome.is_file():
        raise SystemExit('Chrome no encontrado. Indicá la ruta con PLANB_CHROME.')
    output = Path(tempfile.mkdtemp(prefix='planb-calendar-browser-'))
    events = [event(id=str(i), name=f'Cena con amigos {i}',
                    starts_at=timezone.make_aware(datetime(2026, 11, 14, 20 + i // 3, i * 5)))
              for i in range(6)]
    events.append(event(name='Mates en feriado', starts_at=timezone.make_aware(datetime(2026, 11, 23, 16, 22))))
    client = Client()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/fixture':
                response = client.get(f'/planes/{PLAN_ID}/propuestas/47/editar/')
                body = response.content.decode()
                # Sólo se prueba el selector: sin mapas, fuentes ni requests externas.
                body = re.sub(r'<script\b[^>]*src="([^"]+)"[^>]*>\s*</script>',
                              lambda m: m[0] if m[1].endswith('proposal-calendar.js') else '', body)
                body = re.sub(r'<link\b[^>]*href="https?://[^>]+>', '', body)
                payload, content_type = body.replace('</body>', HARNESS + '</body>').encode(), 'text/html; charset=utf-8'
            elif path.startswith('/static/'):
                asset = finders.find(path.removeprefix('/static/'))
                if not asset:
                    self.send_error(404)
                    return
                payload = Path(asset).read_bytes()
                content_type = 'text/css' if path.endswith('.css') else 'application/javascript'
            elif path == '/calendario/selector/':
                response = client.get(self.path)
                payload, content_type = response.content, 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass  # Navegación/cierre abortó una consulta anterior.

        def log_message(self, *args):
            pass

    with ExitStack() as stack:
        fixtures = {
            'apps.users.services.session_service.current_user': {'id': USER_ID, 'username': 'Ana'},
            'apps.users.services.session_service.get_access_token': 'browser-jwt',
            'apps.notifications.services.unread_count': 0,
            'apps.plans.views.plan_service.get_plan': plan_row(),
            'apps.plans.views.group_service.get_group': {'id': GROUP_ID, 'name': 'Amigos', 'role': 'member'},
            'apps.plans.views.proposal_service.get_proposal': proposal_row(
                date_pick=timezone.make_aware(datetime(2026, 11, 14, 21, 30))),
            'apps.users.calendar_views.timezone.localdate': date(2026, 10, 26),
            'apps.users.services.calendar_service.list_events': events,
        }
        for name, value in fixtures.items():
            stack.enter_context(patch(name, return_value=value))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        for width in (320, 390, 1280):
            screenshot = output / f'selector-{width}.png'
            profile = output / ('profile-' + str(width))
            command = [str(chrome), '--headless', '--disable-gpu', '--no-sandbox',
                       '--no-proxy-server', '--disable-background-networking',
                       '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                       f'--user-data-dir={profile}', '--no-first-run',
                       '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', 'about:blank']
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file = profile / 'DevToolsActivePort'
                for _ in range(160):
                    if port_file.exists():
                        break
                    time.sleep(.05)
                port = int(port_file.read_text().splitlines()[0])
                with urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as response:
                    target = next(t for t in json.load(response) if t['type'] == 'page')
                with connect(target['webSocketDebuggerUrl'], open_timeout=5) as connection:
                    sequence = 0

                    def cdp(method, params=None):
                        nonlocal sequence
                        sequence += 1
                        connection.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
                        while True:
                            message = json.loads(connection.recv(timeout=10))
                            if message.get('id') == sequence:
                                if 'error' in message:
                                    raise RuntimeError(message['error'])
                                return message.get('result', {})

                    cdp('Emulation.setDeviceMetricsOverride', {
                        'width': width, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False,
                    })
                    cdp('Page.navigate', {'url': f'http://127.0.0.1:{server.server_port}/fixture'})
                    raw = None
                    for _ in range(300):
                        raw = cdp('Runtime.evaluate', {'expression':
                            "document.getElementById('browser-results')?.textContent", 'returnByValue': True})
                        raw = raw.get('result', {}).get('value')
                        if raw:
                            break
                        time.sleep(.05)
                    cdp('Runtime.evaluate', {'expression':
                        'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))',
                        'awaitPromise': True})
                    time.sleep(.5)  # Esperar transiciones visuales del shell antes de capturar.
                    screenshot.write_bytes(base64.b64decode(cdp('Page.captureScreenshot', {'captureBeyondViewport': False})['data']))
                    if not raw:
                        raise SystemExit(f'Sin resultado del navegador. Captura: {screenshot}')
                    report = json.loads(raw)
                    if report['ok']:
                        cdp('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Escape', 'code': 'Escape',
                                                      'windowsVirtualKeyCode': 27})
                        cdp('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Escape', 'code': 'Escape',
                                                      'windowsVirtualKeyCode': 27})
                        escaped = cdp('Runtime.evaluate', {'expression':
                            "!document.querySelector('dialog').open && document.querySelector('[data-proposal-time]').value === '21:30'",
                            'returnByValue': True})['result'].get('value')
                        if not escaped:
                            report['ok'], report['error'] = False, 'Escape no cerró conservando el horario'
                        else:
                            report['checks'].append('Escape nativo cierra conservando el horario')
            finally:
                process.terminate()
                process.wait(timeout=10)
            print(json.dumps(report, ensure_ascii=False))
            if not report['ok'] or report['width'] != width:
                raise SystemExit(f'Falló la prueba. Captura: {screenshot}')
    print(f'Capturas locales: {output}')


if __name__ == '__main__':
    main()
