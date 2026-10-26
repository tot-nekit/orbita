"use strict";
const $ = (id) => document.getElementById(id);
const isMini = /^\/mini\/?$/.test(location.pathname);
const tg = isMini ? window.Telegram?.WebApp : null;
if (isMini) document.body.classList.add("mini");
const prefs = {
  get(k, d) {
    try {
      return localStorage.getItem("orbita.v2." + k) || d;
    } catch {
      return d;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem("orbita.v2." + k, v);
    } catch {}
  },
};
const shapes = {
  spark: "M4 4h16v12H9l-5 4Z",
  calendar: "M8 2v6M16 2v6M3 10h18M7 14h3M14 14h3M7 17h3|r:3,5,18,16,2",
  search: "M16 16l5 5|c:10.5,10.5,6.5",
  settings:
    "M9 3 8 6 5 7 3 10l2 2v3l3 2 1 4h6l1-4 3-2v-3l2-2-2-3-3-1-1-3Z|c:12,12,3",
  clock: "M12 6v6l4 2|c:12,12,9",
  check: "M5 12l4 4L20 5",
  plus: "M12 5v14M5 12h14",
  moon: "M20 15.3A9 9 0 0 1 8.7 4 9 9 0 1 0 20 15.3Z",
  volume: "M11 5 5 9H2v6h3l6 4ZM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14",
  mic: "M8 10V6a4 4 0 0 1 8 0v4a4 4 0 0 1-8 0ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8",
  arrow: "M12 20V4M5 11l7-7 7 7",
  lock: "M7 10V7a5 5 0 0 1 10 0v3M12 14v3|r:4,10,16,12,2",
  link: "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2",
  refresh:
    "M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2",
  telegram: "M22 3 2 11l7 3 3 7 10-18ZM9 14 22 3M12 21l2-6",
  database:
    "M3 5v14c0 4 18 4 18 0V5M3 12c0 4 18 4 18 0M3 5c0-4 18-4 18 0S3 9 3 5Z",
  close: "M6 6l12 12M18 6 6 18",
  question: "M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5M12 17h.01|c:12,12,10",
};
function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [k, v] of Object.entries({
    class: "ui-icon",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.6",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
  }))
    svg.setAttribute(k, v);
  for (const part of (shapes[name] || shapes.spark).split("|")) {
    let tag = "path",
      attrs = { d: part };
    if (part.startsWith("c:")) {
      tag = "circle";
      const [cx, cy, r] = part.slice(2).split(",");
      attrs = { cx, cy, r };
    }
    if (part.startsWith("r:")) {
      tag = "rect";
      const [x, y, width, height, rx] = part.slice(2).split(",");
      attrs = { x, y, width, height, rx };
    }
    const el = document.createElementNS(svg.namespaceURI, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
  }
  return svg;
}
document
  .querySelectorAll("[data-icon]")
  .forEach((el) => el.replaceChildren(icon(el.dataset.icon)));
let token = null,
  config = {},
  delivery = null,
  timezone = "Europe/Moscow",
  session = prefs.get("session", crypto.randomUUID()),
  view = "assistant",
  range = "today",
  busy = false,
  speaking = prefs.get("speak", "false") === "true",
  currentEvent = null,
  plannerOffset = 0,
  searchOffset = 0,
  plannerGeneration = 0,
  searchGeneration = 0;
// A conversation handle is public, random metadata, never an authentication token.
if (!/^[a-f0-9-]{36}$/i.test(session)) session = crypto.randomUUID();
prefs.set("session", session);
let authRequired = false,
  authMode = "login",
  identityVersion = 0,
  privateReady = false;
const requests = new Set();
const identityChannel =
  typeof BroadcastChannel === "function"
    ? new BroadcastChannel("orbita.identity")
    : null;
function staleResponse() {
  return Object.assign(new Error("Пространство изменилось."), { stale: true });
}
function clearPrivate() {
  privateReady = false;
  identityVersion++;
  for (const controller of requests) controller.abort();
  requests.clear();
  plannerGeneration++;
  searchGeneration++;
  clearTimeout(search.timer);
  clearTimeout(recordTimer);
  clearTimeout(toast.timer);
  window.speechSynthesis?.cancel();
  if (recognition) {
    recognition.onresult = recognition.onend = recognition.onerror = null;
    recognition.abort();
    recognition = null;
  }
  if (recorder) {
    recorder.onstop = recorder.ondataavailable = recorder.onerror = null;
    if (recorder.state === "recording") recorder.stop();
    recorder = null;
  }
  stopStream();
  starting = transcribing = busy = false;
  token = null;
  config = {};
  delivery = currentEvent = null;
  timezone = "Europe/Moscow";
  historyLoaded = false;
  historyLoading = refreshLoading = null;
  session = crypto.randomUUID();
  prefs.set("session", session);
  for (const id of [
    "messages",
    "planner-list",
    "search-list",
    "trash-list",
    "today-list",
    "toast-text",
    "chat-notice",
    "planner-count",
    "search-count",
    "import-result",
    "link-error",
    "form-error",
    "settings-username",
    "recovery-code",
    "password-error",
    "delete-error",
  ])
    $(id)?.replaceChildren();
  document.querySelectorAll("dialog[open]").forEach((el) => el.close());
  for (const id of [
    "event-form",
    "search-form",
    "chat-form",
    "password-form",
    "delete-account-form",
    "link-form",
  ])
    $(id).reset();
  $("auth-form").reset();
  $("trash-list").hidden = $("toast").hidden = $("chat-notice").hidden = true;
  $("planner-more").hidden = $("search-more").hidden = true;
  $("account-card").hidden = true;
  $("open-bot").hidden = true;
  $("open-bot").removeAttribute("href");
  $("use-ai").checked = $("notify-toggle").checked = false;
  $("encryption-state").textContent = "";
  $("ai-details").textContent = $("voice-details").textContent = "";
  $("use-ai-state").textContent =
    $("telegram-state").textContent =
    $("notify-state").textContent =
      "";
  $("voice-status").textContent = "Enter — отправить · Shift + Enter — перенос";
  $("prompt").style.height = "auto";
  $("account-name").textContent = "Мои записи";
  $("account-channel").textContent = "";
  $("connection").textContent = "Вход не выполнен";
  for (const id of [
    "stat-today",
    "stat-meetings",
    "stat-done",
    "stat-inbox",
    "stat-overdue",
  ])
    $(id).textContent = "—";
  $("recovery-panel").hidden = true;
  $("recovery-saved").checked = false;
  $("recovery-continue").disabled = true;
  $("password-details").open = $("delete-details").open = false;
  $("prompt").disabled = $("send").disabled = $("mic").disabled = false;
  $("chat-form").setAttribute("aria-busy", "false");
  $("send").replaceChildren(icon("arrow"));
  plannerOffset = searchOffset = 0;
  view = "assistant";
  range = "today";
  $("kind-filter").value = "";
  $("status-filter").value = "active";
  $("status-filter").disabled = false;
  $("range-start").value = $("range-end").value = "";
  $("custom-range").hidden = true;
  document
    .querySelectorAll("[data-range]")
    .forEach((el) => el.classList.toggle("active", el.dataset.range === range));
  document
    .querySelectorAll(".view")
    .forEach((el) => (el.hidden = el.id !== "view-assistant"));
  document.querySelectorAll(".nav").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
    if (el.dataset.view === view) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("page-label").textContent = "Ассистент";
}
function showAuth(mode = "login", message = "") {
  clearPrivate();
  document.body.classList.remove("booting", "locked", "recovery-screen");
  document.body.classList.add("auth-screen");
  $("auth-panel").hidden = false;
  setAuthMode(mode);
  $("auth-error").textContent = message;
}
function setAuthMode(mode) {
  authMode = mode;
  $("auth-form").reset();
  $("auth-error").textContent = "";
  $("auth-password").type = "password";
  $("auth-password-show").textContent = "Показать";
  $("auth-password-show").setAttribute("aria-pressed", "false");
  $("auth-recovery-field").hidden = mode !== "recover";
  $("auth-recovery").required = mode === "recover";
  $("auth-confirm-field").hidden = mode === "login";
  $("auth-confirm").required = mode !== "login";
  $("auth-password").minLength = mode === "login" ? 1 : 12;
  $("auth-password").autocomplete =
    mode === "login" ? "current-password" : "new-password";
  $("auth-password-hint").hidden = mode === "login";
  $("auth-no-email").hidden = mode === "login";
  $("auth-title").textContent = {
    login: "С возвращением",
    register: "Твои планы начинаются здесь",
    recover: "Вернём доступ",
  }[mode];
  $("auth-description").textContent = {
    login: "Войди, чтобы продолжить свои планы.",
    register: "Придумай псевдоним. Почта не понадобится.",
    recover: "Введи сохранённый код и придумай новый пароль.",
  }[mode];
  $("auth-password-label").textContent =
    mode === "recover" ? "Новый пароль" : "Пароль";
  $("auth-submit").textContent = {
    login: "Войти в Орбиту ↗",
    register: "Создать личное пространство ↗",
    recover: "Восстановить доступ ↗",
  }[mode];
  $("auth-recover").textContent =
    mode === "recover" ? "Вернуться ко входу" : "Не помню пароль";
  document
    .querySelectorAll("[data-auth-mode]")
    .forEach((el) =>
      el.setAttribute("aria-pressed", String(el.dataset.authMode === mode)),
    );
}
async function authRequest(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? "GET" : "POST",
    headers: headers(data !== undefined),
    credentials: "same-origin",
    cache: "no-store",
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(25000),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      result.error || "Не удалось выполнить действие. Попробуй ещё раз.",
    );
  return result;
}
function showRecovery(code) {
  if (typeof code !== "string" || !code)
    throw new Error(
      "Сервер не вернул код восстановления. Не закрывай страницу и обратись к владельцу сервера.",
    );
  clearPrivate();
  document.body.classList.remove("booting", "locked", "auth-screen");
  document.body.classList.add("recovery-screen");
  $("auth-panel").hidden = true;
  $("recovery-code").textContent = code;
  $("recovery-copy-status").textContent = "";
  $("recovery-panel").hidden = false;
  $("recovery-copy").focus();
}
document
  .querySelectorAll("[data-auth-mode]")
  .forEach((el) => (el.onclick = () => setAuthMode(el.dataset.authMode)));
