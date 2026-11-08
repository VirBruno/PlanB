# Ajustes incrementales del detalle confirmado

Validación local: 6 de noviembre de 2026. Se conserva la arquitectura aprobada: encabezado compacto, hero, “Organizar el encuentro”, selector de tres herramientas, una herramienta activa, drawer derecho y sus breakpoints. No se modificó ningún archivo JavaScript de producción.

1. **Hero principal.** Se enriqueció el hero existente con tipo, título, avatar y autor, indicación de propuesta elegida, descripción, fecha y hora, presupuesto, pronóstico resumido y ubicación. Dos columnas en escritorio; información, clima y mapa en ese orden hasta 900 px. El contenido completo sigue disponible, sin truncar descripciones.
2. **Información trasladada.** Avatar, indicador de elección, bloque meteorológico y preview del mapa que estaban en la ficha ganadora del drawer ahora aparecen en el hero. La descripción y el presupuesto ya estaban en el hero y se conservaron.
3. **Drawer.** Mantiene información general del plan, acciones permitidas, alternativas, método de elección, votación/comparación y resultado. La propuesta elegida aparece como referencia compacta con título, autor y fecha, indicando dónde está la información completa.
4. **Duplicación.** La ficha completa ganadora se renderiza una sola vez. Se extrajeron únicamente los fragmentos existentes de clima y ubicación a dos partials compartidos: mismos atributos, URLs e inicializadores. Las alternativas conservan sus fichas completas. Las pruebas comprueban un solo bloque de clima/mapa por propuesta y una sola consulta meteorológica para la elegida al recorrer el drawer.
5. **Mapa.** Se reutiliza el preview Leaflet existente con las coordenadas de la propuesta confirmada y el enlace original a OpenStreetMap. Altura de 300 px en escritorio y 220 px al apilarse. Se conserva el inicializador y su ajuste de tamaño. No se agregó otro mapa para la ganadora en el drawer.
6. **Clima resumido.** Usa el mismo endpoint y `proposal-weather.js`: condición, mínima/máxima, lluvia, viento, fuente Open-Meteo y aviso orientativo. Se mantienen sus estados de carga, fecha fuera de horizonte e indisponibilidad; no hay cálculos ni consultas a un proveedor nuevo.
7. **Gastos.** Total y balance propio tienen una grilla uniforme y la misma altura, con apilado a 320 px. El CTA acompaña al título del listado y usa la paleta existente. Cada fila separa concepto, pagador, fecha, importe y reparto desplegable. Formularios, importes y reglas no cambiaron.
8. **Balances.** La tabla conserva su semántica y columnas. Ahora tiene ancho completo, encabezados diferenciados, cifras alineadas a la derecha, números tabulares y colores existentes para positivos/negativos. En pantallas angostas el scroll queda dentro de una región etiquetada y accesible con teclado, sin desbordar la página.
9. **Para saldar.** Cada transferencia muestra deudor → acreedor y el importe en una línea propia. La dirección tiene texto accesible. Los botones de notificación, permisos, campos ocultos, CSRF y mensajes existentes se conservan.
10. **Scrollbar de Clima.** `scrollbar-width: none` y `::-webkit-scrollbar` ocultan únicamente la barra del carrusel; se incluye la propiedad de compatibilidad con motores antiguos. No se ocultó el overflow.
11. **Desplazamiento lateral.** Se mantienen `overflow-x: auto`, snap y botones de día nativos. Se agrega el hint “Deslizá para ver más días” y foco en el contenedor. El navegador verifica overflow real, cambio de `scrollLeft`, foco de los días y selección conservada al alternar herramientas. No se añadieron handlers que intercepten gestos ni se cambió la búsqueda/comparación.
12. **Archivos.**

    | Archivo | Cambio |
    | --- | --- |
    | `backend/apps/plans/templates/plans/detail.html` | Versiones de los tres CSS modificados |
    | `backend/apps/plans/templates/plans/partials/detail_decision.html` | Información y composición del hero |
    | `backend/apps/plans/templates/plans/partials/detail_proposal_list.html` | Referencia ganadora compacta y reutilización de fragmentos |
    | `backend/apps/plans/templates/plans/partials/proposal_weather.html` | Nuevo partial con el markup meteorológico existente |
    | `backend/apps/plans/templates/plans/partials/proposal_location.html` | Nuevo partial con el preview/enlace existente |
    | `backend/apps/plans/templates/plans/partials/detail_weather.html` | Hint y foco del carrusel |
    | `backend/apps/plans/templates/plans/expenses/panel.html` | Jerarquía de filas/transferencias y región de balances |
    | `backend/apps/users/static/users/css/plan-confirmed-workspace.css` | Estilos acotados al hero, referencia y Gastos inline |
    | `backend/apps/users/static/users/css/plan-expenses.css` | Presentación compartida del pagador y transferencia |
    | `backend/apps/users/static/users/css/plan-weather-planner.css` | Scrollbar oculta y foco visible |
    | `backend/apps/plans/tests/test_recommendations_ui.py` | Hero completo, ausencia de duplicación y alternativas preservadas |
    | `backend/apps/plans/tests/test_expenses.py` | Aserción del nuevo markup accesible de transferencia |
    | `backend/apps/plans/tests/browser_confirmed_layout.py` | Pronóstico de 16 días y verificaciones de los ajustes |
    | `backend/apps/plans/tests/browser_recommendations.py` | Capturas adicionales de Gastos, balances y Clima |
    | `docs/testing/detalle-confirmado-ajustes.md` | Este informe |

