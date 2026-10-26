# Calendario personal

Ruta: `/calendario/`, nombre `users:calendar`. Requiere la sesión de Supabase del usuario.

## Modelo existente y fuente de fecha

El estado no es un enum ni una columna nueva:

| status | selected_proposal_id | Estado |
| --- | --- | --- |
| true | NULL | En organización |
| true | presente | Confirmado |
| false | presente | Finalizado |
| false | NULL | Inactivo |

La migración existente `202610230001_plan_decisions.sql` define la decisión.
La RPC `confirm_plan_proposal` verifica que la propuesta pertenezca al plan y tenga
fecha y ubicación. Guarda `selected_proposal_id`, copia exactamente su `date_pick`
a `plans.scheduled_at` y registra `confirmed_at`. Los triggers bloquean cambios en
la propuesta y en la fecha confirmada. Finalizar conserva la selección y la fecha;
reabrir limpia ambas.

El calendario usa exclusivamente `scheduled_at` cuando hay `selected_proposal_id`.
No usa `confirmed_at`, fechas tentativas ni otras propuestas. Si faltan las columnas
de decisión, devuelve indisponibilidad (503); no aplica el fallback legacy de la
lista general de planes. Una decisión sin fecha no genera un evento; una fecha
sin timezone se rechaza. El schema inspeccionado ya garantiza la fecha confirmada,
por lo que no se necesitan migraciones, campos, relaciones ni dependencias nuevas.
No se consultaron datos ni se ejecutó SQL remoto para verificar el schema desplegado.

## Organización

- `plans.services.plan_service.list_meetings`: consulta por intervalo semiabierto,
  selección no nula, orden estable y paginación en lotes de 500. Incluye activos e
  históricos sin limitarse a las 12 filas del listado de planes.
- `users.services.calendar_service.list_events`: reutiliza `get_group` del detalle
  para comprobar membresía actual, una vez por grupo y petición. Omite grupos sin
  acceso, incluso si el usuario creó el plan. Los fallos del proveedor se distinguen
  de un mes sin eventos. Todas las consultas usan public client y JWT, con RLS;
  no se utiliza secret key ni service_role.
- `users.calendar_month`: valida año (1900–2100) y mes, calcula límites locales,
  navegación y semanas desde el lunes. Valores inválidos vuelven al mes actual;
  en los extremos se oculta la navegación que saldría del rango.
- `users.calendar_views`: coordina el servicio y renderiza el layout autenticado.
  Se usa la app de usuarios porque ya contiene dashboard y perfil personales.
- Templates y CSS específicos del módulo, con iconos SVG y variables existentes.
  En desktop hay siete columnas y scroll vertical dentro del día para consultar
  todos los eventos. Hasta 900 px, un mes compacto enlaza a una agenda del mismo
  mes sin ocultar encuentros. El sidebar móvil conserva sus seis accesos.

Los límites y la presentación usan el timezone actual de Django (`USE_TZ=True`,
`America/Argentina/Buenos_Aires` por defecto). El filtrado incluye desde la medianoche
local del primer día hasta, sin incluir, la medianoche del primer día del siguiente mes.

## Verificación automática

Se agregan 51 tests del calendario (39 de la implementación inicial y 12 de feriados) en:

- `apps.users.tests.test_calendar`: construcción del mes, año bisiesto, seis semanas,
  límites de navegación, parámetros inválidos, estados, membresía revocada, grupo
  ajeno, varias citas por día, fecha local, timezone activo, autenticación, privacidad
  de caché, errores, escape HTML, navegación móvil y apertura del detalle existente.
- `apps.users.tests.test_calendar_holidays`: fechas oficiales observadas, traslados,
  días turísticos, exclusiones territoriales/religiosas, año sin datos, fines de
  semana, coincidencia de feriado y fin de semana, accesibilidad de nombres,
  navegación y conservación de eventos, estados y enlaces al detalle.
- `apps.plans.tests.test_calendar_service`: contrato del SDK real con HTTP en memoria,
  JWT y publishable key, filtros, paginación completa, errores y schema sin decisión.

Desde `backend`, usando el Python del virtualenv:

```text
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py test apps.users.tests.test_calendar apps.users.tests.test_calendar_holidays apps.plans.tests.test_calendar_service
python manage.py test
```

La suite utiliza `settings_test`, SQLite en memoria y el runner que bloquea red/DNS.
Resultado: 455 tests correctos (443 existentes + 12 de feriados). Ambos checks sin errores.
Los 443 tests de la entrega inicial siguen pasando; no se modificaron sus expectativas.

## Revisión visual local

