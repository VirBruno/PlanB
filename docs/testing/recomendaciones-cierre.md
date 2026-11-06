# Cierre del asistente de planes confirmados · Prompt 4

## 1. Baseline previo

Antes del trabajo del Prompt 4: **737 tests OK**, 34.404 s. Tras preparar el
claim autorizado, y antes de continuar con el resto: **751 tests OK**, 24.423 s.
Ambos baselines usaron `manage.py test --settings=config.settings_test --noinput`,
sin cargar credenciales ni permitir conexiones externas.

## 2. Arquitectura final

Se conservan los servicios y la persistencia de los bloques anteriores:

```text
GET estado → autorizar → snapshot → comparar fingerprint actual → flags públicos
                      └─ legacy ready → reutilizar sin invalidar

detalle sin snapshot / con can_update → un POST automático por documento
→ autorizar → inputs actuales → claim atómico por fingerprint
→ una interpretación Gemini (intenciones + summary)
→ validación → mapping existente → una búsqueda Geoapify
→ ranking existente / Top 5 → finish existente → snapshot compartido
```

GET no consulta Gemini ni Places. Comparar un snapshot con fingerprint requiere
leer la propuesta elegida mediante Data API. Si esa lectura falla, se conserva
el snapshot consultable sin intentar clasificar datos desconocidos.

## 3. Archivos creados

- `backend/apps/plans/services/recommendation_inputs.py`: payload mínimo y fingerprint.
- `backend/apps/plans/tests/test_recommendation_claim.py`: contrato SDK/RPC y concurrencia simulada.
- `backend/apps/plans/tests/test_recommendation_updates.py`: normalización e integración del recálculo.
- `supabase/migrations/202611060001_recommendation_fingerprint_claim.sql`: nueva migration autorizada.
- `supabase/tests/recommendation_fingerprint_claim.sql`: prueba SQL manual reversible.
- `docs/testing/recomendaciones-cierre.md`: este informe y pasos manuales.

## 4. Archivos modificados

- Servicios `gemini_intent_service.py`, `place_intent_service.py`,
  `plan_recommendation_service.py` y `recommendation_store.py`.
- `backend/apps/plans/recommendation_views.py`.
- `backend/apps/plans/templates/plans/partials/recommendations.html`.
- `backend/apps/users/static/users/js/plan-recommendations.js` y CSS del mismo componente.
- Tests `test_gemini_intent.py`, `test_recommendations.py`, `test_recommendations_ui.py`.
- `backend/apps/plans/tests/browser_recommendations.py`.
- `README.md` y referencia de continuidad en `recomendaciones-gemini.md`.

No se agregan dependencias ni se modifican modelo, settings, `.env`, catálogo,
Geoapify o ranking. La regla que impide editar propuestas confirmadas permanece.

## 5. Schema final de Gemini

```json
{
  "type": "object",
  "properties": {
    "place_intents": {
      "type": "array",
      "minItems": 1,
      "maxItems": 3,
      "items": {
        "type": "string",
        "enum": ["restaurant", "cafe", "bar", "nightclub", "park", "supermarket", "cinema", "bakery", "fast_food", "shopping", "unsupported"]
      }
    },
    "summary": {"type": ["string", "null"], "maxLength": 320}
  },
  "required": ["place_intents"],
  "additionalProperties": false
}
```

El backend conserva la validación de enum, cantidad, duplicados y exclusividad
de unsupported. Summary es opcional incluso si se espera que Gemini lo produzca:
su ausencia o su tipo inválido no invalida unas intenciones correctas.

## 6. Summary en la misma llamada

La instrucción de `GeminiIntentClient` pide una o dos frases en español para
el conjunto completo. Describe tipos de lugares buscados cerca del punto acordado,
sin afirmar que ya se encontraron locales ni atribuirles características.
La respuesta incluye intenciones y summary en el mismo JSON; no hay segundo request.
Se sigue usando `settings.GEMINI_MODEL`, con el timeout de lectura de 45 s y
conexión de 2 s establecidos antes del Prompt 4.

## 7. Validación y sanitización

El summary se normaliza a Unicode NFC y espacios simples. Se omite si está
vacío, supera 320 caracteres, tiene un tipo incorrecto, caracteres de control
inesperados, marcas HTML/Markdown, URLs, emails, dígitos, categorías técnicas,
más de dos frases o términos de ratings, reseñas, precios, popularidad,
disponibilidad, calidad, secretos, instrucciones o razonamiento interno.
El prompt mantiene el aislamiento del título/descripción como datos no confiables
y prohíbe copiar instrucciones, pedir secretos o mostrar chain-of-thought.

