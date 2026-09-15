# Aurora AI

Portal web con cuentas, pagos manuales, tokens personales y chat efímero. Uncensored funciona mediante Ollama en este PC. Los demás servicios se solicitan directamente al administrador por WhatsApp.

## Uso diario

1. En Windows, ejecuta `Iniciar-Qwen-Movil.cmd`. El lanzador detecta Node.js y Ollama, crea la clave administrativa si falta y descarga automáticamente el cliente oficial de Cloudflare Tunnel.
2. El archivo `ENLACE AURORA AI.txt` se actualizará con el nuevo enlace público.
3. Cada cliente crea un usuario y contraseña, selecciona Uncensored, elige de 1 a 12 meses y registra su pago por Nequi con número de WhatsApp y foto del comprobante. La referencia de Nequi es opcional; si falta, Aurora genera una interna con la fecha. Al terminar se abre un mensaje de WhatsApp con todos los datos para que el cliente confirme el envío. El precio se calcula automáticamente a `$10.000 COP` por mes.
4. Entra a **Administrar** con la clave local. Puedes aprobar comprobantes o dar acceso manual a cualquier cuenta registrada; en ambos casos se crea un token único.
5. El cliente usa usuario, contraseña y token para entrar.

El panel administrativo también permite registrar clientes atendidos por WhatsApp, guardar su fecha de inicio, pago, servicio y duración, y completar después una duración que haya quedado pendiente.

## Datos y privacidad

- No se escribe en disco el historial de las conversaciones ni las imágenes adjuntas.
- Sí se guardan en `data/users.json` los datos mínimos de cuenta, WhatsApp, estado del pago y token cifrado. Las fotos de comprobantes se guardan de forma privada en `data/receipts` y solo se sirven tras iniciar sesión como administrador.
- La app escucha en `127.0.0.1:4173`; Ollama permanece en `127.0.0.1:11434` y Cloudflare entrega HTTPS.
- Las sesiones firmadas duran siete días.
- Solo hay una generación local simultánea para proteger el PC.
- Las respuestas de Uncensored usan 12K de contexto y hasta cuatro bloques de 2K tokens. Si un bloque termina por longitud, el servidor continúa automáticamente sin repetir la introducción.

## Servicios

- Uncensored/Ollama: único modelo que se activa y usa dentro del portal.
- Uncensored cuesta `$10.000 COP` por mes. El cliente elige entre 1 y 12 meses (6 meses siguen costando `$60.000 COP`) y la aprobación propone automáticamente la duración solicitada.
- ChatGPT cuesta `$40.000 COP` por un mes. Gemini cuesta `$40.000 COP` por 12 meses, incluye 400 GB de almacenamiento y requiere consultar las condiciones de acceso antes de pagar. Ambos se solicitan por WhatsApp.
- Imagen y video cuestan `$50.000 COP` por un mes con generaciones ilimitadas y se solicitan por WhatsApp.
- WhatsApp prepara el mensaje, pero el visitante debe confirmar su envío.

El túnel gratuito `trycloudflare.com` es temporal y cambia al reactivarlo. Para una URL fija hace falta un dominio y un túnel Cloudflare administrado.