$("auth-recover").onclick = () =>
  setAuthMode(authMode === "recover" ? "login" : "recover");
$("auth-password-show").onclick = () => {
  const visible = $("auth-password").type === "password";
  $("auth-password").type = visible ? "text" : "password";
  $("auth-password-show").textContent = visible ? "Скрыть" : "Показать";
  $("auth-password-show").setAttribute("aria-pressed", String(visible));
};
$("auth-form").onsubmit = async (event) => {
  event.preventDefault();
  if ($("auth-submit").disabled) return;
  if (
    authMode !== "login" &&
    $("auth-password").value !== $("auth-confirm").value
  ) {
    $("auth-error").textContent = "Пароли не совпадают.";
    $("auth-confirm").focus();
    return;
  }
  const mode = authMode;
  const data = {
    username: $("auth-username").value.trim(),
    password: $("auth-password").value,
  };
  if (mode === "recover") data.recovery_code = $("auth-recovery").value.trim();
  $("auth-submit").disabled = true;
  $("auth-form").setAttribute("aria-busy", "true");
  $("auth-error").textContent = "";
  document
    .querySelectorAll("[data-auth-mode],#auth-recover")
    .forEach((el) => (el.disabled = true));
  try {
    const result = await authRequest("/api/auth/" + mode, data);
    $("auth-form").reset();
    identityChannel?.postMessage("changed");
    if (mode !== "login") showRecovery(result.recovery_code);
    else {
      clearPrivate();
      await init();
    }
  } catch (error) {
    $("auth-password").value =
      $("auth-confirm").value =
      $("auth-recovery").value =
        "";
    $("auth-error").textContent =
      error instanceof TypeError
        ? "Нет связи с сервером. Проверь соединение и попробуй снова."
        : error.message;
  } finally {
    $("auth-submit").disabled = false;
    $("auth-form").setAttribute("aria-busy", "false");
    document
      .querySelectorAll("[data-auth-mode],#auth-recover")
      .forEach((el) => (el.disabled = false));
  }
};
$("recovery-copy").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("recovery-code").textContent);
    $("recovery-copy-status").textContent =
      "Скопировано. Сохрани код в безопасном месте.";
  } catch {
    $("recovery-copy-status").textContent =
      "Выдели код и скопируй его вручную.";
  }
};
$("recovery-saved").onchange = () =>
  ($("recovery-continue").disabled = !$("recovery-saved").checked);
$("recovery-continue").onclick = async () => {
  if (!$("recovery-saved").checked) return;
  $("recovery-code").textContent = "";
  $("recovery-panel").hidden = true;
  document.body.classList.remove("recovery-screen");
  await init();
};
$("password-form").onsubmit = async (event) => {
  event.preventDefault();
  if ($("password-submit").disabled) return;
  if ($("new-password").value !== $("new-password-confirm").value) {
    $("password-error").textContent = "Пароли не совпадают.";
    return;
  }
  $("password-submit").disabled = true;
  $("password-error").textContent = "";
  try {
    const result = await api("/api/auth/password", {
      current_password: $("current-password").value,
      password: $("new-password").value,
    });
    $("password-form").reset();
    identityChannel?.postMessage("changed");
    showRecovery(result.recovery_code);
  } catch (error) {
    if (error.stale) return;
    $("password-error").textContent = error.message;
  } finally {
    $("password-submit").disabled = false;
  }
};
$("delete-account-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!$("delete-confirmed").checked || $("delete-account-submit").disabled)
    return;
  $("delete-account-submit").disabled = true;
  $("delete-error").textContent = "";
  try {
    await api("/api/auth/delete", {
      password: $("delete-password").value,
      confirmed: true,
    });
    identityChannel?.postMessage("changed");
    showAuth("register", "Аккаунт и его данные удалены.");
  } catch (error) {
    if (error.stale) return;
    $("delete-error").textContent = error.message;
    $("delete-password").value = "";
  } finally {
    $("delete-account-submit").disabled = false;
  }
};
$("use-ai").onchange = async () => {
  const enabled = $("use-ai").checked;
  $("use-ai").disabled = true;
  try {
    await api("/api/account", { use_ai: enabled });
    config = await api("/api/config");
    renderPrivacy();
    renderAIState();
  } catch (error) {
    if (error.stale) return;
    $("use-ai").checked = !enabled;
    toast(error.message);
  } finally {
    $("use-ai").disabled = false;
  }
};
function renderPrivacy() {
  $("encryption-state").textContent = config.privacy?.encrypted
    ? "Шифрование при хранении включено. Данные защищены ключом сервера."
    : "Шифрование при хранении не включено на этом сервере.";
  $("account-card").hidden = isMini || !config.account?.username;
  $("settings-username").textContent = config.account?.username || "";
  $("use-ai-label").hidden = $("use-ai-state").hidden =
    typeof config.account?.use_ai !== "boolean";
  $("use-ai").checked = config.account?.use_ai === true;
  $("use-ai-state").textContent = config.account?.use_ai
    ? config.privacy?.ai_enabled
      ? "ИИ включён. Запрос и нужный контекст отправляются провайдеру."
      : "Разрешение включено, но провайдер сейчас недоступен."
    : "ИИ выключен. Текстовые команды обрабатываются локально, без отправки провайдеру модели.";
}
let historyLoaded = false,
  historyLoading = null,
  refreshLoading = null;