Es un filtro conservador de presentación, no un verificador semántico general.
Puede omitir un texto inocuo; en ese caso los lugares se muestran sin explicación.
La UI inserta el texto con `textContent`, nunca como HTML. El summary no participa
en categorías, ubicación, fingerprint, búsqueda ni ranking.

## 8. Persistencia

Sólo se agrega `summary` válido a `criteria`, junto a los metadatos seguros ya
existentes y al `input_fingerprint` del snapshot generado. No se guarda la
propuesta original, prompt, raw response, firma del proveedor, chain-of-thought,
claves ni sesiones. El claim agrega `attempted_input_fingerprint` sin reemplazar
el fingerprint ni el summary del último snapshot válido.

## 9. Contrato público seguro

El endpoint publica `ai_summary` sanitizado y flags explícitos: `has_snapshot`,
`is_stale`, `can_update` y `can_refresh`. No publica `criteria`, modelo,
fingerprints o intenciones de clasificación. Se conserva el contrato de cada
item Geoapify, incluidas sus categorías existentes usadas por las cards;
no se agrega la lista interna de categorías de búsqueda a la respuesta.

## 10. Ubicación visual

Un párrafo secundario debajo del encabezado/introducción y antes del feedback,
cards y mapa. Se oculta por completo cuando no hay texto válido. Usa los colores
existentes light/dark, ancho acotado, corte de palabras y tipografía del componente.
No hay nueva card grande, modal ni cambios en la interacción card/marker.

## 11. Inputs del fingerprint

Sólo la versión del contrato de inputs, ID de propuesta seleccionada, título y
descripción realmente enviados a Gemini, y coordenadas normalizadas. El adaptador
de ubicación textual se contempla únicamente si faltan coordenadas válidas.
No se incluyen nombre/descripción del plan, fecha, tipo, presupuesto, gastos,
votos, usuarios, integrantes, avatares, notificaciones, modelo ni summary.

## 12. Algoritmo y normalización

`activity_payload` se comparte con el cliente Gemini: NFC, espacios normalizados,
título hasta 255 caracteres y descripción hasta 4000, con null equivalente a vacío.
Cambiar texto fuera de esas cotas no invalida porque no participa en la clasificación.
Las coordenadas válidas se convierten a float; 1/1.0 y -0.0/0.0 son equivalentes.
No se redondea ni se elimina precisión de las coordenadas.

Se serializa con JSON de claves ordenadas, separadores estables y Unicode,
y se calcula SHA-256 hexadecimal. No se usan timestamps o IDs aleatorios como
sustitutos del contenido. El hash se construye sólo en backend y no se envía al navegador.

## 13. Legacy y actualización explícita

Un ready sin fingerprint sigue siendo válido durante apertura/refresh, sin
generación automática. Ofrece **Actualizar sugerencias** como acción explícita.
La firma nueva del claim distingue:

| Snapshot | `p_replace` | Resultado |
| --- | --- | --- |
| ready legacy sin fingerprint | false | No reserva |
| ready legacy sin fingerprint | true | Puede reservar e incorporar fingerprint |
| fingerprint válido ya coincidente | false o true | No reserva |
| input nuevo, sin intento de ese input | false | Puede reservar |
| mismo intento ya fallido | false | No reserva |
| mismo intento fallido | true | Puede reintentar |
| pending reciente | cualquiera | No reserva |

El bloqueo de pending reciente conserva la ventana de dos minutos existente.
Un pending vencido para el mismo fingerprint requiere reintento explícito.

## 14. Detección de stale

Un fingerprint de snapshot distinto al de los inputs actuales indica stale.
`can_update` sólo habilita un intento automático si no está pending y el input
actual todavía no fue intentado. También permite un nuevo input después de
un fallo inicial o de actualización. Un fallo no produce reintentos al recargar.

## 15. Disparo del recálculo

El JS reutiliza GET → POST inicial, ampliado para `can_update=true`.
Primero renderiza la última versión y luego hace un único POST automático por
documento, compartido por componentes duplicados. El backend vuelve a comprobar
los inputs y reclama el trabajo atómicamente. Reintentar usa el endpoint existente,
que también permite la actualización explícita de ready legacy o stale.
No se cambia `generated_at` al reutilizar el snapshot vigente.

## 16. Concurrencia y migration final

La migration nueva afecta solamente `claim_plan_recommendations`:

- Agrega una firma de cinco argumentos sin defaults, incluido el fingerprint.
- Bajo el mismo orden de locks plan → snapshot, compara el fingerprint válido y
  el del último intento; sólo entonces entrega ticket y registra el nuevo intento.
- La firma anterior conserva defaults/ACL y delega a la nueva con fingerprint null,
  manteniendo el contrato para consumidores previos.
