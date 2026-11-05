# Asistente de planes confirmados · bloque 1

Este documento conserva las decisiones y validación del backend del Prompt 1.
La experiencia visual y la generación desde el detalle agregadas en Prompt 2
se describen en [Sugerencias en el detalle del plan](recomendaciones-ui.md).

## 1. Arquitectura encontrada

Planes y propuestas viven en `public` de Supabase, sin modelos ORM Django.
Los servicios usan Data API, clientes efímeros, JWT del usuario y RLS. Las
decisiones usan RPC. La base directa de Django queda para infraestructura en
`django_internal`. Los tests usan SQLite en memoria y bloquean DNS y sockets
con `OfflineTestRunner`, sin cargar `.env`.

Crear un plan no confirma una actividad. `confirm_plan_proposal`, ejecutada por
el owner, requiere una propuesta del mismo plan con fecha y posición. Guarda
`selected_proposal_id`, `confirmed_at`, copia `date_pick` a `scheduled_at` y
limpia la localidad meteorológica tentativa. Reabrir limpia la decisión.
Finalizar conserva selección y cambia `status` a false; reactivar vuelve a true.
Sólo un plan activo con selección y confirmación puede generar/consultar lugares.

El título real se llama `tittle`. `proposals.posicion` es un punto PostGIS;
`proposal_service` normaliza GeoJSON, EWKT y EWKB a `latitude`/`longitude`.
No existe una dirección textual separada en las propuestas actuales. No se
geocodifica el título ni se usa la localidad tentativa del clima.
Los módulos de clima ya usan HTTPX con timeout, endpoints fijos y excepciones
controladas; se mantiene ese estilo con códigos específicos para recomendaciones.

## 2. Archivos creados

- `backend/apps/plans/services/place_types.py`: `Location`, `PlaceSearch` y errores seguros.
- `backend/apps/plans/services/place_intent_service.py`: resolver temporal de categorías/ubicación.
- `backend/apps/plans/services/geoapify_service.py`: Places y geocoding, sin planes o IA.
- `backend/apps/plans/services/place_ranking_service.py`: ranking, distancia y URL Maps.
- `backend/apps/plans/services/recommendation_store.py`: lectura JWT y RPC backend.
- `backend/apps/plans/services/plan_recommendation_service.py`: orquestación autorizada.
- `backend/apps/plans/recommendation_views.py`: endpoints JSON privados.
- `backend/apps/plans/tests/test_recommendations.py`: suite offline de la feature.
- `supabase/migrations/202611050001_plan_recommendations.sql`: persistencia y seguridad.
- `supabase/tests/plan_recommendations.sql`: integración SQL manual reversible.
- `docs/testing/recomendaciones.md`: este informe.

## 3. Archivos modificados

- `backend/apps/plans/urls.py`: ruta de recomendaciones.
- `backend/config/settings.py`: lectura opcional de `GEOAPIFY_API_KEY`.
- `backend/config/settings_test.py`: clave vacía, independiente del entorno.
- `.env.example`: nombre de variable sin valor y alcance del secreto Supabase.
- `README.md`: referencia a esta guía.

## 4. Migration y permisos

`202611050001_plan_recommendations.sql`, preparada para revisión y aplicación
manual después de las migrations existentes. No se ejecutó SQL remoto.
Incluye tabla, RLS de lectura para miembros actuales, dos RPC exclusivas de
`service_role` y un trigger de invalidación al cambiar/reabrir la decisión.

Decisión a revisar: una RPC abierta a authenticated que reciba snapshots
permitiría a un miembro guardar lugares falsos. Se amplía el uso del cliente
administrativo existente exclusivamente a `claim_plan_recommendations` y
`finish_plan_recommendations`. Ni authenticated ni anon pueden ejecutarlas;
no se permiten escrituras directas a la tabla, incluso a service_role.
La credencial administrativa sigue siendo potente: limitar su uso a estas
RPC es una restricción de aplicación, no una credencial con privilegios mínimos.

Las lecturas usan JWT/RLS y autorización adicional de plan y grupo en servicio.
Se revalida acceso/decisión al terminar la búsqueda. No se aceptan ubicación,
propuesta, categorías o replace desde el navegador. El ticket de generación
no tiene SELECT para authenticated. Las respuestas usan listas explícitas de
campos; no guardan claves ni entregan respuestas crudas del proveedor.

## 5. Persistencia

Una fila por plan en `plan_recommendations`, con snapshot JSONB de máximo cinco
lugares y estado pending/ready/failed. Una fila inexistente es not_generated;
ready con array vacío es no_results, sin error técnico. Un CHECK limita el array
y una sentencia reemplaza el conjunto completo. No hay consultas por lugar para
reconstruir una card.

