# Selección de fecha desde el Calendario Personal

## Fecha y hora: antes y después

Crear y editar propuestas comparten `plans/proposal_form.html` y `ProposalForm`.
Antes, `date_pick` se mostraba como un único `datetime-local` con formato
`YYYY-MM-DDTHH:mm`; el backend producía un datetime en la zona activa de Django
y el servicio de propuestas lo enviaba a Supabase.

Ahora el mismo campo usa `SplitDateTimeField` y dos controles nativos visibles:
`date_pick_0` (Fecha, `YYYY-MM-DD`) y `date_pick_1` (Hora, `HH:mm`). Django los
combina en el mismo `cleaned_data['date_pick']`, con la zona activa. No cambian
la vista de guardado, el servicio remoto, el esquema ni el contrato de Supabase.
El widget sigue aceptando el POST combinado anterior cuando no llegan los nuevos
controles; si llegan ambos formatos, tienen prioridad los controles separados.

Ambos controles siguen siendo editables manualmente y funcionan sin JavaScript.
El calendario escribe **sólo** `.value` del input Fecha y emite `input`/`change`.
No consulta ni modifica el control Hora, ni copia el horario de un encuentro.
La edición descompone el timestamp existente a fecha/hora local. Un error de
validación conserva ambos valores.

Si ambos controles están vacíos, `date_pick` sigue siendo opcional/NULL. Si se
completa sólo uno, el formulario exige completar el otro al guardar: no se inventa
medianoche, fecha actual ni otro valor. Seleccionar un día con Hora vacía deja
esa hora vacía para que el usuario la ingrese explícitamente.

## Reutilización del calendario y permisos

`calendar_views._month_context` contiene la consulta y construcción común de mes
para `/calendario/` y `/calendario/selector/`. Ambos usan:

- `selected_month`, límites 1900–2100, `shift_month`, `month_bounds` y `build_month`.
- `calendar_service.list_events`: `scheduled_at` definitivo, planes Confirmados
  y Finalizados, JWT/RLS y comprobación de membresía actual.
- `holidays.py`, sin listas de feriados adicionales ni requests para obtenerlos.
- Clases de día construidas una vez en `build_month`: fin de semana, feriado,
  fuera de mes, Hoy y selección cuando corresponde.
- Componentes compartidos `calendar_event.html` y `calendar_holiday.html`, y los
  colores/estilos de `personal-calendar.css` para light/dark.

El refactor del calendario completo se limita a extraer su contexto y componente
de feriado, compartir las clases y permitir que el evento sea informativo en el
selector. `/calendario/` conserva su navegación HTML, agenda móvil, estados y
enlaces habituales a los planes. Las reglas del shell en `personal-calendar.css`
se acotan para que un selector dentro del formulario no ensanche la página.

El endpoint parcial sólo permite GET y requiere la misma sesión que el calendario.
Recibe año, mes y fecha seleccionada; no recibe una identidad que otorgue acceso.
Siempre obtiene el JWT de la sesión. Conserva `Cache-Control: no-store` y
`Referrer-Policy: no-referrer`, y escapa nombres/atributos en templates.
No renderiza el shell ni sus context processors: no consulta el badge por mes.
No se hacen consultas por día. Se reutiliza la consulta mensual y la caché de
membresía por grupo de `calendar_service`, sin agregar N+1 por celda.

## Modal compacto

El botón **Elegir fecha desde mi calendario** se habilita con JavaScript en
navegadores con `<dialog>`. El ingreso manual permanece disponible si no hay
JavaScript o soporte de dialog. El modal abre el mes de la fecha escrita, o el
mes actual si está vacía, y marca la selección. Permite anterior, siguiente y Hoy
sin recargar el formulario ni perder sus demás campos.

Cada día es un botón completo, también con eventos. Los eventos se presentan como
texto, sin enlaces: hacer click sobre uno selecciona el día y conserva la hora
de la propuesta. No se bloquean días ocupados ni se detectan conflictos.

El popup conserva la grilla mensual de siete columnas, los tonos de fines de
semana/feriados, el círculo de Hoy y el borde de selección. Cada evento muestra
`HH:mm · nombre` en desktop; en móvil, hora y nombre en dos líneas compactas para
mantener legible el horario. Las clases diferencian Confirmado/Finalizado.
Feriado y evento conviven en la celda; el nombre completo del feriado también
está en el nombre accesible y título.

Se muestran hasta dos eventos por celda y `+N más` cuando corresponde. El desplegable
**Ver todos los encuentros del mes** conserva nombres, fechas, horarios y estados
de todos, sin navegación a otros planes. Los nombres largos se truncan en la
grilla y pueden consultarse en esa lista. Los eventos de días adyacentes fuera
del mes siguen el criterio del calendario completo: se cargan al navegar a su mes.

El ancho máximo es 840 px y las filas tienen 80 px en desktop, 100 px en móvil.
La altura máxima es 90dvh, con scroll interno y sin scroll horizontal. Se cierra
con ×, Escape o click en el fondo; vuelve el foco al botón que lo abrió. Los días
son botones nativos para Tab/Enter/Espacio, y admiten flechas para moverse entre
las celdas. Ninguna de estas acciones envía el formulario.