- La firma nueva requiere su propio permiso EXECUTE, limitado a `service_role`
  igual que el anterior. No modifica permisos de tabla ni RLS.
- Una solicitud demorada no vuelve a generar después del éxito o fallo de su
  competidor para ese mismo fingerprint. La recuperación explícita de pending
  vencidos conserva la política de lease/tickets existente.

`finish_plan_recommendations` no cambia. En éxito reemplaza el conjunto y los
criterios atómicamente; en fallo conserva el snapshot y el marcador de intento.
La nueva orquestación usa la firma con fingerprint; no hace fallback a la firma
legacy si falta la migration, porque eludiría esta protección.

## 17. Durante la actualización

Cards, mapa y summary anterior permanecen visibles. El estado accesible dice
que se están actualizando y permite consultar la última versión mientras tanto.
Se bloquea el doble envío. Si otro miembro tiene el claim, se muestra pending;
la consulta de estado continúa siendo manual, sin polling continuo.

## 18. Actualización fallida

Se muestran los datos previos y el aviso: «No pudimos actualizar las sugerencias.
Estás viendo la última versión disponible». La acción pasa a **Reintentar
actualización**. También se conserva lo visible ante un error HTTP del endpoint.
Una nueva generación correcta reemplaza lugares, summary, criterios y fecha.

## 19. Sin snapshot anterior

Se mantiene failed con error controlado y **Reintentar**, sin cards/mapa ni summary
inventados. `has_snapshot` distingue además un éxito previo sin lugares mediante
`generated_at`, por lo que un resultado vacío válido no se confunde con ausencia
de generación. Los errores externos no modifican la confirmación.

## 20. Cambios de UI y accesibilidad

Sólo se agregan el párrafo de summary, textos de actualización/última versión y
la acción explícita legacy. Se conserva la región `role=status`/`aria-live`, sin
depender de color. Cards, mapa, enlaces Google, navegación por teclado y layout
existentes continúan. El timeout del fetch pasa a 90 s para no abortar a los
30 s una operación cuyo Gemini puede tardar 45 s más Places/persistencia;
esto no agrega solicitudes ni retries.

Chrome headless usa Leaflet real con tiles/servicios simulados y DNS externo
bloqueado. Se verificaron 320, 390 y 820 px, 1366×768, 1440×900 y 1920×1080:
107 checks por tamaño móvil/tablet y 111 por tamaño desktop, con capturas light/dark.
Incluye summary visible/ausente, inserción como texto, actualización con datos
previos, warning/reintento, legacy explícito, un GET+POST, ausencia de polling,
teclado, interacción card/mapa y ausencia de scroll horizontal.

## 21. Consumo

Máximo una clasificación Gemini y una búsqueda Places por generación efectiva.
No hay Details, paginación, segunda IA, grounding o retries automáticos.
El claim compartido evita consumo por miembro. El summary se genera antes de
Places y no recibe datos de establecimientos. No se hicieron llamadas reales
a Gemini/Geoapify ni se habilitó billing.

## 22. Tests agregados/modificados

- Summary: válido, ausente, vacío, tipo incorrecto, excesivo, inseguro, razonamiento,
  datos no sustentados, aislamiento de injection, persistencia y respuesta pública.
- Inputs: determinismo, orden de claves, Unicode/espacios, coordenadas equivalentes,
  cambios de título/descripción/ubicación/ID y cambios irrelevantes o fuera de cotas.
- Flujo: cache vigente, stale sólo en GET sin generar, éxito de recálculo,
  fallo Gemini sin Places, fallo Places, conservación del snapshot/summary,
  fallo inicial, nuevo input tras fallo, reintento exitoso sin reconfirmar y legacy.
- Concurrencia: consumidores reales del orquestador y adaptador SDK sobre
  `MockTransport`, contra una RPC simulada atómica. Incluye pending y competidores
  demorados que llegan después del éxito o timeout.
- SQL manual: permisos de la nueva firma, tickets, normal/explicit legacy,
  preservación de campos, intento registrado, fallo, hash nuevo y restricción de
  edición confirmada. Agrega indicaciones para dos sesiones locales.

La suite sigue bloqueando DNS/sockets con `OfflineTestRunner`. La simulación RPC
no ejecuta ni prueba el motor PostgreSQL; se entrega la prueba SQL para validarlo
en un entorno autorizado antes del despliegue.

## 23. Tests específicos finales

```text
manage.py test apps.plans.tests.test_recommendation_updates apps.plans.tests.test_recommendation_claim apps.plans.tests.test_gemini_intent apps.plans.tests.test_recommendations apps.plans.tests.test_recommendations_ui --settings=config.settings_test --noinput
Ran 150 tests in 1.424s
OK
```