let theme = prefs.get("theme", "light");
if (isMini && tg) theme = tg.colorScheme || theme;
document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
const types = { task: "Задача", meeting: "Встреча", reminder: "Напоминание" };
function haptic(type = "success") {
  try {
    tg?.HapticFeedback?.notificationOccurred(type);
  } catch {}
}
function toast(text, action = null) {
  if (document.body.matches(".auth-screen,.recovery-screen,.locked")) return;
  $("toast-text").textContent = text;
  $("toast").hidden = false;
  $("toast-action").hidden = !action;
  $("toast-action").textContent = action?.label || "";
  $("toast-action").onclick = action?.run || null;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(
    () => ($("toast").hidden = true),
    action ? 15000 : 6000,
  );
}
$("toast-close").onclick = () => ($("toast").hidden = true);
function ask(title, text, yes = "Подтвердить", danger = false) {
  return new Promise((resolve) => {
    const d = $("confirm-dialog");
    if (d.open) {
      resolve(false);
      return;
    }
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-yes").textContent = yes;
    $("confirm-yes").classList.toggle("danger", danger);
    d.returnValue = "";
    d.addEventListener("close", () => resolve(d.returnValue === "yes"), {
      once: true,
    });
    d.showModal();
    $("confirm-no").focus();
  });
}
$("confirm-yes").onclick = () => $("confirm-dialog").close("yes");
$("confirm-no").onclick = () => $("confirm-dialog").close("no");
function lock(message) {
  clearPrivate();
  $("auth-panel").hidden = true;
  document.body.classList.remove("auth-screen", "recovery-screen");
  document.body.classList.remove("booting");
  document.body.classList.add("locked");
  $("access-title").textContent = isMini
    ? "Открой Орбиту\nв Telegram"
    : "Не удалось подключиться";
  $("access-text").textContent = message;
  $("access-link").hidden = false;
  $("access-link").textContent =
    !isMini && $("access-link").onclick
      ? "Открыть веб-пространство"
      : "Перейти на сайт";
}
function headers(json = false) {
  const h = { "X-Orbita-Client": isMini ? "mini" : "web" };
  if (json) h["Content-Type"] = "application/json";
  if (token) h.Authorization = "Bearer " + token;
  return h;
}
async function api(path, data, options = {}) {
  const version = identityVersion;
  const controller = new AbortController();
  requests.add(controller);
  const post = data !== undefined;
  const key = post ? crypto.randomUUID() : null;
  const h = headers(post);
  if (post) h["Idempotency-Key"] = key;
  let r;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (version !== identityVersion) {
      requests.delete(controller);
      throw staleResponse();
    }
    try {
      r = await fetch(path, {
        method: post ? "POST" : "GET",
        headers: h,
        credentials: isMini ? "omit" : "same-origin",
        body: post ? JSON.stringify(data) : undefined,
        cache: "no-store",
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(65000),
        ]),
      });
      break;
    } catch (err) {
      if (version !== identityVersion) {
        requests.delete(controller);
        throw staleResponse();
      }
      if (
        attempt === 0 &&
        post &&
        !["/api/link", "/api/logout"].includes(path) &&
        !path.startsWith("/api/auth/")
      ) {
        await new Promise((r) => setTimeout(r, 400));
        continue;
      }
      requests.delete(controller);
      throw new Error(
        "Нет связи с сервером. Запрос не повторён с новым ID; проверь соединение.",
      );
    }
  }
  let result;
  try {
    result = await r.json();
  } catch {
    requests.delete(controller);
    if (version !== identityVersion) throw staleResponse();
    throw new Error("Неожиданный ответ сервера. Попробуй обновить страницу.");
  }
  requests.delete(controller);
  if (version !== identityVersion) throw staleResponse();
  if (!r.ok) {
    if (
      r.status === 401 &&
      (!path.startsWith("/api/auth/") ||
        ["unauthorized", "session_expired"].includes(result.code))
    ) {
      if (isMini)
        lock(
          "Сессия истекла. Закрой Mini App и снова открой его кнопкой бота.",
        );
      else if (result.code === "unauthorized" || authRequired) {
        showAuth(
          "login",
          "Сессия завершена. Войди снова, чтобы открыть свои планы.",
        );
      } else {
        lock(result.error || "Привязка пространства истекла.");
        $("access-link").textContent = "Открыть веб-пространство";
        $("access-link").onclick = async (ev) => {
          ev.preventDefault();
          try {
            const response = await fetch("/api/logout", {
              method: "POST",
              headers: headers(true),
              credentials: "same-origin",
              body: "{}",
            });
            if (!response.ok)
              throw new Error(
                "Не удалось открыть веб-пространство. Попробуй ещё раз.",
              );
            location.assign("/");
          } catch (err) {
            $("access-text").textContent = err.message;
          }
        };
      }
    }
    const error = new Error(result.error || "Не удалось выполнить действие");
    if (version !== identityVersion) error.stale = true;
    error.status = r.status;
    error.details = result;
    throw error;
  }
  return result;
}
function today(zone = timezone) {
  const p = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (k) => p.find((x) => x.type === k).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}
function shift(date, n) {
  const d = new Date(date + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
function pretty(d) {
  return d
    ? new Date(d + "T12:00:00Z").toLocaleDateString("ru-RU", {
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      })
    : "Без даты";
}
function meta(e) {
  return `${pretty(e.date)}${e.time ? " · " + e.time : ""}${e.kind === "meeting" ? " · " + e.duration + " мин" : ""}${e.timezone !== timezone ? " · " + e.timezone : ""}`;
}
function labels() {
  const heading = $("assistant-heading");
  if (heading) {
    const text = new Date().toLocaleDateString("ru-RU", {
      weekday: "long",
      day: "numeric",
      month: "long",
      timeZone: timezone,
    });
    heading.textContent = text[0].toUpperCase() + text.slice(1);
  }
  $("date-label").textContent = new Date().toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: timezone,
  });
  $("today-date").textContent = new Date().toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: timezone,
  });
  if (![...$("timezone").options].some((x) => x.value === timezone))
    $("timezone").append(new Option(timezone, timezone));
  $("timezone").value = timezone;
  $("speak-toggle").setAttribute("aria-pressed", String(speaking));
}
function setView(name) {
  if (!privateReady) return;
  view = name;
  document
    .querySelectorAll(".view")
    .forEach((e) => (e.hidden = e.id !== "view-" + name));
  document.querySelectorAll(".nav").forEach((e) => {
    e.classList.toggle("active", e.dataset.view === name);
    if (e.dataset.view === name) e.setAttribute("aria-current", "page");
    else e.removeAttribute("aria-current");
  });
  $("page-label").textContent = {
    assistant: "Ассистент",
    planner: "Мои планы",
    archive: "Поиск",
    settings: "Настройки",
  }[name];
  if (!isMini && matchMedia("(max-width:760px)").matches)
    $("channel-label").textContent =
      "Орбита · " +
      {
        assistant: "ассистент",
        planner: "мои планы",
        archive: "поиск",
        settings: "настройки",
      }[name];
  if (isMini) {
    $("channel-label").textContent = {
      assistant: "Орбита · ассистент",
      planner: "Орбита · мои планы",
      archive: "Орбита · поиск",
      settings: "Мои записи",
    }[name];
    try {
      name === "planner" ? tg?.BackButton?.hide() : tg?.BackButton?.show();
    } catch {}
  }
  if (name === "planner") refresh();
  if (name === "settings") loadNotifications();
  if (name === "assistant") {
    loadHistory();
    refresh();
  }
  if (name === "archive" && !isMini) $("search").focus();
  window.scrollTo({ top: 0, behavior: "auto" });
}
window.addEventListener("resize", () => {
  if (!privateReady || isMini) return;
  $("channel-label").textContent = matchMedia("(max-width:760px)").matches
    ? "Орбита · " +
      {
        assistant: "ассистент",
        planner: "мои планы",
        archive: "поиск",
        settings: "настройки",
      }[view]
    : "Орбита";
});
document
  .querySelectorAll("[data-view]")
  .forEach((e) => (e.onclick = () => setView(e.dataset.view)));
