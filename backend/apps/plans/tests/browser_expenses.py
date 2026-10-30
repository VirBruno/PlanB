"""Prueba opcional del popup con Chrome local, Django real y servicios simulados.

Desde backend: python -m apps.plans.tests.browser_expenses
Sin .env, SQL, Supabase remoto ni dependencias nuevas. Capturas en la carpeta temporal.
"""
import base64
from contextlib import ExitStack
from decimal import Decimal
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
from apps.plans.services.expense_service import summarize
from apps.plans.tests.test_expenses import EXPENSE, JUAN, MEMBERS, expense, snapshot
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row

HARNESS = r"""
<script>
window.addEventListener('load', async () => {
  const checks = [];
  const assert = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const wait = async predicate => {
    for (let i = 0; i < 200; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw Error('Tiempo de espera: ' + predicate.toString());
  };
  const dialog = document.querySelector('[data-expenses-dialog]');
  const opener = document.querySelector('[data-open-expenses]');
  const content = document.querySelector('[data-expenses-content]');
  const loaded = () => wait(() => content.querySelector('[data-expense-summary]'));
  const form = () => content.querySelector('[data-expense-form]');
  const fill = (amount = '30.000') => {
    form().elements.description.value = 'Cena';
    form().elements.amount.value = amount;
    form().elements.paid_by.value = '00000000-0000-0000-0000-000000000001';
    form().elements.all_group.checked = true;
    form().elements.all_group.dispatchEvent(new Event('change', {bubbles: true}));
  };
  try {
    opener.scrollIntoView({block: 'center'});
    const before = {url: location.href, scroll: scrollY, title: document.querySelector('#plan-title')};
    opener.click(); await loaded();
    assert(dialog.open && content.textContent.includes('Aún no hay gastos'), 'Abre con estado vacío');
    content.querySelector('[data-add-expense]').click();
    fill();
    assert([...form().querySelectorAll('input[name=participants]')].every(i => i.disabled), 'TODO EL GRUPO desactiva selección individual');
    content.querySelector('[data-cancel-expense]').click();
    assert(content.querySelector('[data-expense-editor]').hidden, 'Cancelar vuelve al listado');
    content.querySelector('[data-add-expense]').click();
    fill(); form().requestSubmit();
    await wait(() => content.textContent.includes('Para saldar'));
    assert(content.textContent.includes('$30.000,00') && content.textContent.includes('+$20.000,00') && content.textContent.includes('-$10.000,00'), 'Guardar actualiza total y balances exactos');
    assert(document.querySelector('[data-expense-card-summary]').textContent.includes('1 gasto registrado'), 'Tarjeta actualiza resumen');
    assert(content.querySelectorAll('.pb-expenses-settle p').length === 2, 'Presenta dos transferencias');
    content.querySelector('[data-add-expense]').click(); fill('6.000'); form().requestSubmit();
    await wait(() => document.querySelector('[data-expense-card-summary]').textContent.includes('2 gastos registrados'));
    assert(content.textContent.includes('$36.000,00') && content.textContent.includes('+$24.000,00'), 'Segundo gasto recalcula las cuentas');
    const realFetch = window.fetch;
    content.querySelector('[data-add-expense]').click(); fill();
    window.fetch = async () => new Response('Sin conexión', {status: 503});
    form().requestSubmit();
    await wait(() => !dialog.querySelector('[data-reload-expenses]').hidden);
    assert(!form() && dialog.querySelector('[data-expenses-status]').textContent.includes('antes de volver'), 'Guardado incierto exige verificar antes de reenviar');
    window.fetch = realFetch;
    dialog.querySelector('[data-reload-expenses]').click(); await loaded();
    assert(content.querySelectorAll('.pb-expenses-row').length === 2, 'Reintentar consulta no duplica gastos');
    document.documentElement.dataset.theme = 'light';
    const light = getComputedStyle(dialog).backgroundColor;
    document.documentElement.dataset.theme = 'dark';
    assert(getComputedStyle(dialog).backgroundColor !== light, 'Popup adapta el tema');
    assert(dialog.scrollWidth <= dialog.clientWidth + 1, 'Popup sin desborde horizontal');
    assert(dialog.getBoundingClientRect().width <= innerWidth, 'Popup dentro del ancho móvil');
    dialog.querySelector('[data-close-expenses]').click();
    await wait(() => !dialog.open && document.activeElement === opener);
    assert(!dialog.open && document.activeElement === opener, 'X cierra y devuelve el foco');
    assert(location.href === before.url && document.querySelector('#plan-title') === before.title && Math.abs(scrollY - before.scroll) < 2, 'Página, URL, DOM y scroll conservados');
    window.fetch = async () => new Response('Sin conexión', {status: 503});
    opener.click(); await wait(() => !dialog.querySelector('[data-reload-expenses]').hidden);
    assert(!content.textContent.includes('Aún no hay gastos'), 'Fallo de lectura no aparenta vacío');
    window.fetch = realFetch;
    dialog.querySelector('[data-reload-expenses]').click(); await loaded();
    const result = document.createElement('pre'); result.id = 'browser-results';
    result.textContent = JSON.stringify({ok: true, width: innerWidth, checks}); document.body.append(result);
  } catch(error) {
    const result = document.createElement('pre'); result.id = 'browser-results';
    result.textContent = JSON.stringify({ok: false, width: innerWidth, checks, error: error.message}); document.body.append(result);
  }
});
</script>
"""


