# EP AI DLP — Protección de datos de IA en endpoints

**Explora cómo inspeccionar datos sensibles antes de que una aplicación Windows los envíe a un servicio de IA.**

[English](README.md) · [한국어](README.ko.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md)

> Vista previa experimental de investigación. No sustituye un DLP de producción ni garantiza la prevención completa de fugas. El README en inglés es la referencia; la consola está en inglés.

Consola real con datos sintéticos de interfaz. Nombres, contadores y estados ilustran la experiencia; no representan clientes ni pruebas de bloqueo en vivo.

![Console overview](docs/assets/console-overview.png)

<table><tr><td width="50%"><img src="docs/assets/console-policy.png" alt="Policy"></td><td width="50%"><img src="docs/assets/console-devices.png" alt="Devices"></td></tr></table>

![Inspection events](docs/assets/console-events-dark.png)

## Un modelo de decisión local inspirado en Jev

Jev me inspiró a aplicar pequeños modelos de decisión a AI DLP para complementar las limitaciones de las expresiones regulares al interpretar condiciones comerciales, contexto y políticas. Enviar contenido sensible a una API comercial de IA para inspeccionarlo introduce otra preocupación de exposición. Por eso implementé inferencia local y ajuste con datos DLP. Tras construirlo y probarlo, mi conclusión personal fue: es útil y merece seguir desarrollándose. No implica integración oficial ni afiliación con Jev.

Las reglas deterministas siguen detectando identificadores y secretos conocidos. El modelo devuelve puntuaciones; un motor separado controla permisos, aprobación, bloqueo y revisión. Un servidor de inferencia remoto configurado explícitamente recibe el texto y debe permanecer dentro del perímetro de confianza de la organización.

En el mismo M4 Max, BF16 y 736 decisiones sintéticas reutilizadas, la precisión original→ajustada fue **66.6→81.8%** para Decider 2B, **82.7→89.9%** para Jeff Qwen 2B y **83.8→91.2%** para Jeff Gemma4 E2B. Mediana de API ajustada: 68/87/121ms. Son 54 familias sintéticas, no una garantía de precisión en producción. Los nuevos modelos pequeños no se instalaron en la protección de Windows ni sustituyeron el modelo predeterminado en ejecución.

[Guía](docs/local-judge.md) · [API](docs/jev-api.md) · [Entrenamiento](judge/training/README.md) · [Resultados](research/judge-candidates/SMALL_MODEL_FINETUNING.md)


## Implementado

- Motor Rust con captura TCP selectiva, inspección TLS, análisis acotado y detección determinista.
- Servicio Windows que captura nuevos procesos de las rutas ejecutables configuradas, sin perfil de navegador dedicado ni parámetro de proxy en esta modalidad.
- Once reglas: patrones de credenciales, marcadores de claves privadas, correo, tarjetas, teléfonos e identificadores de residente coreanos.
- Registro de un solo uso, políticas Ed25519 vinculadas al dispositivo, acciones de bloqueo o supervisión por regla y versión aplicada.
- Consola Next.js, NestJS y PostgreSQL; eventos de metadatos sin el texto protegido.

Un solo tenant y una política compartida. Dominios y ejecutables se configuran en el agente. No incluye inspección de respuestas, extracción de archivos, clasificación contextual ni Agent IAM.

```text
Windows application -> TCP capture -> Rust TLS / DLP -> AI destination
                           ^
                  signed policy / metadata
                           |
              Next.js -> NestJS -> PostgreSQL
```

## Iniciar la consola local

Requiere Node.js 22.19+, npm y Docker Compose. Iniciar la consola no protege automáticamente los endpoints.

```bash
git clone https://github.com/hellocosmos/ep-ai-dlp.git
cd ep-ai-dlp
npm ci
npm run bootstrap
docker compose --env-file .local/managed.env -f deploy/compose.yaml up -d
npm run build
```

Ejecuta `npm run dev:server` y `npm run start:console` en terminales separadas y abre `http://127.0.0.1:3100`. Las credenciales aleatorias se generan en `.local/managed.env`, excluido de Git.

## Evidencia y límites

En un destino HTTPS controlado se verificó la llegada de solicitudes normales y la ausencia de incremento para solicitudes sensibles sintéticas bloqueadas. Se comprobó Chrome normal tras reiniciar el servicio y una conversación inocua con ChatGPT. Esto no demuestra compatibilidad general.

**Queda un posible falso positivo en una solicitud auxiliar de ChatGPT. Cargas de archivos reales, más servicios y navegadores, avisos de bloqueo, rendimiento, reinicio y recuperación de fallos, y resistencia a manipulación requieren validación. Un fallo del servicio puede liberar la captura: no se garantiza bloqueo continuo ante fallos. No hay captura de endpoints macOS ni auditoría inmutable.**

## Por qué compartirlo

Diseñado y validado por Jaemyung Kim, arquitecto de productos de ciberseguridad, con implementación asistida por agentes de programación de IA. Compartimos arquitectura, modelo de amenazas, criterios de aceptación y límites.

El código original usa MIT; componentes de terceros como Windows redirector y WinDivert conservan sus licencias. Los experimentos anteriores de navegador Python y .NET se mantienen como rutas separadas. Se excluyen credenciales operativas, evidencia bruta y documentos comerciales internos.

[English reference](README.md) · [Setup](docs/getting-started.md) · [Windows lab](docs/windows-lab.md) · [Architecture](docs/architecture.md) · [Validation](docs/validation.md) · [Screenshots](docs/screenshot-provenance.md) · [Security](SECURITY.md) · [Licenses](THIRD_PARTY.md)