function toggleTheme() {
  const dark =
    document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme &&
      matchMedia("(prefers-color-scheme:dark)").matches);
  theme = dark ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  prefs.set("theme", theme);
  try {
    tg?.setHeaderColor(dark ? "#ffffff" : "#1c2738");
    tg?.setBackgroundColor(dark ? "#f6f8fc" : "#151c29");
  } catch {}
}
$("theme").onclick = toggleTheme;
$("settings-theme").onclick = toggleTheme;
$("auth-theme").onclick = toggleTheme;
function empty(target, title, text, create = false) {
  target.replaceChildren();
  const box = node("div", "empty-state"),
    mark = node("div", "empty-symbol");
  mark.append(icon("spark"));
  box.append(mark, node("strong", "", title), node("p", "", text));
  if (create) {
    const b = node("button", "primary", "Добавить запись");
    b.onclick = () => openEvent();
    box.append(b);
  }
  target.append(box);
}
function addMessage(role, text, events = [], actions = []) {
  if (document.body.matches(".auth-screen,.recovery-screen,.locked")) return;
  const row = node("div", "message " + role),
    bubble = node("div", "bubble");
  if (role === "assistant") {
    const mark = node("div", "tiny-avatar");
    mark.append(icon("spark"));
    row.append(mark);
    bubble.append(node("div", "message-heading", "Орбита"));
  }
  bubble.append(node("p", "", text));
  if (Array.isArray(events) && events.length) {
    const list = node("div", "inline-events");
    for (const e of events) {
      const b = node("button", "inline-event");
      b.type = "button";
      b.append(
        node("strong", "", e.title),
        node("small", "", `#${e.id} · ${meta(e)}`),
      );
      b.onclick = () => openEvent(e);
      list.append(b);
    }
    bubble.append(list);
  }
  if (role === "assistant" && Array.isArray(actions)) {
    const list = node("div", "chat-actions");
    for (const action of actions) {
      if (
        typeof action?.label !== "string" ||
        typeof action?.text !== "string" ||
        !action.text.trim()
      )
        continue;
      const b = node("button", "secondary", action.label);
      b.type = "button";
      b.onclick = () => submitChat(action.text, action.revision);
      list.append(b);
    }
    if (list.childElementCount) bubble.append(list);
  }
  row.append(bubble);
  $("messages").append(row);
  requestAnimationFrame(
    () => ($("messages").scrollTop = $("messages").scrollHeight),
  );
}
function welcome() {
  $("messages").replaceChildren();
  addMessage(
    "assistant",
    "Что нужно записать? Например: «Завтра в 15 созвон с командой».",
    [],
    [{ label: "Мои планы сегодня", text: "Что у меня сегодня?" }],
  );
}
async function loadHistory() {
  if (historyLoaded) return;
  if (historyLoading) return historyLoading;
  const currentSession = session;
  historyLoading = (async () => {
    try {
      const r = await api(
        "/api/history?session=" + encodeURIComponent(currentSession),
      );
      if (session !== currentSession) return;
      historyLoaded = true;
      if (r.messages.length) {
        $("messages").replaceChildren();
        let last = -1;
        r.messages.forEach((m, i) => {
          if (m.role === "assistant") last = i;
        });
        r.messages.forEach((m, i) =>
          addMessage(
            m.role,
            m.text,
            m.events,
            i === last ? r.actions || m.actions : [],
          ),
        );
        if (last < 0 && r.actions?.length)
          addMessage("assistant", "Продолжим?", [], r.actions);
      } else welcome();
    } catch (e) {
      if (e.stale) return;
      toast(e.message);
    } finally {
      if (session === currentSession) historyLoading = null;
    }
  })();
  return historyLoading;
}
function speak(text) {
  if (!speaking || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 2000));
  utterance.lang = "ru-RU";
  window.speechSynthesis.speak(utterance);
}
$("speak-toggle").onclick = () => {
  speaking = !speaking;
  prefs.set("speak", speaking);
  labels();
  if (!speaking) window.speechSynthesis?.cancel();
  toast(speaking ? "Ответы будут озвучиваться" : "Озвучивание выключено");
};
async function submitChat(text, revision) {
  if (!privateReady) return;
  const version = identityVersion;
  if (
    busy ||
    starting ||
    recognition ||
    recorder?.state === "recording" ||
    !text.trim()
  )
    return;
  busy = true;
  document.querySelectorAll(".chat-actions").forEach((el) => el.remove());
  ["send", "prompt", "mic"].forEach((id) => ($(id).disabled = true));
  $("chat-form").setAttribute("aria-busy", "true");
  $("send").setAttribute("aria-label", "Отправляю сообщение");
  $("send").replaceChildren(node("span", "send-spinner"));
  await loadHistory();
  if (version !== identityVersion) return;
  document.querySelectorAll(".chat-actions").forEach((el) => el.remove());
  addMessage("user", text);
  $("prompt").value = "";
  $("prompt").style.height = "auto";
  $("chat-notice").hidden = false;
  $("chat-notice").textContent = "Секунду…";
  $("mode-label").textContent = "Обработка сообщения…";
  try {
    const r = await api("/api/chat", {
      text,
      session,
      ...(Number.isInteger(revision) ? { expected_revision: revision } : {}),
    });
    addMessage("assistant", r.reply, r.events, r.actions);
    renderAIState(r.mode, r.notice, r.model);
    $("chat-notice").hidden = !r.notice;
    $("chat-notice").textContent = r.notice
      ? r.mode === "local"
        ? "Сейчас доступны простые команды. Попробуй указать действие, дату и время."
        : "Ответ готов. Подробности работы — в настройках."
      : "";
    speak(r.reply);
    if (r.changed) haptic();
    await refresh();
  } catch (e) {
    if (version !== identityVersion) return;
    $("connection").textContent = "Запрос не выполнен";
    $("mode-label").textContent = "Не удалось получить ответ";
    addMessage(
      "assistant",
      e.message + " Проверь планировщик перед повторной отправкой.",
    );
    $("chat-notice").hidden = true;
    haptic("error");
  } finally {
    if (version !== identityVersion) return;
    busy = false;
    ["send", "prompt"].forEach((id) => ($(id).disabled = false));
    $("chat-form").setAttribute("aria-busy", "false");
    $("send").setAttribute("aria-label", "Отправить сообщение");
    $("send").replaceChildren(icon("arrow"));
    $("mic").disabled = transcribing;
    if (!isMini) $("prompt").focus();
  }
}
$("chat-form").onsubmit = (e) => {
  e.preventDefault();
  submitChat($("prompt").value.trim());
};
$("prompt").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("chat-form").requestSubmit();
  }
};
$("prompt").oninput = () => {
  $("prompt").style.height = "auto";
  $("prompt").style.height = Math.min($("prompt").scrollHeight, 120) + "px";
};
document.querySelectorAll("[data-prompt]").forEach(
  (b) =>
    (b.onclick = () => {
      if (busy) return;
      setView("assistant");
      $("prompt").value = b.dataset.prompt;
      $("prompt").focus();
    }),
);
async function refresh() {
  if (!privateReady) return;
  labels();
  if (view === "settings") return loadNotifications();
  if (view === "planner") {
    await loadNotifications();
    return loadPlanner();
  }
  if (view !== "assistant" || isMini) return;
  if (refreshLoading) return refreshLoading;
  const version = identityVersion;
  refreshLoading = (async () => {
    try {
      const desktop = !matchMedia("(max-width:760px)").matches;
      const [s, r] = await Promise.all([
        api("/api/stats?tz=" + encodeURIComponent(timezone)),
        desktop
          ? api(
              "/api/events?start=" +
                today() +
                "&end=" +
                today() +
                "&status=active&limit=4",
            )
          : null,
      ]);
      $("stat-today").textContent = s.today;
      $("stat-meetings").textContent = s.meetings;
      $("stat-done").textContent = s.done;
      $("stat-inbox").textContent = s.inbox ?? 0;
      $("stat-overdue").textContent = s.overdue ?? 0;
      if (!r) return;
      $("today-list").replaceChildren();
      if (!r.events.length) {
        const box = node("div", "today-empty"),
          mark = node("div", "empty-symbol");
        mark.append(icon("calendar"));
        box.append(
          mark,
          node("strong", "", "На сегодня ничего нет"),
          node("p", "", "Новые задачи появятся здесь."),
        );
        const add = node("button", "subtle", "Запланировать дело");
        add.onclick = () => openEvent();
        box.append(add);
        $("today-list").append(box);
      } else
        for (const e of r.events) {
          const row = node("div", "today-item"),
            b = node("button", "", e.title);
          b.append(node("small", "", types[e.kind]));
          b.onclick = () => openEvent(e);
          row.append(node("span", "today-time", e.time || "День"), b);
          $("today-list").append(row);
        }
    } catch (e) {
      if (e.stale) return;
      toast(e.message);
    } finally {
      if (version === identityVersion) refreshLoading = null;
    }
  })();
  return refreshLoading;
}
function chooseRange(value) {
  range = value;
  document
    .querySelectorAll("[data-range]")
    .forEach((b) => b.classList.toggle("active", b.dataset.range === value));
  $("custom-range").hidden = value !== "custom";
  $("status-filter").disabled = ["inbox", "overdue"].includes(value);
}
function openToday(kind = "") {
  chooseRange("today");
  $("kind-filter").value = kind;
  $("status-filter").value = "active";
  setView("planner");
}
$("open-today").onclick = () => openToday();
$("all-today").onclick = () => openToday();
$("open-meetings").onclick = () => openToday("meeting");
$("open-done").onclick = () => {
  chooseRange("all");
  $("status-filter").value = "done";
  $("kind-filter").value = "";
  setView("planner");
};
async function saveWithConflict(data) {
  try {
    return await api("/api/events/save", data);
  } catch (e) {
    if (e.status !== 409 || !e.details.conflicts) throw e;
    if (
      !(await ask(
        "Время уже занято",
        e.details.conflicts
          .map((x) => "• " + x.title + " · " + x.time)
          .join("\n") + "\n\nСохранить ещё одну встречу на это время?",
        "Всё равно сохранить",
      ))
    )
      return null;
    return api("/api/events/save", { ...data, confirm_conflicts: true });
  }
}
async function restore(id) {
  try {
    try {
      await api("/api/events/restore", { id });
    } catch (e) {
      if (
        !e.details?.conflicts ||
        !(await ask("Есть пересечение", e.message, "Восстановить"))
      )
        throw e;
      await api("/api/events/restore", { id, confirm_conflicts: true });
    }
    toast("Запись восстановлена");
    haptic();
    await refreshLists();
    if (!$("trash-list").hidden) await loadTrash();
  } catch (e) {
    toast(e.message);
  }
}
function card(e) {
  const el = node("article", "record" + (e.status === "done" ? " done" : "")),
    top = node("div", "record-top");
  top.append(
    node("span", "badge " + e.kind, types[e.kind]),
    node("span", "record-id", "#" + e.id),
  );
  el.append(top, node("h3", "", e.title), node("div", "record-meta", meta(e)));
  const actions = node("div", "record-actions");
  const done = node(
    "button",
    "",
    e.status === "done" ? "↶ Вернуть" : "✓ Готово",
  );
  done.onclick = async () => {
    done.disabled = true;
    try {
      const r = await saveWithConflict({
        ...e,
        status: e.status === "done" ? "active" : "done",
      });
      if (r) {
        haptic();
        await refreshLists();
      }
    } catch (err) {
      toast(err.message);
      await refreshLists();
    } finally {
      done.disabled = false;
    }
  };
  const edit = node("button", "", "Изменить");
  edit.onclick = () => openEvent(e);
  const del = node("button", "danger", "Удалить");
  del.onclick = async () => {
    if (
      !(await ask(
        "Удалить запись?",
        e.title + "\n\nУдаление можно отменить в течение 10 минут.",
        "Удалить",
        true,
      ))
    )
      return;
    try {
      await api("/api/events/delete", {
        id: e.id,
        revision: e.revision,
        confirmed: true,
      });
      haptic();
      toast("Запись удалена", { label: "Отменить", run: () => restore(e.id) });
      await refreshLists();
    } catch (err) {
      toast(err.message);
    }
  };
  actions.append(done, edit);
  if (e.status === "active") {
    const tomorrow = node("button", "", "На завтра");
    tomorrow.onclick = async () => {
      tomorrow.disabled = true;
      try {
        const saved = await saveWithConflict({
          ...e,
          date: shift(today(e.timezone), 1),
          revision: e.revision,
        });
        if (saved) {
          toast("Перенесено на завтра · " + e.timezone);
          await refreshLists();
        }
      } catch (err) {
        toast(err.message);
        await refreshLists();
      } finally {
        tomorrow.disabled = false;
      }
    };
    actions.append(tomorrow);
  }
  actions.append(del);
  el.append(actions);
  appendDelivery(el, e);
  return el;
}
async function loadPlanner(more = false) {
  const generation = ++plannerGeneration;
  let start, end;
  const d = today();
  if (range === "today") start = end = d;
  else if (range === "tomorrow") start = end = shift(d, 1);
  else if (range === "week") {
    const day = new Date(d + "T12:00:00Z").getUTCDay();
    start = shift(d, -((day + 6) % 7));
    end = shift(start, 6);
  } else if (range === "custom") {
    start = $("range-start").value;
    end = $("range-end").value;
    if (start && end && start > end) {
      $("planner-count").textContent =
        "Проверьте даты: начало периода позже конца.";
      return;
    }
  }
  if (!more) plannerOffset = 0;
  const q = new URLSearchParams({ limit: "30", offset: String(plannerOffset) });
  if (range === "inbox" || range === "overdue") {
    q.set("bucket", range);
    q.set("status", "active");
  }
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  if ($("kind-filter").value) q.set("kind", $("kind-filter").value);
  if (!["inbox", "overdue"].includes(range) && $("status-filter").value)
    q.set("status", $("status-filter").value);
  $("planner-more").disabled = true;
  try {
    const r = await api("/api/events?" + q);
    if (generation !== plannerGeneration) return;
    $("planner-count").textContent =
      `Найдено: ${r.total} · показано ${Math.min(plannerOffset + r.events.length, r.total)}`;
    if (!more) $("planner-list").replaceChildren();
    appendPlanGroups(r.events);
    plannerOffset += r.events.length;
    $("planner-more").hidden = !r.has_more;
    if (!r.events.length && !more)
      empty(
        $("planner-list"),
        range === "overdue"
          ? "Всё под контролем"
          : range === "inbox"
            ? "Все дела с датой"
            : "Здесь пока свободно",
        range === "overdue"
          ? "Просроченных дел нет. Можно заняться следующими планами."
          : range === "inbox"
            ? "Новые идеи можно сохранить без даты и запланировать позже."
            : "Добавь задачу, встречу или напоминание.",
        true,
      );
  } catch (e) {
    toast(e.message);
  } finally {
    $("planner-more").disabled = false;
  }
}
document.querySelectorAll("[data-range]").forEach(
  (b) =>
    (b.onclick = () => {
      chooseRange(b.dataset.range);
      loadPlanner();
    }),
);
["kind-filter", "status-filter", "range-start", "range-end"].forEach(
  (id) => ($(id).onchange = () => loadPlanner()),
);
$("planner-more").onclick = () => loadPlanner(true);
$("planner-refresh").onclick = () => loadPlanner();
async function search(more = false) {
  const q = $("search").value.trim();
  const generation = ++searchGeneration;
  if (!more) searchOffset = 0;
  if (!q) {
    empty(
      $("search-list"),
      "Поиск по записям",
      "Введи тему или часть названия.",
    );
    $("search-more").hidden = true;
    return;
  }
  $("search-more").disabled = true;
  try {
    const r = await api(
      "/api/events?q=" +
        encodeURIComponent(q) +
        "&limit=30&offset=" +
        searchOffset,
    );
    if (generation !== searchGeneration) return;
    $("search-count").textContent =
      `Найдено: ${r.total} · показано ${Math.min(searchOffset + r.events.length, r.total)}`;
    if (!more) $("search-list").replaceChildren();
    $("search-list").append(...r.events.map(card));
    searchOffset += r.events.length;
    $("search-more").hidden = !r.has_more;
    if (!r.events.length && !more)
      empty(
        $("search-list"),
        "Пока ничего похожего",
        "Попробуй имя, тему или другое слово.",
      );
  } catch (e) {
    toast(e.message);
  } finally {
    $("search-more").disabled = false;
  }
}
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  clearTimeout(search.timer);
  search();
};
$("search").oninput = () => {
  clearTimeout(search.timer);
  search.timer = setTimeout(() => search(), 350);
};
$("search-more").onclick = () => search(true);
async function refreshLists() {
  await refresh();
  if (view === "archive") await search();
}
function fieldRules() {
  const required = $("event-kind").value !== "task";
  $("event-date").required = required;
  $("event-time").required = required;
  $("duration-label").hidden = $("event-kind").value !== "meeting";
}
$("event-kind").onchange = fieldRules;
function openEvent(e = null) {
  currentEvent = e;
  $("dialog-title").textContent = e ? "Изменить запись" : "Новая запись";
  $("event-title").value = e?.title || "";
  $("event-kind").value = e?.kind || "task";
  $("event-date").value = e?.date || "";
  $("event-time").value = e?.time || "";
  $("event-duration").value = e?.duration || 60;
  $("event-status").value = e?.status || "active";
  $("event-zone").textContent = "Часовой пояс: " + (e?.timezone || timezone);
  $("form-error").textContent = "";
  fieldRules();
  $("event-dialog").showModal();
  if (!isMini) $("event-title").focus();
}
["side-create", "today-add", "planner-add", "global-add"].forEach(
  (id) => ($(id).onclick = () => openEvent()),
);
["dialog-close", "dialog-cancel"].forEach(
  (id) => ($(id).onclick = () => $("event-dialog").close()),
);
$("event-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const d = {
    title: $("event-title").value,
    kind: $("event-kind").value,
    date: $("event-date").value || null,
    time: $("event-time").value || null,
    duration: Number($("event-duration").value),
    status: $("event-status").value,
    timezone: currentEvent?.timezone || timezone,
  };
  if (currentEvent) {
    d.id = currentEvent.id;
    d.revision = currentEvent.revision;
  }
  $("event-save").disabled = true;
  try {
    const r = await saveWithConflict(d);
    if (!r) return;
    $("event-dialog").close();
    haptic();
    toast("Запись сохранена");
    await refreshLists();
  } catch (e) {
    $("form-error").textContent = e.message;
  } finally {
    $("event-save").disabled = false;
  }
};
$("timezone").onchange = async () => {
  try {
    await api("/api/account", { timezone: $("timezone").value });
    timezone = $("timezone").value;
    await refresh();
    toast("Часовой пояс обновлён на сайте и в боте");
  } catch (e) {
    toast(e.message);
  }
};
$("notify-toggle").onchange = async () => {
  try {
    await api("/api/account", { notify: $("notify-toggle").checked });
    config.account.notify = $("notify-toggle").checked;
    await loadNotifications();
    toast(
      $("notify-toggle").checked
        ? "Напоминания включены. Статус доставки — в настройках."
        : "Напоминания выключены",
    );
  } catch (e) {
    $("notify-toggle").checked = !$("notify-toggle").checked;
    toast(e.message);
  }
};
$("link-form").onsubmit = async (e) => {
  e.preventDefault();
  if (
    !(await ask(
      config.account?.username
        ? "Подключить Telegram?"
        : "Переключить аккаунт?",
      config.account?.username
        ? "Записи твоего Telegram будут перенесены в этот аккаунт. После подключения сайт, бот и Mini App будут работать с общими планами."
        : "Сайт покажет записи Telegram-аккаунта. Текущие записи останутся в отдельном веб-пространстве. Автоматического объединения нет.",
      "Связать аккаунт",
    ))
  )
    return;
  try {
    const registered = Boolean(config.account?.username);
    await api("/api/link", { code: $("link-code").value.trim() });
    clearPrivate();
    identityChannel?.postMessage("changed");
    if (registered) await init();
    else {
      document.body.classList.add("booting");
      location.reload();
    }
  } catch (err) {
    $("link-error").textContent = err.message;
  }
};
$("import-data").onclick = async () => {
  if (
    !(await ask(
      "Добавить данные задания?",
      "Импортируем 1000 записей только в текущий аккаунт. Оригинальные даты сохранятся. Повторный импорт не создаёт дубликаты.",
      "Импортировать",
    ))
  )
    return;
  $("import-data").disabled = true;
  try {
    const r = await api("/api/import", {});
    $("import-result").textContent = r.imported
      ? `Добавлено ${r.imported} записей. Их можно найти по слову «презентация».`
      : "Все исходные записи уже импортированы.";
    await refresh();
  } catch (e) {
    $("import-result").textContent = e.message;
  } finally {
    $("import-data").disabled = false;
  }
};
async function download(format) {
  if (!privateReady) return;
  const version = identityVersion;
  if (isMini && config.telegram.username) {
    try {
      tg.openTelegramLink(
        "https://t.me/" + config.telegram.username + "?start=export",
      );
      return;
    } catch {}
  }
  try {
    const r = await fetch("/api/export?format=" + format, {
      headers: headers(),
      credentials: isMini ? "omit" : "same-origin",
    });
    if (!r.ok) throw new Error("Не удалось скачать экспорт.");
    const blob = await r.blob();
    if (version !== identityVersion) return;
    const url = URL.createObjectURL(blob),
      a = node("a");
    a.href = url;
    a.download = "orbita-events." + format;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    if (isMini) toast("Если WebView не скачивает файл, отправь /export боту.");
  } catch (e) {
    toast(e.message);
  }
}
async function loadTrash() {
  try {
    const r = await api("/api/trash");
    $("trash-list").hidden = false;
    $("trash-list").replaceChildren();
    if (!r.events.length) {
      $("trash-list").append(node("p", "muted", "В корзине пусто."));
      return;
    }
    for (const e of r.events) {
      const row = node("div", "trash-row"),
        button = node("button", "secondary", "Восстановить");
      button.onclick = () => restore(e.id);
      row.append(node("span", "", e.title), button);
      $("trash-list").append(row);
    }
  } catch (err) {
    toast(err.message);
  }
}
$("open-trash").onclick = loadTrash;
$("export-json").onclick = () => download("json");
$("export-csv").onclick = () => download("csv");
$("new-chat").onclick = async () => {
  if (
    !(await ask(
      "Начать новый диалог?",
      "Все записи останутся. Новый разговор начнётся без контекста старого.",
      "Начать заново",
    ))
  )
    return;
  session = crypto.randomUUID();
  prefs.set("session", session);
  historyLoaded = true;
  welcome();
  setView("assistant");
};
$("logout").onclick = async () => {
  if (isMini) return;
  if (authRequired || config.account?.username) {
    // Hide private DOM synchronously, before waiting for the cookie revocation.
    showAuth();
    $("auth-submit").disabled = true;
    try {
      await authRequest("/api/logout", {});
      identityChannel?.postMessage("changed");
    } catch {
      $("auth-error").textContent =
        "Данные скрыты, но сервер не подтвердил выход. Проверь соединение и повтори выход.";
    } finally {
      $("auth-submit").disabled = false;
    }
    return;
  }
  if (!config.account?.linked) return;
  if (
    !(await ask(
      "Отвязать Telegram от сайта?",
      "Сайт вернётся к общему веб-пространству. Записи Telegram сохранятся в боте и Mini App.",
      "Отвязать",
    ))
  )
    return;
  $("logout").disabled = true;
  clearPrivate();
  document.body.classList.add("booting");
  try {
    await authRequest("/api/logout", {});
    identityChannel?.postMessage("changed");
    location.reload();
  } catch (e) {
    lock(e.message);
  }
  $("logout").disabled = false;
};
let recognition = null,
  recorder = null,
  stream = null,
  recordTimer = null,
  starting = false,
  transcribing = false;