Las cargas usan `fetch` del mismo origen, sin caché, y cancelan solicitudes viejas
al navegar/cerrar. Se evita que una respuesta tardía sobrescriba otro mes o que un
cierre anterior cancele una reapertura rápida. Ante fallos o sesión vencida se
muestra un mensaje y reintento, sin presentar un mes vacío como disponibilidad
confirmada. El usuario puede cerrar y seguir escribiendo manualmente.

## Tests y validación

Desde `backend/`, con el virtualenv activo:

```powershell
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py test
```

Desde la raíz: `git diff --check` y `git status`.

Tests nuevos:

- `apps.users.tests.test_calendar_selector`: parcial privado, JWT y membresía
  usando el servicio real, timezone, estados, consulta mensual/caché por grupo,
  sin badge, navegación/límites, selección, Hoy, fin de semana y feriado con evento,
  celda densa/agenda, escape HTML, método HTTP, sesión anónima/expirada y errores.
- `apps.plans.tests.test_proposal_calendar`: controles y acceso en creación/edición,
  datetime local combinado, POST anterior compatible, prioridad del nuevo formato,
  valores vacíos/parciales, validación, permisos de edición y conservación de valores.

Prueba opcional con Chrome local, datos ficticios y acceso externo bloqueado:

```powershell
python -X utf8 -m apps.plans.tests.browser_proposal_calendar
```

Usa Chrome headless y DevTools para viewports reales de 320, 390 y 1280 px. La
ruta predeterminada es `C:\Program Files\Google\Chrome\Application\chrome.exe`;
si difiere, indicar `PLANB_CHROME` en las variables del proceso, sin editar `.env`.
No instala paquetes: utiliza `websockets`, ya presente como dependencia de Supabase.
El servidor temporal sólo escucha en loopback y usa fixtures; no ejecuta un
servidor Django de producción ni consulta Supabase. Guarda capturas en TEMP.

Esta prueba usa DOM, templates, CSS y JavaScript reales: apertura por mes actual
y fecha existente, navegación/Hoy, selección, conservación de `21:30`, hora vacía,
cierre/reapertura, ausencia de submit, click en evento informativo, fallo/reintento,
light/dark, densidad, feriado con horario, teclado Escape y desbordes horizontales.
La suite Django continúa siendo offline y cubre las regresiones de `/calendario/`
y propuestas. No se modifican notificaciones, scheduler, estados de planes o SQL.

Resultado local: **516 tests correctos** (491 existentes + 25 nuevos), con ambos
`manage.py check` sin problemas. El bloque específico de calendario/propuestas
ejecutó 132 tests correctos. Chrome verificó 21 comprobaciones en cada viewport
(320, 390 y 1280 px), incluido Escape enviado por DevTools, con temas claro/oscuro,
sin overflow horizontal. En desktop, un mes de seis semanas ocupa un modal de
840 × 716 px en un viewport de 1280 × 900; en móvil el alto queda acotado y se
usa scroll interno. Se revisaron capturas del DOM/CSS real con fixtures locales.
`git diff --check` pasó. No hubo commit, push, SQL remoto ni cambios en `.env`.

## Pasos manuales

1. Iniciar sesión y entrar a un plan que esté En organización.
2. Crear una propuesta; completar título, ubicación y demás campos.
3. En Hora escribir `21:30`; en Fecha escribir una fecha de noviembre de 2026.
4. Pulsar **Elegir fecha desde mi calendario**. Verificar que abre noviembre y
   marca el día escrito.
5. Probar anterior/siguiente y Hoy; volver a noviembre.
6. Revisar los encuentros y sus horas; elegir el día 20, incluso si tiene planes.
7. Verificar que se cerró el modal, Fecha es `20/11/2026` y Hora sigue en `21:30`.
8. Guardar con **Crear propuesta**. Volver a editarla y repetir cambiando sólo el día.
9. Borrar Hora, elegir otro día y comprobar que sigue vacía. Completarla antes de guardar.
10. Abrir y cerrar con ×, Escape y fondo: comprobar que no cambia ningún campo.
11. Revisar un día feriado y uno con muchos encuentros; expandir la lista mensual.
12. Repetir en claro/oscuro, móvil y teclado. Abrir `/calendario/` y comprobar que
    su agenda, navegación y enlaces siguen funcionando como antes.

## Archivos

Modificados: `backend/apps/plans/forms.py`,
`backend/apps/plans/templates/plans/proposal_form.html`,
`backend/apps/users/calendar_month.py`, `backend/apps/users/calendar_views.py`,
`backend/apps/users/urls.py`, `backend/apps/users/templates/users/calendar.html`,
`backend/apps/users/templates/users/calendar_event.html`,
`backend/apps/users/static/users/css/personal-calendar.css` y `README.md`.

Creados: `backend/apps/plans/templates/plans/widgets/proposal_datetime.html`,
`backend/apps/users/templates/users/calendar_holiday.html`,
`backend/apps/users/templates/users/calendar_selector.html`,
`backend/apps/users/static/users/css/proposal-calendar.css`,
`backend/apps/users/static/users/js/proposal-calendar.js`,
`backend/apps/plans/tests/test_proposal_calendar.py`,
`backend/apps/users/tests/test_calendar_selector.py`,
`backend/apps/plans/tests/browser_proposal_calendar.py` y esta guía.
