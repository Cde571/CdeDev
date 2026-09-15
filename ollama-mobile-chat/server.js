const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const HOST = '127.0.0.1';
const PORT = Number(process.env.PORT || 4173);
const PUBLIC_DIR = path.join(__dirname, 'public');
const DATA_DIR = process.env.AURORA_DATA_DIR || path.join(__dirname, 'data');
const RECEIPTS_DIR = path.join(DATA_DIR, 'receipts');
const DB_FILE = path.join(DATA_DIR, 'users.json');
const SECRET_FILE = path.join(__dirname, '.session-secret');
const ADMIN_CODE_FILE = path.join(__dirname, '.access-code');
const OLLAMA_URL = process.env.OLLAMA_URL || 'http://127.0.0.1:11434';
const QWEN_MODEL = process.env.OLLAMA_MODEL || 'srchmnmichael/qwen3.5-9B-uncensored:latest';
const OLLAMA_CONTEXT = Math.min(32768, Math.max(4096, Number.parseInt(process.env.OLLAMA_CONTEXT, 10) || 12288));
const OLLAMA_PART_TOKENS = Math.min(4096, Math.max(32, Number.parseInt(process.env.OLLAMA_PART_TOKENS, 10) || 2048));
const OLLAMA_MAX_PARTS = Math.min(8, Math.max(1, Number.parseInt(process.env.OLLAMA_MAX_PARTS, 10) || 4));
const UNCENSORED_MONTHLY_PRICE = 10000;
const COOKIE_NAME = 'aurora_session';
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;
const SYSTEM_MESSAGE = 'Responde de forma clara, útil y bien estructurada. Usa Markdown. Para matemáticas usa LaTeX: \\( ... \\) en línea y \\[ ... \\] en bloque. Termina siempre la respuesta.';

fs.mkdirSync(DATA_DIR, { recursive: true });
fs.mkdirSync(RECEIPTS_DIR, { recursive: true });
if (!fs.existsSync(DB_FILE)) fs.writeFileSync(DB_FILE, JSON.stringify({ users: [] }, null, 2));
if (!fs.existsSync(SECRET_FILE)) fs.writeFileSync(SECRET_FILE, crypto.randomBytes(48).toString('base64url'));
const SESSION_SECRET = fs.readFileSync(SECRET_FILE, 'utf8').trim();
const ADMIN_CODE = fs.readFileSync(ADMIN_CODE_FILE, 'utf8').trim();
const loginAttempts = new Map();
let activeGeneration = false;