function stopStream() {
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
}
function voiceError(message) {
  toast(message);
  $("voice-status").textContent = message;
}
function voiceIdle() {
  clearTimeout(recordTimer);
  $("mic").classList.remove("recording");
  $("mic").setAttribute("aria-label", "Начать голосовой ввод");
  $("voice-status").textContent = "Enter — отправить · Shift + Enter — перенос";
  starting = false;
}
function voiceActive() {
  $("mic").classList.add("recording");
  $("mic").setAttribute("aria-label", "Остановить запись");
  $("voice-status").textContent = "Слушаю… Нажми микрофон, чтобы остановить.";
}
$("mic").onclick = async () => {
  if (!privateReady) return;
  const version = identityVersion;
  if (busy || starting || transcribing) return;
  if (recognition) {
    recognition.stop();
    return;
  }
  if (recorder?.state === "recording") {
    recorder.stop();
    return;
  }
  if (!window.isSecureContext) {
    voiceError("Для микрофона нужен localhost или HTTPS.");
    return;
  }
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (Speech && !isMini) {
    try {
      recognition = new Speech();
      recognition.lang = "ru-RU";
      recognition.interimResults = true;
      recognition.onresult = (e) => {
        if (version !== identityVersion) return;
        let text = "";
        for (let i = 0; i < e.results.length; i++)
          text += e.results[i][0].transcript;
        $("prompt").value = text;
      };
      let errorMessage = "";
      recognition.onerror = (e) => {
        errorMessage =
          e.error === "not-allowed"
            ? "Разреши доступ к микрофону в браузере."
            : e.error === "no-speech"
              ? "Речь не обнаружена. Попробуй ещё раз или введи текст."
              : "Браузерное распознавание недоступно. Введи текст.";
        voiceError(errorMessage);
      };
      recognition.onend = () => {
        recognition = null;
        voiceIdle();
        if (errorMessage) $("voice-status").textContent = errorMessage;
      };
      recognition.start();
      voiceActive();
      recordTimer = setTimeout(() => recognition?.stop(), 30000);
    } catch {
      recognition = null;
      voiceIdle();
      voiceError("Браузер не смог запустить распознавание. Введи текст.");
    }
    return;
  }
  if (!config.stt) {
    voiceError(
      "Серверное распознавание не включено для этого провайдера. Введи текст.",
    );
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    voiceError("Этот браузер не поддерживает запись. Введи текст.");
    return;
  }
  starting = true;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (document.hidden || version !== identityVersion) {
      stopStream();
      voiceIdle();
      return;
    }
    const mime = ["audio/webm", "audio/ogg", "audio/mp4"].find((m) =>
      MediaRecorder.isTypeSupported(m),
    );
    if (!mime) throw new Error("Аудиоформат не поддерживается. Введи текст.");
    recorder = new MediaRecorder(stream, { mimeType: mime });
    const chunks = [];
    let recordingFailed = false;
    recorder.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };
    recorder.onerror = () => {
      recordingFailed = true;
      stopStream();
      voiceIdle();
      voiceError("Ошибка записи микрофона. Попробуй ещё раз или введи текст.");
    };
    recorder.onstop = async () => {
      stopStream();
      voiceIdle();
      if (recordingFailed) {
        recorder = null;
        return;
      }
      if (!chunks.length || !chunks.some((c) => c.size)) {
        recorder = null;
        voiceError("Запись пустая. Попробуй ещё раз или введи текст.");
        return;
      }
      transcribing = true;
      const originalText = $("prompt").value;
      $("mic").disabled = true;
      $("voice-status").textContent = "Распознаю запись…";
      let errorMessage = "";
      try {
        const h = headers();
        h["Content-Type"] = mime;
        const r = await fetch("/api/transcribe", {
          method: "POST",
          headers: h,
          credentials: isMini ? "omit" : "same-origin",
          body: new Blob(chunks, { type: mime }),
          signal: AbortSignal.timeout(65000),
        });
        let data;
        try {
          data = await r.json();
        } catch {
          throw new Error("Сервер не вернул расшифровку. Введи текст.");
        }
        if (version !== identityVersion) return;
        if (r.status === 401) {
          lock("Сессия истекла. Открой приложение заново.");
          throw new Error("Сессия истекла.");
        }
        if (!r.ok)
          throw new Error(
            data.error || "Распознавание недоступно. Введи текст.",
          );
        if (typeof data.text !== "string" || !data.text.trim())
          throw new Error(
            "Речь не распознана. Попробуй ещё раз или введи текст.",
          );
        if ($("prompt").value !== originalText || busy) {
          addMessage(
            "assistant",
            "Расшифровка (не отправлена): " + data.text.trim(),
          );
          toast("Расшифровка добавлена в диалог; скопируй её для отправки.");
        } else {
          $("prompt").value = data.text.trim();
          $("prompt").dispatchEvent(new Event("input"));
          toast("Проверь расшифровку и нажми отправку");
        }
      } catch (e) {
        if (version !== identityVersion) return;
        errorMessage =
          e.name === "TimeoutError"
            ? "Сервер не успел распознать запись. Попробуй ещё раз или введи текст."
            : e instanceof TypeError
              ? "Нет связи с сервером распознавания. Введи текст."
              : e.message;
        toast(errorMessage);
      } finally {
        if (version !== identityVersion) return;
        transcribing = false;
        $("mic").disabled = busy;
        voiceIdle();
        if (errorMessage) $("voice-status").textContent = errorMessage;
        recorder = null;
      }
    };
    recorder.start();
    starting = false;
    voiceActive();
    recordTimer = setTimeout(() => {
      if (recorder?.state === "recording") recorder.stop();
    }, 30000);
  } catch (e) {
    if (version !== identityVersion) return;
    stopStream();
    recorder = null;
    voiceIdle();
    voiceError(
      e.name === "NotAllowedError"
        ? "Разреши доступ к микрофону в браузере."
        : e.name === "NotFoundError"
          ? "Микрофон не найден. Введи текст."
          : e.message || "Микрофон недоступен. Введи текст.",
    );
  }
};
window.addEventListener("beforeunload", () => {
  stream?.getTracks().forEach((t) => t.stop());
  recognition?.abort();
  window.speechSynthesis?.cancel();
});
window.addEventListener("offline", () =>
  toast("Нет подключения к сети. Изменения требуют работающего сервера."),
);
document.addEventListener("keydown", (e) => {
  if (!privateReady) return;
  const editing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    setView("archive");
    $("search").focus();
  } else if (
    !editing &&
    !$("event-dialog").open &&
    !$("confirm-dialog").open &&
    e.key.toLowerCase() === "n"
  ) {
    e.preventDefault();
    openEvent();
  }
});
function syncTelegramViewport() {
  if (tg?.viewportStableHeight)
    document.documentElement.style.setProperty(
      "--app-height",
      tg.viewportStableHeight + "px",
    );
}
async function init() {
  const version = identityVersion;
  document.body.classList.remove("auth-screen", "recovery-screen", "locked");
  document.body.classList.add("booting");
  $("auth-panel").hidden = true;
  $("recovery-panel").hidden = true;
  if (!isMini) {
    try {
      const status = await authRequest("/api/auth/status");
      if (version !== identityVersion) return;
      authRequired = status.required === true;
      if (authRequired && !status.authenticated) {
        showAuth();
        return;
      }
    } catch (error) {
      if (version !== identityVersion) return;
      lock("Не удалось проверить вход. Проверь соединение и обнови страницу.");
      return;
    }
  }
  welcome();
  if (!isMini && matchMedia("(max-width:760px)").matches)
    $("channel-label").textContent = "Орбита · ассистент";
  if (isMini) {
    if (!tg?.initData) {
      lock(
        "Нажми «Открыть планировщик» в своём Telegram-боте. Без подтверждённого входа записи недоступны.",
      );
      return;
    }
    try {
      const r = await fetch("/api/telegram/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "omit",
        body: JSON.stringify({ initData: tg.initData }),
      });
      const auth = await r.json();
      if (!r.ok) throw new Error(auth.error);
      token = auth.token;
      $("account-name").textContent = auth.name;
      tg.ready();
      tg.expand();
      tg.BackButton?.onClick(() => setView("planner"));
      tg.onEvent?.("viewportChanged", syncTelegramViewport);
      tg.onEvent?.("themeChanged", () => {
        document.documentElement.dataset.theme = tg.colorScheme;
      });
      syncTelegramViewport();
    } catch (e) {
      lock(e.message);
      return;
    }
  }
  try {
    config = await api("/api/config");
    if (version !== identityVersion) return;
    timezone = config.account.timezone;
    if (config.account.username)
      $("account-name").textContent = config.account.username;
    $("account-channel").textContent = config.account.private
      ? "Личное пространство"
      : config.account.linked
        ? "Связано с Telegram"
        : "Веб-версия";
    renderAIState();
    renderPrivacy();
    $("telegram-state").textContent = config.account.linked
      ? "Аккаунт связан: сайт, бот и Mini App видят одни и те же записи."
      : config.telegram.configured
        ? "Свяжи сайт с ботом — и продолжай с любого экрана."
        : "Telegram ещё не настроен. Владелец должен задать токен бота и HTTPS-адрес.";
    $("link-form").hidden =
      isMini || !config.telegram.configured || config.account.linked;
    $("link-description").textContent = config.account.username
      ? "Подключи только свой Telegram. Его записи будут перенесены в этот аккаунт; сайт, бот и Mini App будут работать с общими планами."
      : "Сайт переключится на записи Telegram-аккаунта. Текущие записи не удаляются и не объединяются автоматически.";
    $("notify-toggle").checked = config.account.notify;
    $("notify-toggle").disabled = !config.account.linked;
    $("notify-state").textContent = config.account.linked
      ? config.account.chat_connected
        ? config.telegram.worker_online
          ? "Бот работает. Доставка напоминаний доступна."
          : "Бот сейчас не на связи. Уведомления пока не отправляются."
        : "Для доставки открой бота и нажми /start."
      : "Сначала свяжи Telegram-аккаунт.";
    const username = config.telegram.username;
    if (/^[A-Za-z0-9_]{5,32}$/.test(username)) {
      $("open-bot").href = "https://t.me/" + username;
      $("open-bot").hidden = false;
    }
    $("logout").hidden =
      isMini || !(config.account.linked || config.account.username);
    $("logout").textContent = config.account.username
      ? "Выйти из аккаунта"
      : "Отвязать Telegram от сайта";
    $("logout").disabled = false;
    labels();
    privateReady = true;
    document.body.classList.remove("booting");
    if (isMini) setView("planner");
    else await Promise.all([loadHistory(), refresh()]);
  } catch (e) {
    if (version !== identityVersion || e.stale) return;
    if (isMini) lock(e.message);
    else lock(e.message);
  }
}
init();
if (identityChannel)
  identityChannel.onmessage = () => {
    if (isMini) return;
    clearPrivate();
    init();
  };