Se renderizó el template real con datos ficticios y los estilos actuales de la app,
sin consultar servicios externos. Chrome headless verificó viewport de 320, 390, 768,
1024 y 1440 px, temas claro/oscuro, seis accesos del sidebar, enlaces del resumen a
la agenda, varios encuentros en el mismo día y nombres extensos. Las mediciones
confirmaron que la página no desborda horizontalmente.

Revisión manual con una sesión real, si se desea validar datos del entorno:

1. Abrir Calendario; comprobar estado activo, mes actual y día de hoy.
2. Verificar un plan confirmado y uno finalizado contra la propuesta elegida.
3. Navegar diciembre → enero y enero → diciembre; volver con Hoy.
4. Abrir un evento y comprobar el detalle habitual de ese plan.
5. Revisar un mes vacío: la grilla y su navegación deben mantenerse.
6. En móvil, elegir un día con eventos y recorrer su agenda; cambiar de tema.
7. Con un usuario ajeno al grupo, comprobar que el encuentro no aparece.


## Densidad, fines de semana y feriados argentinos

La grilla mantiene siete columnas y todo el ancho útil en desktop. Usa filas
`minmax(104px, auto)` y altura natural según su contenido, sin alturas basadas en
viewport ni obligación de llenar la pantalla. Los días con muchos eventos tienen
scroll local a partir de 136 px de contenido. Se redujeron paddings y separación
entre breadcrumb, encabezado, leyenda y grilla. La vista móvil con agenda se conserva.

`calendar_month` aporta `is_weekend` (sábado/domingo) y `holiday` sin intervenir en
la selección de eventos ni los estados de planes. Los fines de semana tienen fondo
neutro/lila tenue, también en encabezados y días vacíos. Un feriado usa otro tono
y una etiqueta textual; Hoy conserva prioridad sobre ambos fondos y los eventos
mantienen su propia apariencia de Confirmado/Finalizado.

En desktop el nombre breve del feriado no desplaza los eventos. Un desplegable
nativo `details/summary` permite consultar el nombre completo mediante clic, Enter
o barra espaciadora, además del título y nombre accesible. En móvil hay un punto
junto al día, nombre accesible y lista desplegable de feriados del mes; en la agenda
se muestra el nombre cuando ese día tiene encuentros. Los turísticos llevan la
etiqueta **No laborable**, para no equipararlos con un feriado nacional obligatorio.

### Provider y fuentes

`backend/apps/users/holidays.py` mantiene una tabla explícita de 20 fechas de alcance
general para 2026. Las fechas son locales y observadas (por ejemplo, 15/6 y 23/11,
no sus días de origen 17/6 y 20/11). No realiza requests, scraping ni cálculos de
traslado en runtime, ni agrega dependencias. Fuentes oficiales verificadas el
**26 de octubre de 2026**:

- [Calendario nacional de Argentina](https://www.argentina.gob.ar/feriados).
- [Listado 2026 de Cancillería](https://ejapo.cancilleria.gob.ar/es/content/feriados-y-diferencia-horaria),
  para contrastar fechas y traslados. Se usan las fechas civiles indicadas en el
  listado y las normas, sin convertirlas desde timestamps de la página web.
- [Resolución 164/2025, artículo 1](https://www.argentina.gob.ar/normativa/nacional/norma-421799/texto):
  días no laborables turísticos 23/3, 10/7 y 7/12 de 2026.
- [DNU 1103/2026, artículo 2](https://www.argentina.gob.ar/normativa/nacional/norma-430580/texto):
  9/11/2026, feriado nacional de alcance general por la visita del Papa León XIV.
  Se excluyen los días 10 y 11/11 del mismo decreto por su alcance territorial.

También se excluyen celebraciones limitadas a determinados grupos religiosos,
feriados provinciales, municipales y asuetos sectoriales. Viernes Santo, Navidad
e Inmaculada Concepción se incluyen por ser feriados nacionales generales.

Para incorporar 2027 u otro año, agregar su tabla verificada a `HOLIDAYS_BY_YEAR`,
actualizar estas referencias y extender los tests de fechas observadas. No se
extrapolan feriados a años sin datos: `holiday_for` devuelve `None` y el calendario
sigue mostrando el mes y los fines de semana normalmente. Los días adyacentes
usan, si existe, la tabla de su propio año.

### Revisión visual de este ajuste

Chrome local verificó temas claro/oscuro, desktop de 1024 y 1440 px y móvil de 320
y 390 px, con fechas y eventos ficticios. Sin overflow horizontal. Un mes vacío de
seis semanas ocupa 624 px de grilla; un mes con días concurridos aumenta sólo las
filas que lo necesitan. Se verificaron fondos distintos de Hoy/feriado/fin de
semana, eventos confirmados y finalizados sobre un feriado en sábado, scroll local,
enlaces a la agenda y despliegue del nombre de Navidad con Enter.
