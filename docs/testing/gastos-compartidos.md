# Prueba de gastos compartidos

## Preparación

Requiere todas las migraciones previas y la nueva
`supabase/migrations/202610300002_shared_expenses.sql` aplicadas por quien
administra el entorno autorizado. La implementación no ejecuta SQL remoto.
Sin la migración, el detalle sigue disponible y el popup informa un error de
consulta con opción de reintentar.

Usar un grupo con Facundo, Juan y Martina. Uno debe ser owner, y los otros dos
miembros aceptados (no invitaciones pendientes). Crear el plan «Cena del viernes».

## Escenario principal

1. Ingresar como Facundo y abrir el detalle de «Cena del viernes».
2. Pulsar la tarjeta «Gastos compartidos». Ver el popup y el mensaje
   «Aún no hay gastos compartidos en este plan.».
3. Pulsar «+ Agregar primer gasto». Completar concepto `Cena`, monto `30.000`,
   pagador `Juan` y seleccionar `TODO EL GRUPO`.
4. Guardar. El popup sigue abierto y muestra total $30.000,00; Juan pagó
   $30.000,00; Facundo y Martina pagaron $0,00.
5. Comprobar balances: Juan +$20.000,00; Facundo y Martina −$10.000,00 cada uno.
   «Para saldar» muestra una transferencia de $10.000,00 desde cada deudor a Juan.
6. Desplegar «Ver reparto»: tres partes de $10.000,00.
7. Agregar `Bebidas`, monto `6.000`, pagador `Facundo`, TODO EL GRUPO.
   Comprobar total $36.000,00, Juan +$18.000,00, Facundo −$6.000,00,
   Martina −$12.000,00. Transferencias: Facundo $6.000,00 y Martina $12.000,00 a Juan.
8. Cerrar con X. Confirmar que se conserva el detalle, scroll y estado de
   propuestas/clima; la tarjeta muestra el total y dos gastos registrados.
9. Volver a abrir y cerrar con Escape. Probar también Cancelar en el formulario.

## Variantes

- En otro plan, cargar $100 entre tres personas. «Ver reparto» debe mostrar
  33,34 / 33,33 / 33,33; el centavo adicional corresponde al menor UUID.
- Cargar $20.000 pagados por Juan y repartir sólo entre Facundo y Martina.
  Juan debe recuperar todo el monto; no se agrega automáticamente al reparto.
- Probar concepto vacío, monto cero, más de dos decimales, demasiado grande o
  sin participantes. Conservar el formulario y mostrar errores en el popup.
- Confirmar que el owner también carga gastos y que funcionan planes activos,
  inactivos, confirmados y finalizados sin nuevas restricciones.
- Agregar un integrante después de un gasto con TODO EL GRUPO. No debe aparecer
  en ese gasto ni asumir partes históricas. Un gasto nuevo de grupo completo sí
  lo incluye.
- Si el entorno permite una baja administrativa de prueba, verificar que el
  exintegrante sigue en los saldos históricos pero no puede abrir plan ni gastos.
  Esta tarea no agrega una interfaz para dar de baja miembros.
- Con un usuario ajeno, abrir el plan y su endpoint `/gastos/`: ambos deben
  rechazar acceso. Rechazar asimismo pagador/participantes ajenos manipulando POST.
- Cambiar tema light/dark y probar móvil a 320/390 px. El modal no debe desbordar;
  la tabla puede tener scroll horizontal interno.
- Cortar la conexión al guardar: pedir actualizar el listado antes de reenviar.
  Si ya se guardó pero falla la actualización posterior, indicar éxito de guardado
  y permitir reconsultar sin duplicar.

## Comandos locales

Desde `backend/`, con el Python del entorno virtual activo:

```powershell
python manage.py check
python manage.py check --settings=config.settings_test
python manage.py test
python -m apps.plans.tests.browser_expenses
```

Desde la raíz:

```powershell
git diff --check
git status
```

La suite habitual no consulta servicios remotos. El arnés opcional utiliza
Chrome headless instalado y deja capturas en una carpeta temporal que imprime
al terminar. No instala dependencias. La prueba SQL reversible queda en
`supabase/tests/shared_expenses.sql`; ejecutarla sólo en una base local/de pruebas
autorizada, como administrador, con las migraciones previas. No se ejecutó en
esta tarea: no había un servidor PostgreSQL local disponible y Docker no estaba
iniciado. Quedan pendientes su ejecución y las pruebas de concurrencia reales.

## Resultado de validación local (30/10/2026)

- `python manage.py check`: sin errores.
- `python manage.py check --settings=config.settings_test`: sin errores.
- `python manage.py test`: 563 pruebas aprobadas, incluidas 47 nuevas de gastos.
  Incluye las suites existentes de propuestas, calendario y notificaciones.
- Chrome headless: 16 verificaciones aprobadas en cada ancho (320, 390 y 1280 px).
- `node --check .../plan-expenses.js`: sintaxis válida.
- `git diff --check`: sin errores.
- Prueba SQL/RLS y concurrencia PostgreSQL: preparadas, sin ejecutar.

No se agregaron dependencias, ni se hicieron commits, push, SQL remoto o cambios
en `.env`. La nueva migración se entrega como archivo pendiente de aplicación.