window.addEventListener("pageshow", (event) => {
  if (event.persisted && !isMini) {
    clearPrivate();
    init();
  }
});
// Lightweight cross-channel refresh; don't interrupt forms, speech, long lists or typing.
setInterval(() => {
  if (
    document.hidden ||
    busy ||
    starting ||
    transcribing ||
    recognition ||
    recorder ||
    document.body.matches(".booting,.locked") ||
    $("event-dialog").open ||
    $("confirm-dialog").open
  )
    return;
  if (view === "planner" && (plannerOffset > 30 || scrollY > 300)) return;
  if (
    /INPUT|TEXTAREA/.test(document.activeElement?.tagName) &&
    document.activeElement.value
  )
    return;
  refresh();
}, 30000);
function renderAIState(mode = null, notice = "", model = null) {
  const fallback = mode === "local" && Boolean(notice) && Boolean(config.llm);
  $("connection").textContent = fallback
    ? "Ограниченный режим"
    : "Пространство открыто";
  $("mode-label").textContent = fallback
    ? "Доступны простые команды"
    : mode
      ? "Можно добавить ещё запись"
      : config.llm
        ? "Добавление и поиск записей"
        : "Задачи, встречи и напоминания";
  $("ai-details").textContent =
    (config.llm
      ? "Модель " +
        (config.model || "провайдера") +
        " настроена. Доступность проверяется при запросе."
      : "Работает ограниченный локальный разбор без LLM.") +
    (fallback
      ? " Последний запрос обработан локально: " + notice
      : mode === "llm"
        ? " Последний ответ: " +
          (model || config.model || "LLM") +
          ". " +
          (notice || "")
        : mode === "local"
          ? " Последний ответ обработан локально."
          : "");
  $("voice-details").textContent =
    (config.stt
      ? "Сервер разрешает STT; успешное распознавание зависит от провайдера."
      : "Серверное STT не включено. Наличие LLM не означает поддержку голоса.") +
    " Микрофон требует HTTPS или localhost. Веб-версия также может использовать распознавание браузера. Расшифровка не отправляется автоматически.";
}

