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

Se agregan 39 tests en:

- `apps.users.tests.test_calendar`: construcción del mes, año bisiesto, seis semanas,
  límites de navegación, parámetros inválidos, estados, membresía revocada, grupo
  ajeno, varias citas por día, fecha local, timezone activo, autenticación, privacidad
  de caché, errores, escape HTML, navegación móvil y apertura del detalle existente.
- `apps.plans.tests.test_calendar_service`: contrato del SDK real con HTTP en memoria,
  JWT y publishable key, filtros, paginación completa, errores y schema sin decisión.

Desde `backend`, usando el Python del virtualenv:

```text
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py test apps.users.tests.test_calendar apps.plans.tests.test_calendar_service
python manage.py test
```

La suite utiliza `settings_test`, SQLite en memoria y el runner que bloquea red/DNS.
Resultado: 443 tests correctos (404 anteriores + 39 nuevos). Ambos checks sin errores.
La suite previa a los cambios también pasó: 404 tests, sin fallos preexistentes.

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