const MODELS = [
  { id: 'qwen', name: 'Uncensored', badge: 'Disponible', provider: 'Privado', priceLabel: 'Desde $10.000 COP al mes · elige 1 a 12 meses', monthlyPriceCop: UNCENSORED_MONTHLY_PRICE, defaultMonths: 6, description: 'Asistente de texto alojado en este equipo, con respuestas directas y gran libertad creativa.', theoreticalLimit: '262K de contexto (modelo)', serviceLimit: '12K de contexto · hasta 8K de salida en partes', supportsImages: false, kind: 'chat', contactOnly: false, capabilities: ['Conversación y lluvia de ideas', 'Respuestas largas con continuación automática', 'Duración seleccionable de 1 a 12 meses', 'Redacción, resumen y traducción', 'Programación y explicación de código', 'Matemáticas con fórmulas LaTeX', 'Sesiones de texto efímeras'] },
  { id: 'gpt', name: 'ChatGPT', badge: 'Solicitar acceso', provider: 'OpenAI', priceLabel: '$40.000 COP · 1 mes', description: 'Chat inteligente para conversar, analizar imágenes, escribir y resolver tareas. Solicita tus credenciales por WhatsApp.', theoreticalLimit: 'Texto, visión y razonamiento', serviceLimit: 'Acceso mediante credenciales', supportsImages: true, kind: 'chat', contactOnly: true, capabilities: ['Razonamiento complejo y análisis', 'Comprensión de imágenes', 'Escritura profesional y código', 'Respuestas estructuradas y matemáticas', 'Flujos multimodales'] },
  { id: 'gemini', name: 'Gemini', badge: 'Solicitar acceso', provider: 'Google', priceLabel: '$40.000 COP · 12 meses', requirements: 'Este acceso requiere cumplir unas condiciones. Solicita los requisitos por WhatsApp antes de pagar.', description: 'Paquete de inteligencia artificial por 12 meses, con 400 GB de almacenamiento y herramientas avanzadas. Acceso sujeto a requisitos.', theoreticalLimit: '400 GB de almacenamiento incluidos', serviceLimit: '12 meses · acceso mediante credenciales', supportsImages: true, kind: 'chat', contactOnly: true, capabilities: ['Gemini para texto, imágenes y análisis', '12 meses de acceso', '400 GB de almacenamiento', 'Análisis y resumen de documentos', 'Escritura, traducción y programación', 'Herramientas avanzadas incluidas en el paquete'] },
  { id: 'image', name: 'Nano Banana 2', badge: 'Solicitar acceso', provider: 'Google', priceLabel: '$50.000 COP · 1 mes · ilimitado', description: 'Generación y edición ilimitada de imágenes durante un mes, disponible mediante credenciales solicitadas por WhatsApp.', theoreticalLimit: 'Generaciones ilimitadas', serviceLimit: 'Acceso mediante credenciales', supportsImages: true, kind: 'media', contactOnly: true, capabilities: ['Crear imágenes desde una descripción', 'Editar imágenes existentes', 'Variaciones visuales y conceptos', 'Composición con texto', 'Generaciones ilimitadas durante el mes'] },
  { id: 'video', name: 'Veo 3.1', badge: 'Solicitar acceso', provider: 'Google', priceLabel: '$50.000 COP · 1 mes · ilimitado', description: 'Generación ilimitada de video durante un mes, disponible mediante credenciales solicitadas por WhatsApp.', theoreticalLimit: 'Generaciones ilimitadas', serviceLimit: 'Acceso mediante credenciales', supportsImages: true, kind: 'media', contactOnly: true, capabilities: ['Crear clips desde texto', 'Animar una imagen inicial', 'Movimiento de cámara y estilo', 'Video con audio sincronizado', 'Generaciones ilimitadas durante el mes'] }
];

const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };

function readDb() { try { const db = JSON.parse(fs.readFileSync(DB_FILE, 'utf8')); db.users = Array.isArray(db.users) ? db.users : []; db.customers = Array.isArray(db.customers) ? db.customers : []; return db; } catch { return { users: [], customers: [] }; } }
function writeDb(db) { const temp = `${DB_FILE}.tmp`; fs.writeFileSync(temp, JSON.stringify(db, null, 2)); fs.renameSync(temp, DB_FILE); }
function json(res, status, body, headers = {}) { res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers }); res.end(JSON.stringify(body)); }