Cada lugar conserva provider, ID, nombre, categorías, dirección, coordenadas,
distancia en metros, website y horarios disponibles, media nullable y rank.
Los timestamps pertenecen al conjunto porque se genera en una sola operación.
No se agregan ratings, reseñas, precios, fotos ni estado abierto/cerrado.
Media queda null: no se inventa ni se consulta Details para obtener imágenes.
Los horarios son texto del proveedor; no se infiere el estado abierto ahora.

La fila identifica propuesta e instante de confirmación. Reabrir la elimina;
el ticket y la decisión impiden que un resultado tardío sobrescriba otra
generación. Finalizar oculta el snapshot por RLS y servicio; reactivar permite
reutilizarlo. Un recálculo conserva el conjunto anterior durante pending y
también si falla. El éxito reemplaza atómicamente todos los lugares.

## 6. Ranking

Descarta candidatos sin ID, nombre, coordenadas válidas o categorías, categorías
incompatibles y lugares fuera del radio. Orden lexicográfico:

1. Categoría solicitada exacta antes de coincidencia por subcategoría.
2. Distancia Haversine de Plan B, con radio terrestre medio 6.371.008,8 m.
3. Cantidad de datos presentes entre dirección, website y horarios, en empate de distancia.
4. ID estable para desempate reproducible.

Deduplica por ID, conserva hasta cinco y asigna rank 1–5. La distancia es
geodésica, no tiempo ni distancia de viaje. No depende del orden del proveedor
ni inventa un score de calidad.

## 7. Consumo de Geoapify

Una llamada Places por generación, máximo 20 candidatos, radio inicial 5 km,
filtro circular y proximity bias. Sin paginación, expansión automática, Details,
consultas individuales, polling o retries automáticos. Se reutilizan las
coordenadas existentes. `resolve_location` admite un futuro adaptador que
entregue `location_text` autorizado y geocodifica una sola vez si faltan
coordenadas; el esquema actual siempre entrega un punto. El centro se guarda
en base_location.

