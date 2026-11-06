# Detalle confirmado · divulgación progresiva

## 1. Arquitectura encontrada y baseline

`plans/detail.html` concentraba encabezado, metadata, comparador de clima,
encuentro, acceso al popup de gastos, método/resultados/listado de propuestas y
recomendaciones en un único flujo vertical. Los inicializadores buscan atributos
globales del DOM; recomendaciones se inicializa al cargar y gastos consultaba su
fragmento al abrir el dialog. Leaflet tiene instancias para sugerencias y previews.

Antes de editar: **779 tests OK**, 19.243 s, con
`manage.py test --settings=config.settings_test --noinput` y bloqueo de red.

## 2. Arquitectura final

Sólo el plan activo confirmado (`show_recommendations`, condición existente)
usa **encuentro → herramientas → contexto bajo demanda**:

- Encabezado compacto con título, regreso al grupo y Detalles del plan.
- Hero Encuentro confirmado con propuesta elegida y datos relevantes.
- Organizar el encuentro con tres selectores compactos, inicialmente cerrados.
- Un workspace con una única herramienta visible a la vez.
- Drawer contextual para datos secundarios y proceso de decisión en acordeones.

No se agrega un estado de negocio ni un endpoint. Los planes en organización,
sin selección, inactivos y finalizados conservan el recorrido anterior.

## 3. Archivos

Modificados:

- `backend/apps/plans/templates/plans/detail.html`.
- `backend/apps/plans/templates/plans/expenses/modal.html`.
- JS `plan-expenses.js`, `plan-recommendations.js`, `proposals.js`.
- Tests `test_recommendations_ui.py`, `test_views.py` y harness `browser_recommendations.py`.

Creados:

- Partials `detail_confirmed`, `detail_organizing`, `detail_weather`, `detail_decision`,
  `detail_proposals`, `detail_election_method`, `detail_election_results`,
  `detail_proposal_list`, todos bajo `plans/partials/`.
- `users/css/plan-confirmed-workspace.css`.
- `users/js/plan-confirmed-workspace.js`.
- `backend/apps/plans/tests/browser_confirmed_layout.py`.
- Este informe.

Los partials extraen contenido existente y se comparten entre los recorridos;
no se generan copias ocultas de cada herramienta ni IDs duplicados.

## 4. Estado, encuentro y propuesta ganadora

La representación principal es el hero: estado confirmado, título de la propuesta,
descripción, autor/tipo, presupuesto, fecha y enlace a ubicación. Grupo y título
del plan quedan en el encabezado. Descripción general/creación del plan pasan al
drawer. La propuesta sigue en el listado histórico completo y como resultado
breve, sin otra card ganadora protagonista en el flujo principal.

Los formularios de finalizar/reabrir conservan condiciones, URLs, confirmaciones
y CSRF; para el owner se agrupan en **Acciones del encuentro** dentro del hero.
Ver proceso de decisión abre el drawer, expande Resultado y enfoca el encabezado
del proceso. No se modifica el bloqueo de edición de confirmados.

## 5. Organizar el encuentro

Tres botones con nombre, copy corto y acción: Clima, Gastos compartidos y
Sugerencias con IA. Desktop usa tres columnas; tablet puede usar 2+1 y mobile
stack. La selección se expresa con borde/fondo, `aria-expanded` y texto Cerrar.
La misma tarjeta puede cerrar la herramienta. Cada panel tiene su control de cierre.

## 6. Herramientas

- **Clima:** mismo comparador, selects, búsquedas y pronóstico. Al abrir se despliega
  el details interno para evitar una segunda apertura. Cambiar de herramienta
  conserva provincia, localidad y día seleccionado.
- **Gastos:** mismo encabezado/fragmento, balances, formularios, guardado y avisos.
  En confirmados se monta inline; en los demás estados conserva el dialog original.
  La primera apertura consulta el fragmento; alternar conserva el borrador y estado.
  El control existente Actualizar gastos permanece accesible para refrescar cuentas
  explícitamente. Guardar conserva su POST y posterior GET, sin reenviar el gasto.