async function readJson(req, maxBytes = 8 * 1024 * 1024) {
  const chunks = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > maxBytes) throw new Error('La solicitud supera el límite permitido.'); chunks.push(chunk); }
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function normalizeUsername(value) { return String(value || '').trim().toLowerCase().replace(/\s+/g, ''); }
function normalizeAdminCode(value) { const code = String(value || '').trim(); return /^\d+$/.test(ADMIN_CODE) ? code.replace(/\D/g, '') : code; }
function hashPassword(password, salt = crypto.randomBytes(16).toString('hex')) { return { salt, hash: crypto.scryptSync(String(password), salt, 64).toString('hex') }; }
function safeEqual(a, b) { const left = Buffer.from(String(a)); const right = Buffer.from(String(b)); return left.length === right.length && crypto.timingSafeEqual(left, right); }
function sign(payload) { const data = Buffer.from(JSON.stringify(payload)).toString('base64url'); const signature = crypto.createHmac('sha256', SESSION_SECRET).update(data).digest('base64url'); return `${data}.${signature}`; }
function verifySigned(token) { try { const [data, signature] = String(token || '').split('.'); const expected = crypto.createHmac('sha256', SESSION_SECRET).update(data).digest('base64url'); if (!safeEqual(signature, expected)) return null; const payload = JSON.parse(Buffer.from(data, 'base64url').toString('utf8')); return payload.exp > Date.now() ? payload : null; } catch { return null; } }
function parseCookies(req) { return Object.fromEntries(String(req.headers.cookie || '').split(';').map(v => v.trim()).filter(Boolean).map(v => { const at = v.indexOf('='); return [decodeURIComponent(v.slice(0, at)), decodeURIComponent(v.slice(at + 1))]; })); }
function session(req) { return verifySigned(parseCookies(req)[COOKIE_NAME]); }
function cookieSecurity(req) { return req.headers['x-forwarded-proto'] === 'https' ? '; Secure' : ''; }
function sessionCookie(req, payload) { const token = sign({ ...payload, exp: Date.now() + SESSION_TTL_SECONDS * 1000 }); return `${COOKIE_NAME}=${token}; HttpOnly${cookieSecurity(req)}; SameSite=Lax; Path=/; Max-Age=${SESSION_TTL_SECONDS}`; }
function effectiveSubscription(user) {
  const subscription = { ...(user.subscription || { status: 'none', plan: null }) };
  if (subscription.status === 'active' && subscription.expiresAt && new Date(subscription.expiresAt).getTime() <= Date.now()) subscription.status = 'expired';
  return subscription;
}
function isActive(user) { return effectiveSubscription(user).status === 'active'; }
function publicUser(user) { const { receiptFile, ...subscription } = effectiveSubscription(user); return { id: user.id, name: user.name, username: user.username, createdAt: user.createdAt, subscription, tokenAvailable: Boolean(user.tokenCipher) }; }
function getUser(req) { const auth = session(req); if (!auth || auth.role !== 'user') return null; return readDb().users.find(user => user.id === auth.sub) || null; }

function rateLimited(req, bucket = 'login') {
  const key = `${bucket}:${req.headers['cf-connecting-ip'] || req.socket.remoteAddress || 'unknown'}`;
  const now = Date.now(); const item = loginAttempts.get(key) || { count: 0, reset: now + 15 * 60 * 1000 };
  if (item.reset < now) Object.assign(item, { count: 0, reset: now + 15 * 60 * 1000 });
  item.count += 1; loginAttempts.set(key, item); return item.count > 12;
}

function encryptToken(token) { const key = crypto.createHash('sha256').update(SESSION_SECRET).digest(); const iv = crypto.randomBytes(12); const cipher = crypto.createCipheriv('aes-256-gcm', key, iv); const encrypted = Buffer.concat([cipher.update(token, 'utf8'), cipher.final()]); return [iv, cipher.getAuthTag(), encrypted].map(value => value.toString('base64url')).join('.'); }
function decryptToken(value) { const [iv, tag, encrypted] = String(value).split('.').map(part => Buffer.from(part, 'base64url')); const key = crypto.createHash('sha256').update(SESSION_SECRET).digest(); const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv); decipher.setAuthTag(tag); return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8'); }

function cleanMessages(input) {
  if (!Array.isArray(input)) return [];
  return input.slice(-16).map(item => ({ role: item?.role === 'assistant' ? 'assistant' : 'user', content: String(item?.content || '').slice(0, 12000), image: item?.image && /^data:image\/(png|jpeg|webp);base64,/.test(item.image) ? item.image.slice(0, 6_000_000) : null })).filter(item => item.content.trim() || item.image);
}

function normalizeWhatsapp(value) { let digits = String(value || '').replace(/\D/g, '').slice(0, 15); if (digits.length === 10 && digits.startsWith('3')) digits = `57${digits}`; return digits; }
function colombiaDate(value) { const text = String(value || ''); if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) throw new Error('Selecciona una fecha válida.'); const date = new Date(`${text}T05:00:00.000Z`); if (Number.isNaN(date.getTime())) throw new Error('Selecciona una fecha válida.'); return date; }
function addCalendarMonths(value, months) { const date = new Date(value); date.setUTCMonth(date.getUTCMonth() + months); return date.toISOString(); }