def main():
    from websockets.sync.client import connect  # Dependencia existente de Supabase.

    chrome = Path(os.environ.get("PLANB_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    if not chrome.is_file():
        raise SystemExit("Chrome no encontrado. Indicá la ruta con PLANB_CHROME.")
    output = Path(tempfile.mkdtemp(prefix="planb-expenses-browser-"))
    rows = []
    client = Client(enforce_csrf_checks=True)

    def create(token, **kwargs):
        assert token == "browser-jwt" and kwargs["all_group"] and not kwargs["participants"]
        assert kwargs["paid_by"] == JUAN
        amount = kwargs["amount"]
        assert isinstance(amount, Decimal)
        assert amount in (Decimal("30000"), Decimal("6000"))
        eid = f"00000000-0000-0000-0000-{len(rows) + 20:012d}"
        rows.append(expense(str(amount), parts=[(p["id"], str(amount / 3)) for p in MEMBERS], eid=eid))
        return EXPENSE

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.serve()

        def do_POST(self):
            self.serve()

        def serve(self):
            path = urlsplit(self.path).path
            code = 200
            cookie = None
            if path == "/fixture":
                rows.clear()
                response = client.get(f"/planes/{PLAN_ID}/")
                body = re.sub(r'<script\b[^>]*src="([^"]+)"[^>]*>\s*</script>',
                              lambda m: m[0] if "plan-expenses.js" in m[1] else "", response.content.decode())
                body = re.sub(r'<link\b[^>]*href="https?://[^>]+>', "", body)
                data = body.replace("</body>", HARNESS + "</body>").encode()
                kind = "text/html; charset=utf-8"
            elif path.startswith("/static/"):
                asset = finders.find(path.removeprefix("/static/"))
                if not asset:
                    self.send_error(404)
                    return
                data = Path(asset).read_bytes()
                kind = "text/css" if path.endswith(".css") else "application/javascript"
            elif path == f"/planes/{PLAN_ID}/gastos/":
                if self.command == "POST":
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                    response = client.post(path, body, content_type=self.headers["Content-Type"])
                else:
                    response = client.get(path)
                data, kind, code = response.content, response.headers["Content-Type"], response.status_code
                if "csrftoken" in response.cookies:
                    cookie = response.cookies["csrftoken"].OutputString()
            else:
                self.send_error(404)
                return
            self.send_response(code)
            self.send_header("Content-Type", kind)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def log_message(self, *args):
            pass

    with ExitStack() as stack:
        fixtures = {
            "apps.users.services.session_service.current_user": {"id": USER_ID, "username": "Facundo"},
            "apps.users.services.session_service.get_access_token": "browser-jwt",
            "apps.notifications.services.unread_count": 0,
            "apps.plans.views.plan_service.get_plan": plan_row(),
            "apps.plans.views.group_service.get_group": {"id": GROUP_ID, "name": "Amigos", "role": "member"},
            "apps.plans.views.proposal_service.list_proposals": [],
            "apps.plans.views.profile_service.usernames_for_ids": {},
        }
        for name, value in fixtures.items():
            stack.enter_context(patch(name, return_value=value))
        stack.enter_context(patch("apps.plans.expense_views.expense_service.get_summary",
                                  side_effect=lambda *a, **kw: summarize(snapshot(*rows), USER_ID)))
        stack.enter_context(patch("apps.plans.expense_views.expense_service.create_expense", side_effect=create))
        # Cualquier servicio externo olvidado falla: sólo se permite el servidor local del navegador.
        stack.enter_context(patch("apps.users.services.clients.http_client", side_effect=AssertionError("HTTP externo prohibido")))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        for width in (320, 390, 1280):
            profile = output / f"profile-{width}"
            command = [str(chrome), "--headless", "--disable-gpu", "--no-sandbox", "--no-proxy-server",
                       "--disable-background-networking", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                       f"--user-data-dir={profile}", "--no-first-run", "--remote-debugging-address=127.0.0.1",
                       "--remote-debugging-port=0", "about:blank"]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file = profile / "DevToolsActivePort"
                for _ in range(160):
                    if port_file.exists():
                        break
                    time.sleep(.05)
                port = int(port_file.read_text().splitlines()[0])
                with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                    target = next(t for t in json.load(response) if t["type"] == "page")
                with connect(target["webSocketDebuggerUrl"], open_timeout=5) as connection:
                    sequence = 0

                    def cdp(method, params=None):
                        nonlocal sequence
                        sequence += 1
                        connection.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                        while True:
                            reply = json.loads(connection.recv(timeout=10))
                            if reply.get("id") == sequence:
                                if "error" in reply:
                                    raise RuntimeError(reply["error"])
                                return reply.get("result", {})

                    cdp("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
                    cdp("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/fixture"})
                    raw = None
                    for _ in range(300):
                        raw = cdp("Runtime.evaluate", {"expression": "document.getElementById('browser-results')?.textContent", "returnByValue": True})["result"].get("value")
                        if raw:
                            break
                        time.sleep(.05)
                    screenshot = output / f"gastos-{width}.png"
                    screenshot.write_bytes(base64.b64decode(cdp("Page.captureScreenshot", {"captureBeyondViewport": False})["data"]))
                    if not raw:
                        raise SystemExit(f"Sin resultado del navegador. Captura: {screenshot}")
                    report = json.loads(raw)
                    if report["ok"]:
                        cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                        cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                        escaped = cdp("Runtime.evaluate", {"expression": "!document.querySelector('[data-expenses-dialog]').open", "returnByValue": True})["result"].get("value")
                        if not escaped:
                            report.update(ok=False, error="Escape no cerró el popup")
                        else:
                            report["checks"].append("Escape nativo cierra el popup")
            finally:
                process.terminate()
                process.wait(timeout=10)
            print(json.dumps(report, ensure_ascii=False))
            if not report["ok"] or report["width"] != width:
                raise SystemExit(f"Falló la prueba. Captura: {screenshot}")
    print(f"Capturas locales: {output}")


if __name__ == "__main__":
    main()
