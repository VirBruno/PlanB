# Interpretación del plan confirmado · Prompt 3

Este informe registra el bloque original. El summary, el fingerprint y la
actualización de snapshots posteriores se documentan en el
[cierre del Prompt 4](recomendaciones-cierre.md).

## 1. Arquitectura elegida

Se inspeccionó la rama `feature/ai-place-recommendations` antes de editar. Ya
incluye snapshot compartido, generación desde el detalle, reintento explícito,
ranking, mapa y cards. Se reutilizan esos contratos:

```text
POST de generación → autorización → snapshot existente / claim RPC
→ propuesta ganadora → GeminiIntentClient (HTTPX REST)
→ validación backend → catálogo semántico → categorías controladas
→ ubicación existente → Geoapify Places (una consulta)
→ ranking existente → Top 5 → finish RPC → UI existente
```

La clasificación ocurre después de obtener el ticket de generación y antes de
geocodificar o buscar lugares. Los GET sólo leen. Las llamadas externas siguen
fuera de las transacciones de base. Geoapify no conoce Gemini.

## 2. Archivos creados

- `backend/apps/plans/services/gemini_intent_service.py`.
- `backend/apps/plans/tests/test_gemini_intent.py`.
- `docs/testing/recomendaciones-gemini.md` (este informe).

## 3. Archivos modificados

- `backend/apps/plans/services/place_intent_service.py`: catálogo y validación;
  conserva la resolución de ubicación.
- `backend/apps/plans/services/plan_recommendation_service.py`: clasificación y
  criterios derivados dentro de la orquestación existente.
- `backend/apps/plans/tests/test_recommendations.py`: fixtures de Gemini,
  regresión del flujo completo y pruebas de fallos, consumo y persistencia.
- `backend/config/settings_base.py`: modelo por defecto.
- `backend/config/settings.py`: variables opcionales de Gemini.
- `backend/config/settings_test.py`: clave vacía, sin cargar `.env`.
- `.env.example`: nombres de variables y comportamiento del override.
- `README.md`: configuración y vínculo a este informe.

## 4. Dependencias

No se agregan dependencias. Se usa `httpx==0.28.1`, ya instalado y declarado en
`requirements.txt`, igual que en los otros proveedores del proyecto.

## 5. Modelo y contrato API

Default: `gemini-3.8-flash`. `GEMINI_MODEL` permite cambiarlo desde configuración
del servidor; ausente o vacío conserva el default. Sólo se aceptan identificadores
`gemini-…`, sin barras, parámetros ni URLs. El override debe soportar structured
output y `thinkingLevel: low`. Un modelo inaccesible/incompatible produce el
error controlado correspondiente; no se sustituye automáticamente.

Endpoint fijo: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`.
Autenticación en header `x-goog-api-key`, nunca en URL ni en el contenido.
Una candidata, hasta 2048 tokens de salida incluyendo razonamiento, nivel `low`,
timeout HTTPX de lectura de 45 s (ajustado tras el timeout de la primera prueba
real), conexión de 2 s y escritura/pool de 15 s, redirects deshabilitados. Sin SDK que
introduzca retries, herramientas, grounding, llamadas de funciones ni búsquedas.

Se verificó el contrato público de
[generateContent](https://ai.google.dev/api/generate-content),
[JSON Schema](https://ai.google.dev/gemini-api/docs/structured-output) y el soporte
de structured output/nivel low del
[modelo](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash).
No se consultó la API autenticada ni se comprobó la disponibilidad de la cuenta.

## 6. Schema de structured output

Se envía `responseMimeType: application/json` y `responseJsonSchema`:

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
        "enum": [
          "restaurant", "cafe", "bar", "nightclub", "park", "supermarket",
          "cinema", "bakery", "fast_food", "shopping", "unsupported"
        ]
      }
    }
  },
  "required": ["place_intents"],
  "additionalProperties": false
}
```

El backend comprueba nuevamente objeto exacto, lista de 1–3 strings del enum,
sin duplicados. `unsupported` sólo es válido como único elemento y produce
`unsupported_intent`. No se aceptan explicaciones, nombres, confianza ni campos
adicionales. También se valida el envelope: una candidata terminada con `STOP`,
contenido del modelo y una parte textual. La firma opaca opcional
`thoughtSignature` se ignora; no se guarda ni se utiliza como clasificación.
No se reparan JSON truncados, texto libre ni bloques Markdown.