function saveReceipt(dataUrl, userId) {
  const match = String(dataUrl || '').match(/^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$/);
  if (!match) throw new Error('Adjunta una foto PNG, JPG o WEBP del comprobante.');
  const bytes = Buffer.from(match[2], 'base64');
  if (!bytes.length || bytes.length > 4 * 1024 * 1024) throw new Error('La foto del comprobante debe pesar menos de 4 MB.');
  const extension = match[1] === 'jpeg' ? 'jpg' : match[1];
  const filename = `${userId}-${Date.now()}-${crypto.randomBytes(5).toString('hex')}.${extension}`;
  fs.writeFileSync(path.join(RECEIPTS_DIR, filename), bytes);
  return filename;
}

function serveStatic(req, res) {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const requested = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const file = path.resolve(PUBLIC_DIR, requested);
  if (!file.startsWith(PUBLIC_DIR + path.sep) && file !== path.join(PUBLIC_DIR, 'index.html')) return false;
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return false;
  res.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream', 'cache-control': path.extname(file) === '.html' ? 'no-cache' : 'public, max-age=3600', 'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY', 'referrer-policy': 'no-referrer' });
  fs.createReadStream(file).pipe(res); return true;
}

async function ollamaOnline() { try { return (await fetch(`${OLLAMA_URL}/api/version`, { signal: AbortSignal.timeout(2500) })).ok; } catch { return false; } }