## 24. Check Django

`manage.py check` con configuración local: `System check identified no issues (0 silenced).`

## 25. Suite completa final

```text
manage.py test --settings=config.settings_test --noinput
Ran 779 tests in 32.855s
OK
System check identified no issues (0 silenced).
```

Son 28 tests más que el baseline de continuación (751), y 42 más que el
baseline previo al ajuste del claim (737). Log temporal:
`%TEMP%\planb-prompt4-final-tests.log`.

## 26. Diff check

`git diff --check`: sin errores. El estado final incluye 14 archivos modificados
y seis nuevos. `.env`, settings/modelo, la migration anterior y la vista de
edición de planes/propuestas no tienen diferencias.

Las capturas/reportes del navegador final están en
`%TEMP%\planb-recommendations-browser-3e0htwra`:
`suggestions-{ancho}-{light|dark}.png` y `report-{ancho}.json`.

## 27. Migration autorizada

Se creó **una migration nueva**, autorizada durante esta conversación:
`supabase/migrations/202611060001_recommendation_fingerprint_claim.sql`.
No se modificaron migrations ya aplicadas, columnas, RLS, finish ni la restricción
de edición. No se ejecutó SQL remoto ni se aplicó la migration en esta tarea.
Revisar junto a `supabase/tests/recommendation_fingerprint_claim.sql` y la prueba
legacy `supabase/tests/plan_recommendations.sql`, que se conserva.

## 28. Entorno y repositorio

`.env`, modelo default/override y credenciales no fueron modificados ni revelados.
No se hizo commit ni push. Las capturas y logs son artefactos temporales locales.

## 29. Pasos manuales preparados

1. Revisar la migration y la prueba SQL entregadas. La nueva firma debe estar
   aplicada manualmente en el entorno autorizado **antes** de desplegar este
   backend: sin ella el endpoint devuelve el error controlado de almacenamiento.
   No se aplica SQL como parte de este trabajo.
2. Reiniciar Django con la configuración existente de Gemini y Geoapify, sin
   cambiar `.env`. Crear y confirmar un plan nuevo con «Queremos salir a tomar
   algo y después bailar», con un punto válido en una zona con oferta.
3. Abrir el detalle: esperar hasta cinco lugares reales y una explicación general
   breve coherente. Verificar que no hay textos por establecimiento ni afirmaciones
   de rating/precio/popularidad. Si falta summary, las cards deben seguir funcionando.
4. Recargar y abrir con otro miembro: reutilizar lugares/fecha. Seleccionar cards,
   mover el mapa y abrir Google no debe generar recomendaciones nuevas.
5. Abrir un ready legacy: no debe generarse automáticamente. Pulsar **Actualizar
   sugerencias** para incorporar fingerprint y summary. Verificar internamente
   los criterios mediante una herramienta autorizada, sin exponerlos en el navegador.
6. Casos B/C (contenido/ubicación): la aplicación y la base actuales **impiden
   editar propuestas confirmadas**. No se desbloqueó esa operación. Para probar
   estos escenarios sin alterar reglas ni datos reales, ejecutar los fixtures de
   `test_recommendation_updates` y el harness de navegador. Reabrir, editar y
   reconfirmar usa la invalidación de decisión existente, no el camino stale.
7. Caso D: los tests/harness simulan timeout/error durante actualización y verifican
   cards, mapa, summary anterior, aviso y reintento exitoso. Para una prueba local
   adicional, inyectar el fallo sólo en el proceso/fixture de prueba; no provocar
   rate limits, modificar `.env` real ni ejecutar escrituras SQL remotas.
8. Ejecutar el navegador con `python -m apps.plans.tests.browser_recommendations`
   desde `backend` para repetir las verificaciones y capturas sin proveedores reales.

## 30. Limitaciones restantes

- SQL y concurrencia PostgreSQL real quedan pendientes de revisión/ejecución local
  o de pruebas autorizada. Los tests offline simulan el contrato, no el motor.
- No se habilita edición de confirmados. La detección stale cubre el cambio de
  inputs cuando exista, sin añadir un nuevo flujo de edición.
- Un snapshot legacy no puede detectar cambios históricos sin un hash anterior;
  conserva compatibilidad hasta su actualización explícita/generación real.
- El filtro de summary puede omitir textos válidos y no reemplaza la evaluación
  semántica manual del modelo configurado. No se ejecutó una nueva prueba real de IA.
- Se mantiene la política de recuperación explícita de pending vencidos y tickets
  existente. No hay jobs de fondo, polling continuo ni bloqueo paralelo.