- **IA:** mismo módulo, summary, cards, mapa, enlaces y estados. La generación
  automática continúa al cargar el detalle, aunque el panel esté visualmente oculto.
  Abrir/cerrar no llama el inicializador ni dispara otra generación.

## 7. Workspace

Una instancia de cada módulo en su panel, con atributo `hidden` para visibilidad.
El controlador sólo selecciona paneles y emite eventos de apertura/cierre para
adaptar presentación y tamaño. No hay desmontaje, recreación de formularios al
alternar ni persistencia en base/localStorage. Recargar vuelve al estado compacto,
salvo un enlace explícito a una sección. Sin JS se ofrece un fallback mostrando
herramientas y detalles, con los acordeones HTML nativos.

## 8. Drawer

Se implementa con `dialog.showModal()`, en el top layer del navegador. Ancho de
540 px como máximo contextual, página detrás visible e inerte, backdrop suave,
header fijo dentro del panel y cuerpo con scroll propio. No depende de offsets
de la sidebar. Los dialogs internos de propuesta ideal mantienen su carácter
modal independiente; el controlador los coloca fuera del drawer cerrado sin
duplicarlos y devuelve al proceso al cerrarlos.

## 9. Contenido del drawer

Información del plan: descripción, fecha de creación y enlaces de administración
según permisos existentes. Proceso de decisión: acordeones independientes para
Propuestas, Método, Votación/comparación o Combate y Resultado. Se conserva el
listado completo con sus mapas, pronósticos, presupuestos, autores y resultados.
El copy histórico evita indicar que se puede votar/jugar cuando ya está confirmado.
Clima completo, gastos completos e IA permanecen fuera del drawer.

## 10. Mobile y ancho útil

El detalle confirmado usa hasta 1200 px disponibles dentro del shell existente,
sin cambios de ancho en otros screens. A 320/390 px el mismo drawer ocupa la
pantalla completa; no se crea una lógica mobile distinta. Las herramientas se
apilan y la metadata de las propuestas dentro del drawer se adapta para no
producir scroll horizontal. Botones principales/cierre tienen targets de 44 px o más.

## 11. Planes no confirmados

El partial tradicional conserva encabezado/metadata, clima, decisión, dialog de
gastos y propuestas/método/votación en el flujo principal. No se cargan los nuevos
assets del workspace ni se renderiza el drawer. Se verifica explícitamente el
recorrido en organización y el finalizado, además de los tests existentes de planes.

## 12. Accesibilidad y enlaces

Controles con `aria-controls`/`aria-expanded`, títulos relacionados, foco visible
y nombres de cierre. El drawer usa modal nativo y focus trap para Tab/Shift+Tab,
Escape, X y click fuera del panel. Al cerrar devuelve foco al disparador y restaura
el scroll del body. Ver proceso enfoca Cómo llegamos a esta decisión. Los acordeones
son details/summary nativos. No hay animaciones fuertes y se respeta reduced-motion.

Se admiten `#clima`, `#gastos`, `#sugerencias`, `#decision`, más los anchors de
método/combate/propuestas existentes. `?open=expenses` abre Gastos y se limpia al
cerrar/cambiar esa herramienta, conservando el acceso desde recordatorios.

## 13. Leaflet al abrir contenido oculto

Se conserva la misma instancia del mapa. Sugerencias invalida tamaño al abrir
y reajusta bounds sólo si se creó oculta. ResizeObserver sigue atendiendo cambios
de tamaño sin nuevas búsquedas. Los previews históricos reciben invalidateSize
al volverse visibles. No se reconstruyen mapas al alternar herramientas.
El navegador comprueba identidad de instancia y selección card ↔ marker.

## 14. Requests antes/después

- IA conserva el mismo GET de estado y, cuando corresponde, un POST automático.
  Abrir/cerrar herramientas o el drawer no agrega requests de generación.