async function answerWithProvider(modelId, messages, res) {
  if (modelId === 'qwen') {
    if (messages.some(message => message.image)) throw new Error('Uncensored es solo texto. Para analizar imágenes, solicita las credenciales del servicio correspondiente por WhatsApp.');
    const controller = new AbortController(); res.on('close', () => controller.abort());
    res.writeHead(200, { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-cache, no-transform' });
    const promptMessages = messages.map(({ role, content }) => ({ role, content })); let generated = ''; let finalReason = 'stop'; let completedParts = 0;
    for (let part = 1; part <= OLLAMA_MAX_PARTS; part += 1) {
      const continuation = part === 1 ? promptMessages : [...promptMessages, { role: 'assistant', content: generated }, { role: 'user', content: 'Continúa exactamente desde donde terminó la respuesta anterior. No repitas la introducción ni el contenido ya escrito. Conserva el formato y concluye completamente la respuesta.' }];
      const upstream = await fetch(`${OLLAMA_URL}/api/chat`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ model: QWEN_MODEL, messages: [{ role: 'system', content: SYSTEM_MESSAGE }, ...continuation], stream: true, think: false, keep_alive: '10m', options: { num_ctx: OLLAMA_CONTEXT, num_predict: OLLAMA_PART_TOKENS } }), signal: controller.signal });
      if (!upstream.ok || !upstream.body) throw new Error(`Ollama respondió ${upstream.status}`);
      if (part > 1) { const divider = `\n\n---\n\n**Continuación ${part}**\n\n`; generated += divider; res.write(`${JSON.stringify({ message: { content: divider }, part, continuation: true })}\n`); }
      const decoder = new TextDecoder(); let pending = ''; finalReason = 'stop'; completedParts = part;
      const handleLine = line => { if (!line.trim()) return; const item = JSON.parse(line); const content = item.message?.content || ''; if (content) { generated += content; res.write(`${JSON.stringify({ message: { content }, part })}\n`); } if (item.done) finalReason = item.done_reason || 'stop'; };
      for await (const chunk of upstream.body) { pending += decoder.decode(chunk, { stream: true }); const lines = pending.split('\n'); pending = lines.pop(); for (const line of lines) handleLine(line); }
      pending += decoder.decode(); if (pending.trim()) handleLine(pending);
      if (finalReason !== 'length') break;
    }
    res.write(`${JSON.stringify({ done: true, done_reason: finalReason, parts: completedParts, truncated: finalReason === 'length' })}\n`);
    return res.end();
  }
  if (modelId === 'gpt') {
    if (!process.env.OPENAI_API_KEY) throw new Error('GPT aún no está conectado. Configura OPENAI_API_KEY en el servidor.');
    const input = messages.map(message => ({ role: message.role, content: message.image ? [{ type: 'input_text', text: message.content || 'Analiza esta imagen.' }, { type: 'input_image', image_url: message.image }] : message.content }));
    const upstream = await fetch('https://api.openai.com/v1/responses', { method: 'POST', headers: { authorization: `Bearer ${process.env.OPENAI_API_KEY}`, 'content-type': 'application/json' }, body: JSON.stringify({ model: 'gpt-6-astra', input, max_output_tokens: 4096 }) });
    const data = await upstream.json(); if (!upstream.ok) throw new Error(data?.error?.message || 'OpenAI no respondió.');
    const content = data.output_text || data.output?.flatMap(item => item.content || []).map(item => item.text || '').join('') || '';
    res.writeHead(200, { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store' }); return res.end(`${JSON.stringify({ message: { content }, done: true })}\n`);
  }
  if (modelId === 'gemini') {
    if (!process.env.GEMINI_API_KEY) throw new Error('Gemini aún no está conectado. Configura GEMINI_API_KEY en el servidor.');
    const contents = messages.map(message => ({ role: message.role === 'assistant' ? 'model' : 'user', parts: [...(message.content ? [{ text: message.content }] : []), ...(message.image ? [{ inlineData: { mimeType: message.image.slice(5, message.image.indexOf(';')), data: message.image.split(',')[1] } }] : [])] }));
    const upstream = await fetch('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent', { method: 'POST', headers: { 'x-goog-api-key': process.env.GEMINI_API_KEY, 'content-type': 'application/json' }, body: JSON.stringify({ contents, generationConfig: { maxOutputTokens: 4096 } }) });
    const data = await upstream.json(); if (!upstream.ok) throw new Error(data?.error?.message || 'Gemini no respondió.');
    const content = data.candidates?.[0]?.content?.parts?.map(part => part.text || '').join('') || '';
    res.writeHead(200, { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store' }); return res.end(`${JSON.stringify({ message: { content }, done: true })}\n`);
  }
  throw new Error('Ese estudio todavía no tiene un conector activo.');
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (req.method === 'GET' && url.pathname === '/api/status') {
    const auth = session(req); const user = auth?.role === 'user' ? getUser(req) : null; const ollama = await ollamaOnline();
    return json(res, 200, { authenticated: Boolean(user), admin: auth?.role === 'admin', unlocked: Boolean(auth?.unlocked), user: user ? publicUser(user) : null, ollama, models: MODELS, providers: { qwen: ollama, gpt: false, gemini: false, image: false, video: false } });
  }
  if (req.method === 'POST' && url.pathname === '/api/auth/register') {
    if (rateLimited(req, 'register')) return json(res, 429, { error: 'Demasiados intentos. Espera unos minutos.' });
    try {
      const body = await readJson(req, 16 * 1024); const name = String(body.name || '').trim().slice(0, 80); const username = normalizeUsername(body.username); const password = String(body.password || '');
      if (name.length < 2 || !/^[a-z0-9._-]{3,40}$/.test(username) || password.length < 8) return json(res, 400, { error: 'Usa nombre válido, usuario de 3 a 40 caracteres y contraseña de mínimo 8 caracteres.' });
      const db = readDb(); if (db.users.some(user => user.username === username)) return json(res, 409, { error: 'Ese usuario ya existe.' });
      const secured = hashPassword(password); const user = { id: crypto.randomUUID(), name, username, passwordSalt: secured.salt, passwordHash: secured.hash, createdAt: new Date().toISOString(), subscription: { status: 'none', plan: null } };
      db.users.push(user); writeDb(db); return json(res, 201, { user: publicUser(user) }, { 'set-cookie': sessionCookie(req, { sub: user.id, role: 'user', unlocked: false }) });
    } catch { return json(res, 400, { error: 'No se pudo crear la cuenta.' }); }
  }
  if (req.method === 'POST' && url.pathname === '/api/auth/login') {
    if (rateLimited(req, 'login')) return json(res, 429, { error: 'Demasiados intentos. Espera unos minutos.' });
    try {
      const body = await readJson(req, 16 * 1024); const user = readDb().users.find(item => item.username === normalizeUsername(body.username));
      if (!user) return json(res, 401, { error: 'Usuario o contraseña incorrectos.' });
      const secured = hashPassword(body.password, user.passwordSalt); if (!safeEqual(secured.hash, user.passwordHash)) return json(res, 401, { error: 'Usuario o contraseña incorrectos.' });
      return json(res, 200, { user: publicUser(user) }, { 'set-cookie': sessionCookie(req, { sub: user.id, role: 'user', unlocked: false }) });
    } catch { return json(res, 400, { error: 'Solicitud inválida.' }); }
  }
  if (req.method === 'POST' && url.pathname === '/api/auth/logout') return json(res, 200, { ok: true }, { 'set-cookie': `${COOKIE_NAME}=; HttpOnly${cookieSecurity(req)}; SameSite=Lax; Path=/; Max-Age=0` });
  if (req.method === 'POST' && url.pathname === '/api/billing/claim') {
    const user = getUser(req); if (!user) return json(res, 401, { error: 'Inicia sesión.' });
    try {
      const body = await readJson(req, 6 * 1024 * 1024); if (body.plan !== 'qwen') return json(res, 400, { error: 'Solo Uncensored se activa dentro de la web. Para los demás accesos, contacta por WhatsApp.' });
      const durationMonths = Number.parseInt(body.durationMonths, 10); if (!Number.isInteger(durationMonths) || durationMonths < 1 || durationMonths > 12) return json(res, 400, { error: 'Selecciona una duración entre 1 y 12 meses.' });
      const payerName = String(body.payerName || '').trim().slice(0, 100); const reference = String(body.reference || '').trim().slice(0, 80); const amount = String(durationMonths * UNCENSORED_MONTHLY_PRICE); const whatsapp = normalizeWhatsapp(body.whatsapp);
      if (payerName.length < 2 || reference.length < 4 || !amount || whatsapp.length < 10) return json(res, 400, { error: 'Completa el nombre, valor, referencia y número de WhatsApp.' });
      const receiptFile = saveReceipt(body.receipt, user.id);
      const db = readDb(); const target = db.users.find(item => item.id === user.id); target.subscription = { status: 'pending', plan: body.plan, payerName, reference, amount, durationMonths, durationDaysRequested: durationMonths * 30, whatsapp, receiptFile, claimedAt: new Date().toISOString() }; delete target.tokenHash; delete target.tokenCipher; writeDb(db);
      return json(res, 200, { user: publicUser(target) });
    } catch (error) { return json(res, 400, { error: error.message || 'No se pudo registrar el comprobante.' }); }
  }
  if (req.method === 'GET' && url.pathname === '/api/account/token') {
    const user = getUser(req); if (!user || !isActive(user) || !user.tokenCipher) return json(res, 403, { error: 'Tu acceso todavía no está activo.' });
    try { return json(res, 200, { token: decryptToken(user.tokenCipher) }); } catch { return json(res, 500, { error: 'No se pudo recuperar el token.' }); }
  }
  if (req.method === 'POST' && url.pathname === '/api/access/verify') {
    const user = getUser(req); if (!user || !isActive(user)) return json(res, 403, { error: 'Tu pago aún no ha sido aprobado o la suscripción venció.' });
    const body = await readJson(req, 8 * 1024); const hash = crypto.createHash('sha256').update(String(body.token || '')).digest('hex');
    if (!user.tokenHash || !safeEqual(hash, user.tokenHash)) return json(res, 401, { error: 'Token incorrecto.' });
    return json(res, 200, { ok: true }, { 'set-cookie': sessionCookie(req, { sub: user.id, role: 'user', unlocked: true }) });
  }
  if (req.method === 'POST' && url.pathname === '/api/admin/login') {
    if (rateLimited(req, 'admin')) return json(res, 429, { error: 'Demasiados intentos.' }); const body = await readJson(req, 8 * 1024);
    if (!safeEqual(normalizeAdminCode(body.code), ADMIN_CODE)) return json(res, 401, { error: 'Clave de administrador incorrecta. Escribe únicamente los cuatro números.' });
    return json(res, 200, { ok: true }, { 'set-cookie': sessionCookie(req, { sub: 'admin', role: 'admin', unlocked: true }) });
  }
  if (req.method === 'GET' && url.pathname === '/api/admin/users') {
    if (session(req)?.role !== 'admin') return json(res, 401, { error: 'Acceso de administrador requerido.' }); const db = readDb();
    return json(res, 200, { users: db.users.map(user => { const payment = effectiveSubscription(user); return { ...publicUser(user), payment: { ...payment, receiptUrl: payment.receiptFile ? `/api/admin/receipt/${encodeURIComponent(payment.receiptFile)}` : null } }; }), customers: db.customers });
  }
  if (req.method === 'GET' && url.pathname.startsWith('/api/admin/receipt/')) {
    if (session(req)?.role !== 'admin') return json(res, 401, { error: 'Acceso de administrador requerido.' });
    const filename = decodeURIComponent(url.pathname.slice('/api/admin/receipt/'.length));
    if (!/^[a-f0-9-]+-\d+-[a-f0-9]+\.(png|jpg|webp)$/.test(filename)) return json(res, 400, { error: 'Comprobante inválido.' });
    const receiptPath = path.join(RECEIPTS_DIR, filename); if (!fs.existsSync(receiptPath)) return json(res, 404, { error: 'Comprobante no encontrado.' });
    const extension = path.extname(filename).toLowerCase(); const contentType = extension === '.png' ? 'image/png' : extension === '.webp' ? 'image/webp' : 'image/jpeg';
    res.writeHead(200, { 'content-type': contentType, 'cache-control': 'private, no-store', 'x-content-type-options': 'nosniff' }); return fs.createReadStream(receiptPath).pipe(res);
  }
  if (req.method === 'POST' && url.pathname === '/api/admin/customers') {
    if (session(req)?.role !== 'admin') return json(res, 401, { error: 'Acceso de administrador requerido.' });
    try {
      const body = await readJson(req, 32 * 1024); const name = String(body.name || '').trim().slice(0, 100); const whatsapp = normalizeWhatsapp(body.whatsapp); const amount = String(body.amount || '').replace(/\D/g, '').slice(0, 12); const service = String(body.service || 'gpt_or_gemini').slice(0, 40); const startedAt = colombiaDate(body.startedAt); const durationMonths = Math.min(36, Math.max(0, Number.parseInt(body.durationMonths, 10) || 0));
      if (name.length < 2 || whatsapp.length < 10 || !amount) return json(res, 400, { error: 'Completa nombre, WhatsApp, valor y fecha.' });
      const db = readDb(); if (db.customers.some(customer => customer.whatsapp === whatsapp && customer.startedAt === startedAt.toISOString())) return json(res, 409, { error: 'Ese cliente ya está registrado en esa fecha.' });
      const customer = { id: crypto.randomUUID(), name, whatsapp, amount, service, startedAt: startedAt.toISOString(), durationMonths: durationMonths || null, expiresAt: durationMonths ? addCalendarMonths(startedAt, durationMonths) : null, status: durationMonths ? 'active' : 'pending_duration', createdAt: new Date().toISOString() };
      db.customers.push(customer); writeDb(db); return json(res, 201, { customer });
    } catch (error) { return json(res, 400, { error: error.message || 'No se pudo agregar el cliente.' }); }
  }
  if (req.method === 'POST' && url.pathname === '/api/admin/customers/duration') {
    if (session(req)?.role !== 'admin') return json(res, 401, { error: 'Acceso de administrador requerido.' }); const body = await readJson(req, 8 * 1024); const months = Math.min(36, Math.max(1, Number.parseInt(body.durationMonths, 10) || 1)); const db = readDb(); const customer = db.customers.find(item => item.id === body.customerId);
    if (!customer) return json(res, 404, { error: 'Cliente no encontrado.' }); customer.durationMonths = months; customer.expiresAt = addCalendarMonths(customer.startedAt, months); customer.status = 'active'; writeDb(db); return json(res, 200, { customer });
  }
  if (req.method === 'POST' && (url.pathname === '/api/admin/approve' || url.pathname === '/api/admin/reject')) {
    if (session(req)?.role !== 'admin') return json(res, 401, { error: 'Acceso de administrador requerido.' }); const body = await readJson(req, 8 * 1024); const db = readDb(); const user = db.users.find(item => item.id === body.userId);
    if (!user) return json(res, 404, { error: 'Usuario no encontrado.' });
    if (url.pathname.endsWith('reject')) { user.subscription = { ...user.subscription, status: 'rejected', reviewedAt: new Date().toISOString() }; delete user.tokenHash; delete user.tokenCipher; writeDb(db); return json(res, 200, { ok: true }); }
    const requestedDays = Number.parseInt(user.subscription?.durationDaysRequested, 10) || 180;
    const durationDays = Math.min(3650, Math.max(1, Number.parseInt(body.durationDays, 10) || requestedDays));
    const activatedAt = new Date();
    const expiresAt = new Date(activatedAt.getTime() + durationDays * 86400000);
    const token = `aur_${crypto.randomBytes(24).toString('base64url')}`; user.tokenHash = crypto.createHash('sha256').update(token).digest('hex'); user.tokenCipher = encryptToken(token); user.subscription = { ...user.subscription, plan: 'qwen', status: 'active', durationDays, activatedAt: activatedAt.toISOString(), expiresAt: expiresAt.toISOString() }; writeDb(db);
    return json(res, 200, { ok: true, token, user: publicUser(user) });
  }
  if (req.method === 'POST' && url.pathname === '/api/admin/extend') {
    if (session(req)?.role !== 'admin') return json(res, 401, { error: 'Acceso de administrador requerido.' });
    const body = await readJson(req, 8 * 1024); const days = Math.min(3650, Math.max(1, Number.parseInt(body.durationDays, 10) || 30)); const db = readDb(); const user = db.users.find(item => item.id === body.userId);
    if (!user || !user.subscription?.activatedAt) return json(res, 404, { error: 'Suscripción no encontrada.' });
    const currentEnd = user.subscription.expiresAt ? new Date(user.subscription.expiresAt).getTime() : Date.now(); const base = Math.max(Date.now(), currentEnd);
    user.subscription = { ...user.subscription, status: 'active', expiresAt: new Date(base + days * 86400000).toISOString() }; writeDb(db);
    return json(res, 200, { ok: true, user: publicUser(user) });
  }
  if (req.method === 'POST' && url.pathname === '/api/chat') {
    const auth = session(req); const user = getUser(req); if (!user || !auth?.unlocked) return json(res, 401, { error: 'Inicia sesión y valida tu token personal.' });
    if (!isActive(user)) return json(res, 403, { error: 'Tu acceso no está activo o la suscripción venció.' }); if (activeGeneration) return json(res, 429, { error: 'El modelo está ocupado. Intenta de nuevo en un momento.' });
    try { const body = await readJson(req); const modelId = String(body.model || user.subscription.plan); if (modelId !== 'qwen' || user.subscription.plan !== 'qwen') return json(res, 403, { error: 'Solo Uncensored funciona dentro de este portal. Solicita las demás credenciales por WhatsApp.' }); const messages = cleanMessages(body.messages); if (!messages.length) return json(res, 400, { error: 'Escribe un mensaje.' }); activeGeneration = true; await answerWithProvider(modelId, messages, res); }
    catch (error) { if (!res.headersSent) json(res, 502, { error: error.message || 'No se pudo generar la respuesta.' }); else res.end(); }
    finally { activeGeneration = false; } return;
  }
  if ((req.method === 'GET' || req.method === 'HEAD') && serveStatic(req, res)) return;
  json(res, 404, { error: 'No encontrado.' });
});

setInterval(() => { const now = Date.now(); for (const [key, entry] of loginAttempts) if (entry.reset < now) loginAttempts.delete(key); }, 10 * 60 * 1000).unref();
server.listen(PORT, HOST, () => { console.log(`Aurora AI listo en http://${HOST}:${PORT}`); console.log(`Ollama: ${QWEN_MODEL}`); });
