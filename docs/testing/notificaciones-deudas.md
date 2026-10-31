# Prueba manual de notificaciones de deudas

## Preparación

Revisar y aplicar la nueva migración `202610300003_expense_debt_notifications.sql`
en el entorno autorizado siguiendo las convenciones del proyecto. La tarea de
implementación no ejecuta SQL remoto ni modifica migraciones anteriores.
La migración previa de gastos ya debe estar aplicada.

Grupo con Juan, Facundo y Martina, todos integrantes actuales. Abrir el plan
«Cena del viernes» y cargar Cena $30.000, pagador Juan, TODO EL GRUPO. Dejar
pendientes las transferencias Facundo → Juan $10.000 y Martina → Juan $10.000.
Usar dos sesiones/navegadores para evitar confundir el usuario activo.

## Envío, actualización y lectura

1. Ingresar como Juan, abrir el detalle y el popup Gastos compartidos.
2. En Para saldar deben aparecer «Notificar a Facundo» y «Notificar a Martina».
3. Pulsar «Notificar a Facundo». El botón se deshabilita durante el envío y
   aparece «Notificación enviada a Facundo.». El popup sigue abierto.
4. Antes de leer, repetir desde Juan. Debe informar que ya existe un pendiente;
   Facundo debe conservar sólo uno. Un doble click no debe duplicarlo.
5. Ingresar como Facundo: ver badge con una notificación nueva. En la bandeja,
   comprobar «Saldo pendiente · Cena del viernes» y $10.000 a Juan.
6. Sin marcarla como leída, pulsar «Ver gastos»: llegar al detalle del plan con
   el popup abierto automáticamente. Abrir el enlace no marca el aviso como leído.
7. Volver como Juan y agregar otro gasto: concepto `Consumo de Facundo`, monto
   `2.000`, pagador Juan, seleccionar sólo Facundo. Su transferencia pasa a $12.000.
8. Pulsar nuevamente «Notificar a Facundo». En la bandeja de Facundo debe seguir
   existiendo una sola fila pendiente, ahora por $12.000, sin otro incremento del badge.
9. Facundo marca esa notificación como leída. Debe desaparecer del contador.
10. Juan vuelve a notificar. Como la deuda continúa, debe crearse otro aviso
    no leído; el anterior conserva su historia.

## Autorización y cambios de saldo

1. Facundo y Martina pueden ver las transferencias, pero no botones para enviar
   avisos en nombre de Juan. El owner tampoco puede enviar en nombre de otro.
2. Manipular el POST con monto, acreedor o destinatario alternativo: debe ser
   rechazado. Sólo se acepta un `debtor_id` y CSRF; el plan viene de la ruta.
3. Un usuario ajeno no puede abrir el detalle, gastos ni invocar el aviso.
4. Para eliminar la transferencia Facundo → Juan de $12.000 sin borrar historial,
   cargar un gasto de $12.000 pagado por Facundo y dividido sólo entre Juan.
   El botón desaparece; una acción vieja debe ser rechazada.
5. El aviso anterior continúa como snapshot con la aclaración sobre cuentas
   actuales. No hay actualización ni eliminación automática.
6. Probar avisos independientes para Martina y en otro plan. Con varios
   acreedores sólo pueden notificarse las parejas presentes en Para saldar.
7. Si el entorno permite simular una baja de membresía, confirmar que el
   exintegrante conserva los avisos pero sin «Ver gastos», y que un enlace viejo
   rechaza acceso. No se agrega UI de bajas en este bloque.
8. Probar X, Escape, tema claro/oscuro y móvil. Cerrar elimina sólo `open=expenses`
   de la URL; conserva otros parámetros y el hash. Propuestas/calendario no se recargan.

## Validación local

Desde `backend/`, con el entorno virtual activo:

```powershell
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py test
python -m apps.plans.tests.browser_expenses
python -m apps.plans.tests.browser_expenses --debts
```

Desde raíz:

```powershell
git diff --check
git status
```

La suite Django usa SQLite y bloquea la red. Chrome headless usa servicios
simulados y guarda capturas temporales. No se agregan dependencias.

## Integración PostgreSQL pendiente

`supabase/tests/expense_debt_notifications.sql` utiliza BEGIN/ROLLBACK y fixtures
propias. Ejecutar como administrador sólo en un entorno de prueba autorizado
con todas las migraciones. Ejecutar también las pruebas anteriores de grupos,
planes, gastos, invitaciones, recordatorios y confirmaciones en ese entorno.

Para verificar solicitudes simultáneas reales, configurar fuera de `.env`
`PLANB_LOCAL_TEST_DSN` con una conexión administrativa a una base **local de
pruebas preparada** y ejecutar:

```powershell
python supabase/tests/expense_debt_concurrency.py
```

El script sólo permite loopback, fuerza la IP local, rechaza servicios de
conexión indirectos y usa dos transacciones concurrentes. Comprueba un único
pendiente y luego otro único pendiente tras marcar el anterior como leído.
Sus fixtures se confirman para que ambas conexiones puedan verlas y se eliminan
en `finally`; no usar la misma base para otras pruebas simultáneas.
La tarea no ejecuta este script ni pruebas SQL sobre Supabase remoto.

## Resultado local de esta implementación

- Ambos `manage.py check`: sin errores.
- `python manage.py test`: **597 pruebas aprobadas**, incluidas 34 nuevas de
  avisos y las 47 de gastos. Pasan las suites previas de invitaciones,
  confirmaciones, recordatorios, calendario y propuestas.
- Gastos + avisos focalizados: 81 pruebas aprobadas.
- Chrome headless, escenario original: 16 verificaciones en cada ancho de
  320, 390 y 1280 px. Escenario `--debts`: 14 verificaciones en cada ancho.
- Sintaxis JavaScript y script de concurrencia: válida.
- `git diff --check`: sin errores.
- Pruebas SQL/RLS y concurrencia PostgreSQL: entregadas, **sin ejecutar**.

No se hicieron commits, push, SQL remoto ni cambios en `.env`. No se agregaron
dependencias. La migración nueva queda pendiente de revisión y aplicación.