const deliveryLabels = {
  scheduled: "Запланировано",
  sent: "Отправлено в Telegram",
  pending: "В очереди",
  retry: "Повторная попытка",
  sending: "Отправляется",
  missed: "Время пропущено",
  failed: "Не удалось отправить",
  cancelled: "Отменено",
  disabled: "Напоминания выключены",
  not_connected: "Telegram не подключён",
  demo: "Учебная запись · без уведомлений",
};
async function loadNotifications() {
  const version = identityVersion;
  if (!config.account?.linked) {
    delivery = null;
    return;
  }
  try {
    delivery = await api("/api/notifications");
    if (!delivery.linked) {
      $("notify-state").textContent =
        "Telegram не связан. Подключи аккаунт для доставки.";
      return;
    }
    const counts = {};
    for (const n of delivery.notifications || [])
      counts[n.status] = (counts[n.status] || 0) + 1;
    const summary = Object.entries(counts)
      .map(
        ([status, count]) =>
          (deliveryLabels[status] || "Статус уточняется") + ": " + count,
      )
      .join(" · ");
    $("notify-state").textContent =
      (!config.account.notify
        ? "Напоминания выключены. "
        : delivery.worker_online
          ? "Сервис доставки на связи. "
          : "Сервис доставки сейчас недоступен. ") +
      (summary || "Запланированных напоминаний пока нет.") +
      " Отправка не подтверждает прочтение.";
  } catch (error) {
    if (version !== identityVersion || error.stale) return;
    $("notify-state").textContent =
      "Не удалось проверить доставку. Обнови раздел позже.";
    delivery = null;
  }
}
function appendDelivery(el, e) {
  if (e.kind !== "reminder" || !config.account?.linked || !delivery?.linked)
    return;
  const n = (delivery.notifications || []).find(
    (n) => String(n.event_id) === String(e.id),
  );
  if (!n) return;
  const line = node(
    "div",
    "delivery-status delivery-" + n.status,
    deliveryLabels[n.status] || "Статус уточняется",
  );
  if (n.due) {
    const date = new Date(n.due);
    if (!Number.isNaN(date.getTime()))
      line.title =
        "Доставка: " +
        date.toLocaleString("ru-RU", { timeZone: timezone }) +
        " · " +
        timezone;
  }
  if (
    e.status === "active" &&
    ["scheduled", "pending", "retry", "missed", "failed"].includes(n.status)
  ) {
    for (const minutes of [15, 60]) {
      const b = node(
        "button",
        "subtle",
        "Через " + (minutes === 60 ? "час" : "15 мин"),
      );
      b.onclick = async () => {
        b.disabled = true;
        try {
          await api("/api/events/snooze", {
            id: e.id,
            minutes,
            revision: e.revision,
          });
          toast("Напоминание перенесено");
          await refreshLists();
        } catch (err) {
          toast(err.message);
          await refreshLists();
        } finally {
          b.disabled = false;
        }
      };
      line.append(b);
    }
  }
  el.append(line);
}
function appendPlanGroups(events) {
  const list = $("planner-list");
  for (const e of events) {
    const key = e.date || "inbox";
    let group = [...list.children].find((g) => g.dataset.day === key);
    if (!group) {
      group = node("section", "day-group");
      group.dataset.day = key;
      const title =
        e.date === today()
          ? "Сегодня"
          : e.date === shift(today(), 1)
            ? "Завтра"
            : e.date
              ? pretty(e.date)
              : "Без даты";
      group.append(
        node("h2", "day-heading", title),
        node("div", "day-records"),
      );
      list.append(group);
    }
    group.lastElementChild.append(card(e));
  }
}
for (const bucket of ["inbox", "overdue"])
  $("open-" + bucket).onclick = () => {
    chooseRange(bucket);
    $("kind-filter").value = "";
    setView("planner");
  };