La reserva atómica serializa generaciones por plan antes de salir a la red.
Los miembros reutilizan pending/ready/failed. GET nunca genera; el detalle no
llama al proveedor. Repetir POST reutiliza el conjunto, incluso si está vacío.
El máximo de 20 sigue el bloque documentado por
[Geoapify Places](https://apidocs.geoapify.com/docs/places/). No se configura
billing ni una cuota global diaria para toda la instalación.

## 8. Errores

El cliente distingue missing_api_key, timeout, http_error (incluido 429) e
invalid_response; geocoding agrega location_not_found. El resolver distingue
unsupported_intent y missing_location. Una propuesta inaccesible deja
proposal_unavailable. No propaga mensajes/URLs del proveedor con credenciales.
Timeout de 6 s y conexión de 2 s; hosts constantes, sin redirects.
Websites sólo HTTP/HTTPS sin credenciales y nunca se solicitan desde backend.

SQL sin aplicar, datos almacenados inválidos o red de Supabase inaccesible
producen 503 controlado con storage_unavailable. Las sesiones vencidas siguen
el manejo existente. Ningún error escribe el estado del plan ni bloquea su
detalle. Si falla la escritura final puede quedar pending; el snapshot previo
se conserva. No se confunden cero lugares con fallos técnicos.

## 9. Gemini y alcance pendiente

Gemini podrá reemplazar `resolve_categories(proposal)` y entregar categorías
para PlaceSearch sin modificar Geoapify, ranking o persistencia. El resolver
actual reconoce comida, café/merienda, parques/picnic y compras. Ante intención
desconocida o ambigua falla sin llamar al proveedor. No es IA.

No hay UI definitiva, mapa, Gemini, worker ni disparador automático. **El próximo
bloque debe conectar el evento de confirmación a la generación fuera de la
operación crítica.** Confirmar sigue igual y no espera a Geoapify en este bloque.

Ruta `/planes/<uuid>/recomendaciones/`:

- GET consulta snapshot/estado, sin generar.
- POST con sesión y CSRF genera una vez si no hay snapshot. Devuelve 200 con estado consultable, incluso si Geoapify falla.
- Miembro ajeno: 404; sesión ausente: login; almacenamiento inaccesible: 503.
- Los headers siguen siendo privados y no-store.

`generate_recommendations(token, plan_id, replace=True)` prepara futuros
recálculos explícitos; el POST actual no expone replace. Un pending reciente
no se puede reclamar durante dos minutos. Un proceso interrumpido requiere
esa llamada interna explícita después del plazo; no hay reintentos ocultos.

Los enlaces externos se derivan de los datos persistidos con `urlencode`:

- `google_maps_url`: `https://www.google.com/maps/search/?api=1&query=...`.
- `google_search_url`: `https://www.google.com/search?q=...`.

Ambos priorizan `nombre, dirección`; sin dirección usan `nombre, latitud,longitud`.
Si falta el nombre, usan coordenadas como fallback. Se recortan espacios de los
extremos y los campos vacíos se consideran ausentes. El encoding preserva tildes,
ñ y caracteres reservados sin introducir parámetros adicionales.
No requieren claves Google ni un Google Place ID, y no garantizan que Google
abra una ficha comercial específica. No hay llamadas HTTP ni scraping de Google.

Ambos campos se agregan al snapshot normalizado al consultar, como ya ocurría
con Maps. El JSONB almacenado no se reescribe: también funcionan los snapshots
anteriores sin regeneración. No se necesita ni se crea una migration.

Validación del ajuste de enlaces externos: se actualizó el caso de Maps sin
dirección y se agregaron cinco tests para prioridad nombre/dirección, campos
ausentes o vacíos, encoding de espacios/tildes/ñ y delimitadores, uso de
nombre/dirección sin coordenadas y compatibilidad con snapshots anteriores sin
reescritura ni llamadas al proveedor. Resultados:

```text
Tests de recomendaciones: Ran 57 tests in 0.191s — OK
python manage.py check: System check identified no issues (0 silenced).
Suite completa: Ran 686 tests in 21.554s — OK
```

No se creó ni modificó ninguna migration en este ajuste. No cambió el ranking,
la persistencia, RLS, RPC ni el consumo de Geoapify. No se hicieron solicitudes
HTTP a Google, scraping, cambios en `.env`, SQL remoto, commit ni push.

## 10. Tests agregados

Proveedor simulado: parámetros, timeouts, HTTP/redirect/cuota, JSON/envelope
inválido, clave ausente, cero resultados, opcionales y websites seguros.
Contratos: coordenadas, geocoding condicional, intención conservadora y límites.
Ranking: distancia, compatibilidad, completitud, orden estable, deduplicación,
máximo cinco y Maps. Servicios: autorización, sólo confirmados activos,
persistencia, datos compartidos, no repetir llamadas, pending/failed,
reapertura durante búsqueda y omisión de campos internos. SDK real sobre
MockTransport, incluido flujo completo con dos miembros; endpoints/CSRF/sesión.

Los mocks no prueban ejecución real de RLS, locks o PL/pgSQL. La prueba SQL
manual cubre miembros/ajenos/ex miembros, permisos de RPC/escritura/ticket,
límite de cinco, reservas duplicadas, tickets obsoletos, conservación al fallar,
finalización/reactivación e invalidación. No se ejecutó contra Supabase remoto.

## 11–12. Resultado de validación

Baseline antes del cambio: `Ran 629 tests in 22.675s`, `OK`.
Se agregaron **52 tests**, sin modificar los 629 existentes.

Resultado exacto de `python manage.py check` (settings normales de ejecución):

```text
System check identified no issues (0 silenced).
```

Resultado exacto de la suite completa `python manage.py test`:

```text
Ran 681 tests in 17.607s

OK
```

La salida también confirmó `Found 681 test(s).` y
`System check identified no issues (0 silenced).` No hubo conexiones reales a
Geoapify: los tests se ejecutaron con bloqueo de red y DNS. `git diff --check`
terminó sin reportar problemas. La integración SQL queda pendiente de ejecución
manual en una instancia autorizada; no se validó PL/pgSQL ni RLS contra una base
real en esta sesión.

## 13. Pasos manuales

1. Revisar la ampliación del uso de SUPABASE_SECRET_KEY a las dos RPC backend.
2. Revisar/aplicar manualmente la migration primero en un Supabase local/de pruebas autorizado.
3. Ejecutar allí `supabase/tests/plan_recommendations.sql` como administrador:
   sus fixtures terminan en ROLLBACK. Validar además dos POST simultáneos desde
   miembros diferentes: una sola consulta Places y un snapshot.
4. Reiniciar Django para cargar configuración. GEOAPIFY_API_KEY ya existe en
   el `.env` local; no se modifica ni se imprime su valor.
5. Confirmar un plan con actividad reconocida y coordenadas; GET debe devolver
   not_generated. Hacer POST autenticado con CSRF desde una herramienta local
   o consola del navegador usando el token de un formulario existente. Consultar
   desde otro miembro y comprobar reutilización sin llamadas en recargas.
6. Validar sin clave/proveedor inaccesible en entorno controlado: el plan debe
   seguir confirmado y operativo, sin imprimir credenciales.

Desde backend con virtualenv activado: `python manage.py check` y
`python manage.py test`. La suite no depende de Internet.

## 14. Decisiones y limitaciones antes de commit

Revisar: uso administrativo acotado a RPC; disparo automático y reintento UI
pendientes; radio 5 km; resolver conservador; sin media ni mapa definitivo;
SQL preparado pero pendiente de ejecución real. No se encontró PostgreSQL local
disponible para validar PL/pgSQL en esta sesión. La cuota global queda a cargo
de la configuración del proveedor. No hubo commit, push, SQL remoto ni cambios
en `.env`. No se leyó su contenido desde herramientas ni se revelaron secretos.