- Estado de la tarjeta IA se deriva con MutationObserver del DOM ya actualizado;
  el de gastos usa el resumen existente. No hay APIs decorativas.
- Clima mantiene las mismas consultas por acciones del usuario; los pronósticos
  de propuestas conservan sus inicializadores y cantidad de elementos.
- Gastos sigue sin consultar hasta la primera apertura. Alternar mantiene su
  estado local; Actualizar gastos permite consultar de nuevo de manera explícita.
- Los tiles OSM necesarios al visualizar/redimensionar mapas siguen siendo parte
  de su presentación. No son llamadas a Gemini ni Geoapify.

No se modifican servicios, ranking, fingerprint, RPC/RLS, schema, permisos,
confirmación, votación, gastos o proveedores meteorológicos. No hay SQL ejecutado.

## 15. Tests

Se actualizan las aserciones que antes exigían IA debajo de todas las propuestas
y una única aparición del nombre (ahora aparece en selector y módulo). Se verifica
que el módulo real sigue siendo único. Cinco tests nuevos cubren tres paneles
inicialmente cerrados, drawer histórico sin herramientas activas, datos del hero,
regresión no confirmada/finalizada y acciones/deep-link de gastos. Se actualiza
la versión esperada del asset de mapas, sin cambiar la prueba de propuesta ideal.

El harness completo usa Django/Leaflet/Chrome reales, fragmento real de gastos,
respuestas simuladas y DNS externo bloqueado. Comprueba búsquedas/días de clima,
borrador de gastos, POST/CSRF, cards/mapa, generación oculta única, selección de
herramientas, ancho/scroll del drawer, focus trap, Escape, X, backdrop y hashes.

## 16. Browser por viewport

El nuevo harness pasó **46 comprobaciones** en cada viewport: 320×900, 390×900,
820×1000, 1366×768, 1440×900 y 1920×1080. Incluye la ausencia de scroll horizontal
dentro del drawer con propuestas abiertas, además del documento principal.
Capturas light/dark del estado compacto, drawer y sugerencias:
`%TEMP%\planb-recommendations-browser-hcsv9fgo`.

También se ejecutó la regresión original de recomendaciones: 107 checks en
320/390/820 y 111 en 1366/1440/1920, todos OK. El dialog original de gastos pasó
16 checks en cada ancho 320, 390 y 1280, incluyendo guardado, balances, error
incierto, reconsulta, foco, Escape y mantenimiento de URL/scroll.

## 17. Django check

`manage.py check`: `System check identified no issues (0 silenced).`

## 18. Suites

Tests específicos: `manage.py test apps.plans --settings=config.settings_test --noinput`:
**366 tests OK**, 25.256 s. Incluye detalle, propuestas/votación, clima, gastos y recomendaciones.

Suite completa: `manage.py test --settings=config.settings_test --noinput`:
**784 tests OK**, 51.788 s. Cinco tests nuevos sobre el baseline de 779.
Logs temporales: `%TEMP%\planb-confirmed-ux-specific.log` y
`%TEMP%\planb-confirmed-ux-final.log`.

## 19. Diff check

`git diff --check`: sin errores. Se verificó que no hay diferencias en servicios,
views Python de negocio, SQL/Supabase, configuración ni `.env`. El estado final
contiene ocho archivos modificados y doce nuevos, todos detallados arriba.

## 20. Decisiones para revisión visual

El cierre del encuentro se mantiene en el hero bajo un disclosure pequeño para
evitar botones administrativos grandes siempre visibles. Los textos largos no
se recortan: pueden aumentar su altura. Los acordeones conservan su estado dentro
de la navegación; no se escribe esa preferencia en ningún storage. Al volver a
Gastos se conserva el trabajo local; actualizar datos es una acción explícita.

Las capturas utilizan datos simulados y fondos de mapa locales; no se probó con
APIs reales. No se hicieron commit/push, no se modificó `.env` y no se ejecutó SQL.