## 7. Catálogo permitido

Diez tipos concretos de lugar para actividades sociales, más `unsupported`.
Se excluyen categorías amplias como entretenimiento/deporte genérico porque
habilitarían búsquedas poco específicas. Las intenciones son valores de Plan B;
Gemini nunca recibe autoridad para definir categorías del proveedor.

## 8. Mapping a Geoapify

| Intención Plan B | Categorías Geoapify controladas |
| --- | --- |
| `restaurant` | `catering.restaurant` |
| `cafe` | `catering.cafe` |
| `bar` | `catering.bar`, `catering.pub` |
| `nightclub` | `adult.nightclub` |
| `park` | `leisure.park`, `leisure.picnic` |
| `supermarket` | `commercial.supermarket` |
| `cinema` | `entertainment.cinema` |
| `bakery` | `commercial.food_and_drink.bakery` |
| `fast_food` | `catering.fast_food` |
| `shopping` | `commercial.shopping_mall` |
| `unsupported` | Sin consulta |

Se contrastó el mapping con el
[catálogo oficial de Places](https://apidocs.geoapify.com/docs/places/#categories).
`adult.nightclub` es la categoría técnica oficial para discotecas; se usa esa
hoja concreta, nunca el grupo `adult` completo. Se deduplican las categorías y
se combinan con comas en el `GeoapifyClient.search` existente. Todas las
combinaciones de hasta tres intenciones caben en el máximo actual de cinco
categorías de `PlaceSearch`; no se modifican ese contrato ni el ranking.

## 9. Datos exactos enviados a Gemini

Sólo se construye este objeto de contenido de la propuesta seleccionada:

```json
{"title": "tittle de la propuesta (hasta 255 caracteres)", "description": "descripción (hasta 4000 caracteres)"}
```

La descripción ausente se envía como string vacío. La cota se aplica antes de
serializar a JSON. No se envía el título del plan: la propuesta ya contiene la
actividad y evita añadir contexto genérico o privado innecesario. No se serializa
la propuesta completa: quedan fuera IDs, miembros, perfiles, emails, JWT,
presupuestos, gastos, fotografías, notificaciones, fechas y ubicación separada.
Los nombres de localidades que el autor escribió dentro de su texto se mantienen
como parte de ese texto; las coordenadas sólo se usan en Geoapify.
Además del objeto se envían la instrucción fija, schema y parámetros técnicos.

## 10. Prompt injection

La instrucción fija va en `systemInstruction`; el JSON de la propuesta va en
`contents` con rol `user`, escapado por `json.dumps`. Se explicita que ambos
campos son datos no confiables, que deben ignorarse instrucciones incrustadas y
que nunca deben pedirse secretos, cambiar reglas o alterar el contrato.
No se incluyen secretos del servidor en el contenido, no hay herramientas y el
backend rechaza cualquier salida fuera del enum/schema, aunque el modelo la
devuelva. No se registra el prompt ni el raw response. El servicio protege sus
variables con `sensitive_variables`, siguiendo la convención existente.

## 11. Manejo de errores

| Situación | Código existente |
| --- | --- |
| `GEMINI_API_KEY` ausente | `missing_api_key` sin request |
| Timeout | `timeout` |
| HTTP, conexión, 429 o redirect | `http_error` |
| Envelope/JSON/schema inválido, vacío o truncado | `invalid_response` |
| Tipo desconocido o categoría técnica devuelta por Gemini | `invalid_response` |
| Clasificación `unsupported` | `unsupported_intent` |

Los errores HTTP no exponen el cuerpo, header de autenticación, URL con secretos
ni el mensaje upstream. No hay retries automáticos ni fallback por keywords.
Los errores de propuesta/Geoapify/persistencia conservan sus contratos previos.

## 12. Cuando Gemini no está disponible

Se termina la generación con estado `failed` y código seguro. No se invoca
Geoapify, no se altera la confirmación y el plan sigue consultable. La UI
existente muestra el fallo y permite el reintento explícito.
`finish_plan_recommendations` ya conserva items, ubicación, criterios y fecha de
generación previos si falla otro intento; se reutiliza sin cambios. El test
offline verifica la invocación y simula ese contrato; la prueba SQL existente en
`supabase/tests/plan_recommendations.sql` cubre la conservación de items.
No se ejecutó esa prueba contra una base remota.

## 13. Control de consumo

Un claim ganado permite una clasificación y una consulta Places. No hay llamadas
por lugar, usuario, card, movimiento del mapa ni GET de estado. Ready, failed y
pending se reutilizan al recargar; sólo el reintento explícito existente puede
reservar otra generación fallida/abandonada, respetando el lock RPC. Los snapshots
ready anteriores a este cambio se conservan sin volver a clasificar.

Se acotan entrada, salida y tiempo; no hay paginación ni nuevas consultas por
intención. No se habilitó billing ni se consumieron Gemini/Geoapify reales.
El objetivo $0 depende de la cuota y disponibilidad que tenga el proyecto del
usuario; el código no activa planes pagos ni garantiza una cuota gratuita.

## 14. Resolver temporal

Se eliminaron las reglas por palabras y `resolve_categories`. El módulo
`place_intent_service` ahora contiene catálogo, mapping, contrato validado y
resolución de ubicación. No existe fallback silencioso, con o sin credencial:
la falta de clave usa el mismo error controlado que una indisponibilidad.

## 15. Criterios persistidos

Se extiende el JSON `criteria` existente, por ejemplo:

```json
{
  "interpreter": "gemini",
  "model": "gemini-3.8-flash",
  "place_intents": ["bar", "nightclub"],
  "categories": ["catering.bar", "catering.pub", "adult.nightclub"],
  "radius_meters": 5000,
  "candidate_limit": 20
}
```

Sólo contiene valores controlados/derivados, sin prompt, texto original, raw
response, firma del proveedor, claves ni sesiones. Los criterios siguen siendo
internos y no forman parte del JSON público de la UI. Los nombres, direcciones,
coordenadas y demás datos de establecimientos siguen saliendo de Geoapify.

## 16. Pruebas agregadas y modificadas

`test_gemini_intent.py` prueba el contrato REST, schema y revalidación, errores,
redirecciones/cuota, modelo configurable y límites de entrada. Verifica payload
mínimo sin datos de usuarios, instrucciones incrustadas aisladas y salida
restringida, firma opcional ignorada, casos ambiguos, múltiples intenciones,
mapping exacto y todas las combinaciones compatibles con `PlaceSearch`.

Incluye fixtures de todas las frases funcionales solicitadas: comida coloquial,
merienda, aire libre, birra, bailar/joda, alimentos para casa, cine, ambigüedad e
injection. Sus respuestas Gemini son simuladas: verifican integración y
validación, no la comprensión empírica del modelo remoto.

`test_recommendations.py` reemplaza las pruebas del resolver por el contrato
semántico; agrega fallos Gemini sin Places ni cambios al plan, retry explícito,
reutilización de snapshots legacy, claim perdido, una clasificación por
generación, persistencia de criterios seguros y conservación del snapshot previo
según el contrato RPC. El flujo completo usa el SDK real de Supabase sobre
`MockTransport`, Gemini/Places simulados y comprueba una escritura/generación
reutilizada por otro miembro. Se conserva `OfflineTestRunner` bloqueando red y DNS.

## 17. Baseline previo a editar

Desde `backend`, con `.venv/Scripts/python.exe`:

```text
manage.py test --settings=config.settings_test --noinput
Ran 709 tests in 53.962s
OK
System check identified no issues (0 silenced).
```

## 18. Tests específicos finales

```text
manage.py test apps.plans.tests.test_gemini_intent apps.plans.tests.test_recommendations apps.plans.tests.test_recommendations_ui --settings=config.settings_test --noinput
Ran 108 tests in 0.640s
OK
```

## 19. Check Django

`manage.py check` con la configuración local de ejecución:
`System check identified no issues (0 silenced).` No imprime claves ni realiza
consultas a Gemini/Geoapify.

## 20. Suite completa final

```text
manage.py test --settings=config.settings_test --noinput
Ran 737 tests in 44.112s
OK
System check identified no issues (0 silenced).
```

Son 28 tests adicionales sobre el baseline de 709. El log completo de esta
ejecución se guarda temporalmente en `%TEMP%\planb-prompt3-tests.log`.

## 21. Diff check

`git diff --check`: sin errores. Se revisó el diff de código y configuración;
`git diff --name-only -- .env supabase/migrations backend/apps/users/migrations`
no devuelve cambios. El estado final contiene ocho archivos modificados y los
tres archivos nuevos detallados arriba. Sin archivos staged, commit ni push.

## 22. Migrations y alcance

No se crea ninguna migration ni se modifican las existentes. No hay cambios en
RLS/RPC, permisos, ranking, `PlaceSearch`, Geoapify, endpoints, mapa ni cards.
No se modifica `.env`, no se ejecuta SQL remoto, no se hace commit ni push.
El recálculo de ready y la explicación general del Prompt 4 quedan fuera.

## 23. Prueba manual con Gemini real (a cargo del usuario)

1. Reiniciar Django para cargar la configuración nueva. Usar la credencial
   `GEMINI_API_KEY` que ya existe localmente y la configuración Geoapify vigente.
   No copiar claves al navegador ni al chat. `GEMINI_MODEL` no es obligatorio.
2. Crear un plan nuevo y una propuesta con coordenadas válidas en una zona con
   oferta de lugares, titulada «Queremos salir a tomar algo y después bailar».
   Confirmarla con el flujo habitual. Un plan con snapshot ready reutilizará sus
   datos y no sirve para verificar una clasificación nueva.
3. Abrir el detalle: la generación automática debe llegar a ready con hasta cinco
   lugares reales (o ready sin resultados si no hay candidatos). Verificar las
   cards y el mapa habituales; no esperar explicaciones nuevas.
4. En el inspector de red del navegador, verificar el POST de generación y el
   GET de estado. No deben existir requests del navegador a Gemini/Geoapify ni
   exponerse claves, prompt o categorías de clasificación en la respuesta.
5. Para verificar la interpretación, inspeccionar desde una herramienta interna
   autorizada el `criteria` persistido en `plan_recommendations`: esperar
   `interpreter: gemini`, el modelo usado y `place_intents` coherentes, idealmente
   `bar` y `nightclub`; sus categorías provienen exclusivamente del mapping.
   Verificar que los items tienen `provider: geoapify` y su ID del proveedor.
   No hace falta modificar datos ni ejecutar SQL de escritura.
6. Recargar y abrir con otro integrante: comprobar la reutilización del conjunto
   y de `generated_at`. Seleccionar cards o mover el mapa no debe generar otro
   POST de recomendaciones ni consumir clasificación/Places.
7. Probar planes nuevos con «Vamos a morfar algo», «Cafecito y algo dulce»,
   «Comprar galletitas para comer en el departamento» y «Vamos a ver una película».
   Verificar intenciones restaurant, cafe, supermarket y cinema respectivamente.
8. Probar «Nos vemos el sábado» sin descripción: debe aparecer el estado de error
   controlado correspondiente a unsupported, sin recomendaciones aleatorias.
9. Para comprobar fallos opcionalmente, usar un proceso/entorno de desarrollo
   aislado con `GEMINI_API_KEY` ausente o `GEMINI_MODEL` inaccesible, sin editar el
   `.env` real. Confirmar un plan nuevo, comprobar detalle utilizable y error
   controlado, restaurar la configuración del proceso y pulsar Reintentar.
   No hace falta volver a confirmar. No provocar 429 con tráfico artificial.

## 24. Decisiones y límites antes del commit

- No se realizó la prueba real: disponibilidad de cuenta, cuota y calidad
  semántica quedan para la validación manual solicitada.
- El ranking existente elige el Top 5 del conjunto combinado; no garantiza una
  cantidad fija de resultados por intención ni genera un itinerario.
- El texto se limita a 255/4000 caracteres; una descripción más larga aporta
  sólo su comienzo. Es una decisión de minimización y consumo.
- Los fallos de ambos proveedores comparten los códigos genéricos y el copy de
  reintento existente; no se crea UI técnica ni nomenclatura nueva en la base.
- El test de conservación de snapshot es offline con el contrato RPC simulado;
  no sustituye ejecutar la integración SQL en un entorno autorizado.