13. **Responsive.** Chrome local en 320×900, 390×900, 820×1000, 1366×768, 1440×900 y 1920×1080. Capturas claras y oscuras del hero, drawer, Gastos, balances y Clima. Sin scroll horizontal general; drawer móvil completo y desktop lateral. Se comprueban herramienta única, foco, cierre, mapa, conservación de borradores/selecciones y generación única de recomendaciones. La compatibilidad de scrollbar con Firefox/WebKit está cubierta por CSS; no se ejecutaron esos navegadores.
14. **Pruebas ejecutadas.** Todos los proveedores y servicios de las pruebas están simulados. Los browsers usan Django/Chrome/Leaflet locales y bloquean DNS externo; los tiles de las capturas completas son una cuadrícula ficticia, no cartografía real.

    | Comando desde `backend` | Resultado |
    | --- | --- |
    | `python manage.py test apps.plans.tests.test_recommendations_ui apps.plans.tests.test_expenses --settings=config.settings_test --noinput` | 70 tests OK |
    | `python manage.py test apps.plans --settings=config.settings_test --noinput` | 368 tests OK |
    | `python manage.py test --settings=config.settings_test --noinput` | 786 tests OK |
    | `python -m apps.plans.tests.browser_confirmed_layout` | 60 verificaciones por resolución, seis resoluciones OK |
    | `python -m apps.plans.tests.browser_recommendations` | 107 verificaciones por resolución móvil/tablet; 111 por resolución desktop, seis resoluciones OK |
    | `python -m apps.plans.tests.browser_expenses` | 16 verificaciones en cada ancho: 320, 390 y 1280 px, OK |
    | `python -m apps.plans.tests.browser_expenses --debts` | 14 verificaciones en cada ancho: 320, 390 y 1280 px, OK |

    Los ejecutables de navegador imprimen la carpeta temporal con sus capturas e informes JSON. La suite pasa de los 784 tests presentes al inicio a 786 por las dos regresiones nuevas de duplicación/alternativas.
15. **`python manage.py check`.** `System check identified no issues (0 silenced).`
16. **`git diff --check`.** Sin errores de whitespace. Git informa conversión futura LF → CRLF en archivos de trabajo, sin errores del chequeo.

Sin cambios a servicios, Gemini, Geoapify, recomendaciones/fingerprint, lógica de gastos o clima, confirmación, migrations, RPC, RLS ni `.env`. Sin APIs nuevas, SQL remoto, commit ni push. Se conserva la restricción existente que impide editar una propuesta confirmada.
