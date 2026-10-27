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
  sliders: "M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1|c:15,6,2|c:9,12,2|c:17,18,2",
  question: "M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5M12 17h.01|c:12,12,10",
  note: "M6 3h8l4 4v14H6ZM14 3v4h4M9 12h6M9 16h4",
  home: "M4 11 12 4l8 7M6 9.5V20h12V9.5M10 20v-5h4v5",
  pin: "M9 3h6M10 3v6l-4 4h12l-4-4V3M12 13v8",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  minus: "M6 12h12",
  info: "M12 11v6M12 7.5h.01|c:12,12,9",
  "arrow-left": "M19 12H5M11 6l-6 6 6 6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  board: "r:3,4,5,16,1.5|r:10,4,5,11,1.5|r:17,4,4,7,1.5",
  graph: "M7 7l9 1M7 7l4 10M16 8l-5 9|c:6,6,3|c:18,7,3|c:11,19,3",
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
  plannerGeneration = 0,
  searchTimer = 0;
// A conversation handle is public, random metadata, never an authentication token.
if (!/^[a-f0-9-]{36}$/i.test(session)) session = crypto.randomUUID();
prefs.set("session", session);
let authRequired = false,
  authMode = "login",
  identityVersion = 0,
  privateReady = false;
let focusState = null,
  focusClockOffset = 0,
  focusPending = false,
  focusSuggestions = [],
  focusLeft = null;
const requests = new Set();
const identityChannel =
  typeof BroadcastChannel === "function"
    ? new BroadcastChannel("orbita.identity")
    : null;
function staleResponse() {
  return Object.assign(new Error("Пространство изменилось."), { stale: true });
}
function clearPrivate() {
  focusState = null;
  focusPending = false;
  focusSuggestions = [];
  focusLeft = null;
  $("daily-focus").hidden = true;
  $("focus-title").textContent = "";
  $("focus-note").textContent = "";
  $("focus-actions").replaceChildren();
  $("link-code").value = "";
  $("telegram-identity").textContent = "";
  $("telegram-identity").hidden = true;
  $("telegram-unlink-password").value = "";
  $("telegram-unlink-confirmed").checked = false;
  $("telegram-unlink-details").hidden = true;
  $("telegram-unlink-error").textContent = "";
  privateReady = false;
  identityVersion++;
  for (const controller of requests) controller.abort();
  requests.clear();
  plannerGeneration++;
  clearTimeout(searchTimer);
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
    "trash-list",
    "today-list",
    "toast-text",
    "chat-notice",
    "planner-count",
    "link-error",
    "form-error",
    "settings-username",
    "recovery-code",
    "password-error",
    "delete-error",
    "notes-board",
    "board-tabs",
    "home-notes-list",
    "event-notes-list",
  ])
    $(id)?.replaceChildren();
  document.querySelectorAll("dialog[open]").forEach((el) => el.close());
  for (const id of [
    "event-form",
    "search-form",
    "notes-search-form",
    "chat-form",
    "password-form",
    "delete-account-form",
    "link-form",
  ])
    $(id).reset();
  $("auth-form").reset();
  $("trash-list").hidden = $("toast").hidden = $("chat-notice").hidden = true;
  $("planner-more").hidden = true;
  $("search-clear").hidden = true;
  setFiltersOpen(false);
  $("account-card").hidden = true;
  $("open-bot").hidden = true;
  $("open-bot").removeAttribute("href");
  $("use-ai").checked = $("notify-toggle").checked = false;
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
  plannerOffset = 0;
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
  resetNotes();
  document.title = "Орбита — задачи и напоминания";
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
  $("account-card").hidden = isMini || !config.account?.username;
  $("settings-username").textContent = config.account?.username || "";
  $("use-ai-label").hidden = $("use-ai-state").hidden =
    typeof config.account?.use_ai !== "boolean";
  $("use-ai").checked = config.account?.use_ai === true;
  $("use-ai-state").textContent = config.account?.use_ai
    ? config.privacy?.ai_enabled
      ? "Текст запросов передаётся сервису ИИ."
      : "ИИ сейчас недоступен. Используй простые команды."
    : "ИИ выключен.";
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
      ((!path.startsWith("/api/auth/") && path !== "/api/telegram/unlink") ||
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
  document.title =
    {
      assistant: docked() ? "Сегодня" : "Ассистент",
      planner: "Мои планы",
      notes: "Заметки",
      settings: "Настройки",
    }[name] + " — Орбита";
  renderFocus();
  if (isMini) {
    try {
      name === "planner" ? tg?.BackButton?.hide() : tg?.BackButton?.show();
    } catch {}
  }
  if (name === "planner") refresh();
  if (name === "notes") loadNotes();
  if (name === "settings") loadNotifications();
  if (name === "assistant") {
    if (!docked()) loadHistory();
    refresh();
  }
  if (name !== "planner") setFiltersOpen(false);
  window.scrollTo({ top: 0, behavior: "auto" });
}
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
function addMessage(role, text, events = [], actions = [], preview = null, notes = []) {
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
  if (preview) {
    const draft = node("div", "chat-preview");
    draft.append(node("span", "eyebrow", "ПЕРЕД СОХРАНЕНИЕМ"), node("strong", "", preview.title || "Без названия"));
    if (preview.type === "event")
      draft.append(node("small", "", (types[preview.kind] || "Задача") + " · " + pretty(preview.date) + (preview.time ? " · " + preview.time : "") + (preview.kind === "meeting" ? " · " + preview.duration + " мин" : "")),
        node("small", "muted", preview.timezone || timezone));
    else if (preview.body) draft.append(node("small", "", preview.body.slice(0, 400)));
    if (preview.conflicts?.length) draft.append(node("small", "danger", "Пересечение: " + preview.conflicts.join(", ")));
    bubble.append(draft);
  }
  for (const n of notes || []) {
    const b = node("button", "inline-event");
    b.type = "button";
    b.append(node("strong", "", n.title), node("small", "", n.excerpt || "Открыть заметку"));
    b.onclick = () => openNote(n.id);
    bubble.append(b);
  }
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
            i === last ? r.preview : null,
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
    addMessage("assistant", r.reply, r.events, r.actions, r.preview, r.notes);
    renderAIState(r.mode, r.notice, r.model);
    $("chat-notice").hidden = !(r.notice && r.mode === "local");
    $("chat-notice").textContent = r.notice && r.mode === "local"
      ? "Сейчас доступны простые команды. Попробуй указать действие, дату и время."
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
      openAssistant();
      $("prompt").value = b.dataset.prompt;
      $("prompt").focus();
    }),
);
async function refresh() {
  if (!privateReady) return;
  labels();
  if (["assistant", "planner"].includes(view)) await loadFocus();
  if (view === "settings") return loadNotifications();
  if (view === "notes") return loadNotes();
  if (view === "planner") {
    await loadNotifications();
    return loadPlanner();
  }
  if (view !== "assistant" || isMini) return;
  if (refreshLoading) return refreshLoading;
  const version = identityVersion;
  refreshLoading = (async () => {
    try {
      const desktop = !phone.matches;
      if (desktop) loadHomeNotes();
      const [s, r] = await Promise.all([
        api("/api/stats?tz=" + encodeURIComponent(timezone)),
        desktop
          ? api(
              "/api/events?start=" +
                today() +
                "&end=" +
                today() +
                "&status=active&limit=6",
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
  // Choosing a period means browsing plans, not searching them.
  $("search").value = "";
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
  );
  if (e.notes) {
    const chip = node("button", "record-notes", "");
    chip.type = "button";
    chip.append(icon("note"), document.createTextNode(String(e.notes)));
    chip.setAttribute("aria-label", "Заметки к плану: " + e.notes);
    chip.title = "Заметки к плану";
    chip.onclick = () => openPlanNotes(e);
    top.append(chip);
  }
  top.append(node("span", "record-id", "#" + e.id));
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
  if (e.kind === "task" && e.status === "active") {
    const focus = node("button", "focus-pick", focusState?.event?.id === e.id ? "В фокусе" : "В фокус");
    focus.disabled = focusState?.event?.id === e.id;
    focus.onclick = () => changeFocus("/api/focus", {id:e.id});
    actions.append(focus);
  }
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
  const note = node("button", "", "Заметка");
  note.onclick = () => openNote(null, { event: e });
  actions.append(note);
  actions.append(del);
  el.append(actions);
  appendDelivery(el, e);
  return el;
}
async function loadPlanner(more = false) {
  const generation = ++plannerGeneration;
  const query = $("search").value.trim();
  renderFilterState(query);
  if (!more) plannerOffset = 0;
  const q = new URLSearchParams({ limit: "30", offset: String(plannerOffset) });
  if ($("kind-filter").value) q.set("kind", $("kind-filter").value);
  if (query) {
    // Search covers every date and status; the period applies to browsing only.
    q.set("q", query);
    return fillPlanner(q, more, generation, query);
  }
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
  if (range === "inbox" || range === "overdue") {
    q.set("bucket", range);
    q.set("status", "active");
  }
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  if (!["inbox", "overdue"].includes(range) && $("status-filter").value)
    q.set("status", $("status-filter").value);
  return fillPlanner(q, more, generation, "");
}
async function fillPlanner(q, more, generation, query) {
  $("planner-more").disabled = true;
  try {
    const r = await api("/api/events?" + q);
    if (generation !== plannerGeneration) return;
    const shown = Math.min(plannerOffset + r.events.length, r.total);
    $("planner-count").textContent = query
      ? `По запросу «${query}»: ${r.total}` + (r.total > shown ? ` · показано ${shown}` : "")
      : `Записей: ${r.total}` + (r.total > shown ? ` · показано ${shown}` : "");
    if (!more) $("planner-list").replaceChildren();
    // Search results keep relevance order; browsing groups records by day.
    if (query) $("planner-list").append(...r.events.map(card));
    else appendPlanGroups(r.events);
    plannerOffset += r.events.length;
    $("planner-more").hidden = !r.has_more;
    if (!r.events.length && !more)
      if (query)
        empty(
          $("planner-list"),
          "Ничего не нашлось",
          "Попробуй начало слова, имя или другую формулировку.",
        );
      else
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
      if (b.dataset.range !== "custom" && matchMedia("(max-width:760px)").matches)
        setFiltersOpen(false);
      loadPlanner();
    }),
);
["kind-filter", "status-filter", "range-start", "range-end"].forEach(
  (id) => ($(id).onchange = () => loadPlanner()),
);
$("planner-more").onclick = () => loadPlanner(true);
$("planner-refresh").onclick = () => loadPlanner();
const rangeNames = {
  today: "Сегодня",
  tomorrow: "Завтра",
  week: "Неделя",
  overdue: "Просрочено",
  inbox: "Без даты",
  all: "Все",
  custom: "Период",
};
function renderFilterState(query = $("search").value.trim()) {
  const parts = query ? ["Поиск"] : [rangeNames[range]];
  if ($("kind-filter").value)
    parts.push($("kind-filter").selectedOptions[0].textContent);
  if (!query && !["inbox", "overdue"].includes(range) && $("status-filter").value !== "active")
    parts.push($("status-filter").selectedOptions[0].textContent);
  $("filters-summary").textContent = parts.join(" · ");
  $("filters").classList.toggle("searching", Boolean(query));
  $("search-clear").hidden = !query;
  $("status-filter").disabled = Boolean(query) || ["inbox", "overdue"].includes(range);
}
function setFiltersOpen(open) {
  $("filters").classList.toggle("open", open);
  $("filters-toggle").setAttribute("aria-expanded", String(open));
}
$("filters-toggle").onclick = () =>
  setFiltersOpen(!$("filters").classList.contains("open"));
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  clearTimeout(searchTimer);
  $("search").blur();
  loadPlanner();
};
$("search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadPlanner(), 300);
};
$("search-clear").onclick = () => {
  $("search").value = "";
  clearTimeout(searchTimer);
  loadPlanner();
  $("search").focus();
};
function openSearch() {
  setView("planner");
  $("search").focus();
}
async function refreshLists() {
  await refresh();
}
function fieldRules() {
  const required = $("event-kind").value !== "task";
  $("event-date").required = required;
  $("event-time").required = required;
  $("duration-label").hidden = $("event-kind").value !== "meeting";
  reminderDateRules();
}
function reminderDateRules() {
  const date = $("event-date"), time = $("event-time");
  const zone = currentEvent?.timezone || timezone;
  const reminder = $("event-kind").value === "reminder";
  // Existing overdue records can still be completed or renamed without moving them.
  const unchanged = currentEvent?.kind === "reminder" && date.value === currentEvent.date && time.value === currentEvent.time;
  date.min = reminder && !unchanged ? today(zone) : "";
  time.min = "";
  if (reminder && !unchanged && date.value === today(zone)) {
    const nextMinute = new Date(Date.now() + 60000);
    const parts = new Intl.DateTimeFormat("en-GB", {timeZone:zone,hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).formatToParts(nextMinute);
    const value = key => parts.find(p=>p.type===key).value;
    time.min = value("hour") + ":" + value("minute");
  }
}
$("event-date").onchange = reminderDateRules;
$("event-time").onchange = reminderDateRules;
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
  renderEventNotes(e);
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
  reminderDateRules();
  if (!$("event-form").reportValidity()) return;
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
$("telegram-unlink-form").onsubmit = async (event) => {
  event.preventDefault();
  const password = $("telegram-unlink-password").value;
  const confirmed = $("telegram-unlink-confirmed").checked;
  $("telegram-unlink-password").value = "";
  $("telegram-unlink-error").textContent = "";
  $("telegram-unlink-submit").disabled = true;
  try {
    await api("/api/telegram/unlink", { password, confirmed });
    clearPrivate();
    identityChannel?.postMessage("changed");
    await init();
    toast("Telegram отключён. Записи сохранены в аккаунте.");
  } catch (error) {
    if (!error.stale) $("telegram-unlink-error").textContent = error.message;
  } finally {
    $("telegram-unlink-submit").disabled = false;
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
function focusClock() {
  return Date.now() / 1000 + focusClockOffset;
}
function focusButton(label, cls, run) {
  const button = node("button", cls, label);
  button.type = "button";
  button.disabled = focusPending;
  button.onclick = run;
  return button;
}
function renderFocus() {
  const box = $("daily-focus");
  if (!privateReady || !focusState || view !== "assistant") {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const event = focusState.event;
  const actions = [];
  // In the pinned strip on phones every line must stay short.
  const pinned = document.body.classList.contains("phone");
  box.className = "daily-focus";
  if (!event) {
    box.classList.add("is-empty");
    $("focus-title").textContent = pinned
      ? "Выбери главное дело дня"
      : "Одно главное дело на сегодня";
    $("focus-note").textContent = focusSuggestions.length
      ? "Что важнее всего сегодня? Выбери одну задачу — она останется здесь до конца дня."
      : "Добавь задачу, и её можно будет сделать главной на сегодня.";
    for (const item of focusSuggestions)
      actions.push(
        focusButton(item.title, "focus-chip", () =>
          changeFocus("/api/focus", { id: item.id }),
        ),
      );
    if (!focusSuggestions.length)
      actions.push(
        focusButton("Добавить задачу", pinned ? "focus-chip" : "primary", () => openEvent()),
      );
  } else if (focusState.completed) {
    box.classList.add("is-done");
    $("focus-title").textContent = event.title;
    $("focus-note").textContent = pinned
      ? "Сделано"
      : "Сделано. Главное на сегодня уже позади.";
    actions.push(
      focusButton("Выбрать ещё", "secondary", () =>
        changeFocus("/api/focus", { id: null }),
      ),
    );
  } else {
    $("focus-title").textContent = event.title;
    const left = focusState.ends_at ? focusState.ends_at - focusClock() : null;
    if (left !== null && left > 0) {
      box.classList.add("is-running");
      const minutes = Math.floor(left / 60),
        seconds = Math.floor(left % 60);
      $("focus-note").textContent =
        (pinned ? "Осталось " : "Без переключений · осталось ") +
        minutes +
        ":" +
        String(seconds).padStart(2, "0");
      actions.push(
        focusButton(pinned ? "Стоп" : "Остановить", "secondary", () =>
          changeFocus("/api/focus/stop", {}),
        ),
      );
    } else if (left !== null) {
      $("focus-note").textContent = pinned
        ? "25 минут прошли"
        : "25 минут прошли. Отметь задачу или возьми ещё один круг.";
      actions.push(
        focusButton(pinned ? "Ещё 25 мин" : "Ещё 25 минут", "secondary", () =>
          changeFocus("/api/focus/start", {}),
        ),
      );
    } else {
      $("focus-note").textContent = pinned
        ? "Главное на сегодня"
        : "Главное на сегодня. Начни с 25 минут без отвлечений.";
      actions.push(
        focusButton(pinned ? "▶ 25 мин" : "Начать 25 минут", "primary", () =>
          changeFocus("/api/focus/start", {}),
        ),
      );
    }
    actions.push(
      focusButton(pinned ? "✓" : "✓ Готово", "secondary focus-done", () => completeFocus(event)),
      focusButton(pinned ? "✕" : "Убрать", "subtle focus-remove", () =>
        changeFocus("/api/focus", { id: null }),
      ),
    );
  }
  if (pinned) {
    const done = actions.find((b) => b.classList.contains("focus-done"));
    const remove = actions.find((b) => b.classList.contains("focus-remove"));
    if (done) done.setAttribute("aria-label", "Отметить главное дело выполненным");
    if (remove) remove.setAttribute("aria-label", "Убрать из фокуса дня");
  }
  $("focus-actions").replaceChildren(...actions);
}
async function loadFocus() {
  const version = identityVersion;
  try {
    const state = await api("/api/focus");
    if (version !== identityVersion) return;
    focusSuggestions = [];
    if (!state.event) {
      const base = "/api/events?kind=task&status=active&limit=3";
      const [day, inbox] = await Promise.all([
        api(base + "&start=" + today() + "&end=" + today()),
        api(base + "&bucket=inbox"),
      ]);
      if (version !== identityVersion) return;
      focusSuggestions = [...day.events, ...inbox.events].slice(0, 3);
    }
    focusState = state;
    focusClockOffset = state.now - Date.now() / 1000;
  } catch (e) {
    if (!e.stale) focusState = null;
  }
  renderFocus();
}
async function changeFocus(path, data) {
  if (focusPending) return;
  focusPending = true;
  renderFocus();
  try {
    focusState = await api(path, data);
    focusClockOffset = focusState.now - Date.now() / 1000;
    if (path === "/api/focus" && data.id) toast("Задача в фокусе дня");
    if (path === "/api/focus/start") haptic();
  } catch (e) {
    if (!e.stale) toast(e.message);
  } finally {
    focusPending = false;
  }
  await refreshLists();
}
async function completeFocus(event) {
  if (focusPending) return;
  focusPending = true;
  renderFocus();
  try {
    if (await saveWithConflict({ ...event, status: "done" })) {
      haptic();
      toast("Главное дело сделано");
    }
  } catch (e) {
    toast(e.message);
  } finally {
    focusPending = false;
  }
  await refreshLists();
}
setInterval(() => {
  if (!focusState?.ends_at || focusPending) return;
  const left = focusState.ends_at - focusClock();
  if (focusLeft !== null && focusLeft > 0 && left <= 0) {
    haptic();
    toast("25 минут прошли — отличная работа");
  }
  focusLeft = left;
  if (!document.hidden) renderFocus();
}, 1000);
// Phone layout: focus and counters become a compact pinned strip inside the
// chat (like a pinned message in Telegram) and the chat fills the screen.
const phone = matchMedia("(max-width:760px)");
const pinnedParts = {
  focus: $("daily-focus"),
  stats: document.querySelector("#view-assistant .stats"),
  heading: document.querySelector("#view-assistant .heading"),
};
function placePinned() {
  const { focus, stats, heading } = pinnedParts;
  if (phone.matches) $("chat-pins").append(focus, stats);
  else heading.after(focus, stats);
  document.body.classList.toggle("phone", phone.matches);
  placeAssistant();
  renderFocus();
}
phone.addEventListener("change", placePinned);
placePinned();
function measureChrome() {
  const root = document.documentElement.style;
  const bar = document.querySelector(".topbar");
  const nav = document.querySelector(".sidebar nav");
  root.setProperty("--top-h", (bar?.offsetHeight || 0) + "px");
  // The bottom navigation is fixed on phones; elsewhere it takes no space below.
  root.setProperty("--nav-h", phone.matches ? (nav?.offsetHeight || 0) + "px" : "0px");
}
if (typeof ResizeObserver === "function") {
  const observer = new ResizeObserver(measureChrome);
  for (const el of [document.querySelector(".topbar"), document.querySelector(".sidebar nav")])
    if (el) observer.observe(el);
}
measureChrome();
async function download(format) {
  if (!privateReady) return;
  const version = identityVersion;
  if (isMini && format !== "notes" && config.telegram.username) {
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
    a.download = format === "notes" ? "orbita-notes.zip" : "orbita-events." + format;
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
$("export-notes").onclick = () => download("notes");
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
  openAssistant();
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
        const audio = new Blob(chunks, { type: mime });
        const limit = config.limits?.upload_bytes || 12 * 1024 * 1024;
        if (audio.size > limit)
          throw new Error(`Запись слишком большая. Максимум ${Math.floor(limit / 1024 / 1024)} МБ. Запиши короче или введи текст.`);
        const r = await fetch("/api/transcribe", {
          method: "POST",
          headers: h,
          credentials: isMini ? "omit" : "same-origin",
          body: audio,
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
  const dialogOpen = Boolean(document.querySelector("dialog[open]"));
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (view === "notes") $("notes-search").focus();
    else openSearch();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
    e.preventDefault();
    if (docked()) toggleDock(!dockOpen());
    else setView("assistant");
  } else if (e.key === "Escape" && !dialogOpen && docked() && dockOpen()) {
    e.preventDefault();
    toggleDock(false);
    $("assistant-fab").focus();
  } else if (
    !editing &&
    !dialogOpen &&
    !e.ctrlKey &&
    !e.metaKey &&
    !e.altKey &&
    e.key.toLowerCase() === "n"
  ) {
    e.preventDefault();
    if (view === "notes") openNote(null);
    else openEvent();
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
    $("account-channel").textContent = config.account.linked
      ? "Telegram подключён"
      : config.account.private
        ? "Личное пространство"
        : "Веб-версия";
    renderAIState();
    renderPrivacy();
    $("telegram-state").textContent = config.account.linked
      ? "Аккаунт связан: сайт, бот и Mini App видят одни и те же записи."
      : config.telegram.configured
        ? "Свяжи сайт с ботом — и продолжай с любого экрана."
        : "Telegram ещё не настроен. Владелец должен задать токен бота и HTTPS-адрес.";
    const telegramIdentity = config.account.telegram_identity;
    $("telegram-identity").hidden = !telegramIdentity?.id;
    $("telegram-identity").textContent = telegramIdentity?.id
      ? [telegramIdentity.display_name, telegramIdentity.username ? "@" + telegramIdentity.username : null, "ID: " + telegramIdentity.id].filter(Boolean).join(" · ")
      : "";
    $("telegram-unlink-details").hidden = isMini || !telegramIdentity?.can_unlink;
    $("telegram-unlink-details").open = false;
    $("telegram-unlink-password").value = "";
    $("telegram-unlink-confirmed").checked = false;
    $("telegram-unlink-error").textContent = "";
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
    $("confirm-dialog").open ||
    $("note-dialog").open ||
    $("text-dialog").open ||
    notesDragging
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
    : "";
  $("mode-label").textContent = fallback
    ? "Доступны простые команды"
    : mode
      ? "Можно добавить ещё запись"
      : config.llm
        ? "Добавление и поиск записей"
        : "Задачи, встречи и напоминания";
  $("ai-details").textContent = fallback
    ? "Сейчас доступны простые команды и добавление через форму."
    : "Напиши, что нужно добавить, найти или изменить.";
  $("voice-details").textContent = config.stt
    ? "После записи проверь текст и нажми «Отправить»."
    : "Для голосового ввода используй микрофон на клавиатуре.";
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
      (delivery.interval_seconds === 60
        ? " Проверяем сроки примерно раз в минуту; возможна небольшая задержка."
        : "") +
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

// ---------- Desktop assistant: a floating window available on every page ----------
function docked() {
  return document.body.classList.contains("has-dock");
}
function dockOpen() {
  return !$("assistant-dock").hidden;
}
function placeAssistant() {
  const panel = document.querySelector(".chat-panel");
  const grid = document.querySelector("#view-assistant .assistant-grid");
  const tips = document.querySelector(".suggestions");
  const dock = !isMini && !phone.matches;
  document.body.classList.toggle("has-dock", dock);
  if (dock) {
    if (panel.parentElement !== $("assistant-dock")) $("assistant-dock").append(panel);
    if (tips && tips.parentElement !== panel)
      panel.insertBefore(tips, panel.querySelector(".composer-wrap"));
  } else {
    if (panel.parentElement !== grid) grid.prepend(panel);
    if (tips && tips.parentElement !== $("view-assistant")) $("view-assistant").append(tips);
    $("assistant-dock").hidden = true;
    document.body.classList.remove("dock-open");
  }
  $("assistant-fab").hidden = !dock;
  $("home-notes").hidden = !dock;
  $("nav-home-label").textContent = dock ? "Сегодня" : "Ассистент";
  $("nav-home-icon").replaceChildren(icon(dock ? "home" : "spark"));
  document.querySelector("#view-assistant .heading p").textContent = dock
    ? "Фокус, планы на день и свежие заметки. Ассистент — в правом нижнем углу."
    : "Добавить дело, найти запись или посмотреть планы.";
}
function toggleDock(open, focus = true) {
  if (!docked() && open) return;
  $("assistant-dock").hidden = !open;
  document.body.classList.toggle("dock-open", open);
  const fab = $("assistant-fab");
  fab.setAttribute("aria-expanded", String(open));
  fab.setAttribute("aria-label", open ? "Свернуть ассистента" : "Открыть ассистента");
  fab.replaceChildren(icon(open ? "close" : "spark"));
  if (!open) return;
  loadHistory().then(() => {
    const m = $("messages");
    m.scrollTop = m.scrollHeight;
  });
  if (focus) setTimeout(() => $("prompt").focus(), 30);
}
function openAssistant() {
  if (docked()) toggleDock(true);
  else setView("assistant");
}
$("assistant-fab").onclick = () => toggleDock(!dockOpen());
$("dock-close").onclick = () => {
  toggleDock(false);
  $("assistant-fab").focus();
};
placeAssistant();

// ---------- Notes: boards, [[links]] and attachments to plans ----------
const INBOX = "Входящие";
const noteColors = [
  [null, "Без цвета"],
  ["yellow", "Жёлтый"],
  ["green", "Зелёный"],
  ["blue", "Синий"],
  ["violet", "Фиолетовый"],
  ["rose", "Розовый"],
];
let notesState = { boards: [], notes: [], titles: [], tags: [], total: 0 },
  notesBoard = prefs.get("notes.board", ""),
  notesTag = "",
  notesEvent = null,
  notesGeneration = 0,
  notesSearchTimer = 0,
  notesDragging = false,
  notesChanged = false,
  currentNote = null,
  noteDraftEvent = null,
  noteTimer = 0,
  noteQueue = Promise.resolve(),
  noteDirty = false,
  noteMode = "edit";
const relative = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 45) return "только что";
  if (s < 3600) return relative.format(-Math.round(s / 60), "minute");
  if (s < 86400) return relative.format(-Math.round(s / 3600), "hour");
  if (s < 7 * 86400) return relative.format(-Math.round(s / 86400), "day");
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    timeZone: timezone,
  });
}
function fullDate(iso) {
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: timezone,
  });
}
function plural(n, [one, few, many]) {
  const a = n % 10,
    b = n % 100;
  const word = a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many;
  return n + " " + word;
}
function shortDate(d) {
  if (d === today()) return "сегодня";
  if (d === shift(today(), 1)) return "завтра";
  return new Date(d + "T12:00:00Z").toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
function eventWhen(e) {
  return e.date ? shortDate(e.date) + (e.time ? " · " + e.time : "") : "без даты";
}
function compactEvent(e) {
  const { id, title, kind, date, time, status } = e;
  return { id, title, kind, date, time, status };
}
function iconButton(name, label, run) {
  const b = node("button", "icon-button");
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.title = label;
  b.append(icon(name));
  b.onclick = run;
  return b;
}
function resetNotes() {
  notesGeneration++;
  clearTimeout(noteTimer);
  clearTimeout(notesSearchTimer);
  notesState = { boards: [], notes: [], titles: [], tags: [], total: 0 };
  notesTag = "";
  notesEvent = null;
  currentNote = noteDraftEvent = null;
  noteDirty = notesChanged = notesDragging = false;
  noteQueue = Promise.resolve();
  noteTabs = [];
  noteConflict = null;
  $("note-conflict").hidden = true;
  $("note-body").value = $("note-title").value = "";
  for (const id of ["note-preview", "note-tabs", "note-files", "note-events", "note-outgoing", "note-backlinks"])
    $(id).replaceChildren();
  $("notes-filter").hidden = true;
  $("notes-search-clear").hidden = true;
  $("event-notes").hidden = true;
  closeBoardMenu();
  if (docked()) toggleDock(false, false);
}
async function loadNotes() {
  if (!privateReady) return;
  const generation = ++notesGeneration;
  const q = new URLSearchParams();
  const query = $("notes-search").value.trim();
  if (query) q.set("q", query);
  if (notesTag) q.set("tag", notesTag);
  if (notesEvent) q.set("event_id", String(notesEvent.id));
  try {
    const r = await api("/api/notes?" + q);
    if (generation !== notesGeneration) return;
    notesState = r;
    if (notesBoard && !r.boards.some((b) => b.name === notesBoard)) notesBoard = "";
    renderBoardTabs();
    renderNotesFilter();
    renderNotes();
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
}
function renderBoardTabs() {
  const tabs = $("board-tabs");
  const total = notesState.boards.reduce((n, b) => n + b.count, 0);
  const make = (name, label, count) => {
    const b = node("button", "board-tab" + (notesBoard === name ? " active" : ""));
    b.type = "button";
    b.setAttribute("aria-pressed", String(notesBoard === name));
    b.append(node("span", "", label), node("small", "", String(count)));
    b.onclick = () => {
      notesBoard = name;
      prefs.set("notes.board", name);
      renderBoardTabs();
      renderNotes();
    };
    if (name) dropTarget(b, name);
    return b;
  };
  tabs.replaceChildren(
    make("", "Все доски", total),
    ...notesState.boards.map((b) => make(b.name, b.name, b.count)),
  );
  const add = node("button", "board-tab add");
  add.type = "button";
  add.append(icon("plus"), node("span", "", "Доска"));
  add.onclick = newBoard;
  tabs.append(add);
}
function renderNotesFilter() {
  const box = $("notes-filter");
  box.replaceChildren();
  const chip = (label, active, run) => {
    const b = node("button", "tag-chip" + (active ? " active" : ""), label);
    b.type = "button";
    b.onclick = run;
    if (active) {
      b.append(icon("close"));
      b.setAttribute("aria-label", "Сбросить фильтр " + label);
    }
    return b;
  };
  const query = $("notes-search").value.trim();
  if (query || notesTag || notesEvent)
    box.append(node("span", "notes-found", "Найдено: " + notesState.total));
  if (notesEvent)
    box.append(chip("План: " + notesEvent.title, true, () => {
      notesEvent = null;
      loadNotes();
    }));
  if (notesTag)
    box.append(chip("#" + notesTag, true, () => {
      notesTag = "";
      loadNotes();
    }));
  if (!notesEvent && !notesTag && !query)
    for (const t of notesState.tags.slice(0, 12))
      box.append(chip("#" + t.tag, false, () => filterTag(t.tag)));
  box.hidden = !box.children.length;
}
function filterTag(tag) {
  notesTag = tag;
  $("notes-search").value = "";
  $("notes-search-clear").hidden = true;
  if (view !== "notes") setView("notes");
  else loadNotes();
}
function emptyNotes(root, title, text, board) {
  const box = node("div", "empty-state notes-empty"),
    mark = node("div", "empty-symbol");
  mark.append(icon("note"));
  box.append(mark, node("h3", "", title), node("p", "", text));
  if (board !== undefined) {
    const add = node("button", "primary", "Новая заметка");
    add.onclick = () => openNote(null, { board: board || undefined });
    box.append(add);
  }
  root.append(box);
}
function renderNotes() {
  closeBoardMenu();
  const root = $("notes-board");
  root.replaceChildren();
  const query = $("notes-search").value.trim();
  const flat = Boolean(query || notesTag || notesEvent);
  let list = notesState.notes;
  if (!flat && notesBoard) list = list.filter((n) => n.board === notesBoard);
  document.querySelectorAll("[data-notes-layout]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.notesLayout === notesLayout)));
  if (notesLayout === "graph") return renderNotesGraph(root, list);
  if (notesLayout === "files") return renderVault(root, list);
  root.className = "notes-board " + (flat || notesBoard ? "grid" : "columns");
  if (flat) {
    if (!list.length)
      return emptyNotes(
        root,
        "Ничего не нашлось",
        query ? "Попробуй начало слова или #тег." : "Под этот фильтр заметок нет.",
      );
    return root.append(...list.map((n) => noteCard(n, true)));
  }
  if (notesBoard) {
    if (!list.length)
      return emptyNotes(root, "Доска пустая", "Добавь сюда первую заметку.", notesBoard);
    root.append(...list.map((n) => noteCard(n, false)));
    return;
  }
  for (const b of notesState.boards) {
    const col = node("section", "note-column");
    col.dataset.board = b.name;
    const head = node("header", "note-column-head");
    head.append(
      node("h2", "", b.name),
      node("span", "note-count", String(b.count)),
      iconButton("plus", "Новая заметка на доске «" + b.name + "»", () =>
        openNote(null, { board: b.name }),
      ),
      iconButton("more", "Действия с доской «" + b.name + "»", (ev) =>
        openBoardMenu(ev.currentTarget, b.name),
      ),
    );
    const body = node("div", "note-column-body");
    body.append(...list.filter((n) => n.board === b.name).map((n) => noteCard(n, false)));
    const ghost = node("button", "note-ghost");
    ghost.type = "button";
    ghost.append(icon("plus"), node("span", "", "Заметка"));
    ghost.onclick = () => openNote(null, { board: b.name });
    body.append(ghost);
    col.append(head, body);
    dropTarget(col, b.name);
    root.append(col);
  }
}
function noteCard(n, showBoard) {
  const card = node("article", "note-card" + (n.pinned ? " pinned" : ""));
  if (n.color) card.dataset.color = n.color;
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", "Открыть заметку «" + n.title + "»");
  card.onclick = () => openNote(n.id);
  card.onkeydown = (e) => {
    if (e.target === card && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      openNote(n.id);
    }
  };
  const top = node("div", "note-card-top");
  if (n.pinned) {
    const p = node("span", "note-pin");
    p.title = "Закреплена";
    p.append(icon("pin"));
    top.append(p);
  }
  if (showBoard) top.append(node("span", "note-board-label", n.board));
  top.append(node("span", "note-time", ago(n.updated_at)));
  card.append(top, node("h3", "", n.title));
  if (n.excerpt) card.append(node("p", "note-excerpt", n.excerpt));
  if (n.checklist.total) {
    const row = node("div", "note-progress-row"),
      bar = node("div", "note-progress"),
      fill = node("span");
    fill.style.width = Math.round((100 * n.checklist.done) / n.checklist.total) + "%";
    bar.append(fill);
    row.append(bar, node("small", "", n.checklist.done + "/" + n.checklist.total));
    card.append(row);
  }
  const foot = node("div", "note-card-foot");
  for (const t of n.tags.slice(0, 3)) {
    const tag = node("button", "note-tag", "#" + t);
    tag.type = "button";
    tag.onclick = (e) => {
      e.stopPropagation();
      filterTag(t);
    };
    foot.append(tag);
  }
  if (n.events.length) {
    const ev = node("span", "note-meta");
    const e = n.events[0];
    ev.append(
      icon("calendar"),
      document.createTextNode(
        n.events.length === 1
          ? (e.title.length > 26 ? e.title.slice(0, 25) + "…" : e.title) + " · " + eventWhen(e)
          : plural(n.events.length, ["план", "плана", "планов"]),
      ),
    );
    foot.append(ev);
  }
  if (n.links) {
    const l = node("span", "note-meta");
    l.title = "Ссылки на другие заметки";
    l.append(icon("link"), document.createTextNode(String(n.links)));
    foot.append(l);
  }
  if (foot.children.length) card.append(foot);
  if (!phone.matches) {
    card.draggable = true;
    card.ondragstart = (e) => {
      notesDragging = true;
      e.dataTransfer.setData("text/orbita-note", String(n.id));
      e.dataTransfer.effectAllowed = "move";
      card.classList.add("dragging");
    };
    card.ondragend = () => {
      notesDragging = false;
      card.classList.remove("dragging");
      document.querySelectorAll(".drop-over").forEach((x) => x.classList.remove("drop-over"));
    };
  }
  return card;
}
function dropTarget(el, board) {
  if (phone.matches) return;
  el.addEventListener("dragover", (e) => {
    if (!e.dataTransfer.types.includes("text/orbita-note")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    el.classList.add("drop-over");
  });
  el.addEventListener("dragleave", (e) => {
    if (!el.contains(e.relatedTarget)) el.classList.remove("drop-over");
  });
  el.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    el.classList.remove("drop-over");
    notesDragging = false;
    const id = Number(e.dataTransfer.getData("text/orbita-note"));
    const n = notesState.notes.find((x) => x.id === id);
    if (n && n.board !== board) moveNote(n, board);
  });
}
async function moveNote(n, board) {
  try {
    await api("/api/notes/save", { id: n.id, revision: n.revision, board });
    toast("Перенесено на доску «" + board + "»");
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
  loadNotes();
}
function openBoardMenu(anchor, name) {
  const menu = $("board-menu");
  if (!menu.hidden && menu.dataset.board === name) return closeBoardMenu();
  menu.replaceChildren();
  menu.dataset.board = name;
  const index = notesState.boards.findIndex((b) => b.name === name);
  const item = (label, run, danger = false) => {
    const b = node("button", danger ? "danger" : "", label);
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.onclick = () => {
      closeBoardMenu();
      run();
    };
    menu.append(b);
  };
  item("Переименовать", () => renameBoard(name));
  if (index > 0) item("Сдвинуть левее", () => reorderBoard(name, -1));
  if (index < notesState.boards.length - 1) item("Сдвинуть правее", () => reorderBoard(name, 1));
  if (notesState.boards.length > 1) item("Удалить доску", () => deleteBoard(name), true);
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  menu.style.top = Math.min(innerHeight - menu.offsetHeight - 8, r.bottom + 6) + "px";
  menu.style.left = Math.max(8, Math.min(innerWidth - menu.offsetWidth - 8, r.right - menu.offsetWidth)) + "px";
  menu.firstElementChild?.focus();
  setTimeout(() => document.addEventListener("pointerdown", outsideMenu), 0);
}
function outsideMenu(e) {
  if (!$("board-menu").contains(e.target)) closeBoardMenu();
}
function closeBoardMenu() {
  $("board-menu").hidden = true;
  delete $("board-menu").dataset.board;
  document.removeEventListener("pointerdown", outsideMenu);
}
$("board-menu").onkeydown = (e) => {
  if (e.key === "Escape") closeBoardMenu();
};
function promptText(title, value = "", ok = "Сохранить") {
  return new Promise((resolve) => {
    const d = $("text-dialog");
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      if (d.open) d.close();
      resolve(v);
    };
    $("text-title").textContent = title;
    $("text-value").value = value;
    $("text-ok").textContent = ok;
    $("text-error").textContent = "";
    $("text-form").onsubmit = (e) => {
      e.preventDefault();
      const v = $("text-value").value.trim();
      if (!v) {
        $("text-error").textContent = "Нужно название.";
        return;
      }
      finish(v);
    };
    $("text-cancel").onclick = () => finish(null);
    d.onclose = () => finish(null);
    d.showModal();
    $("text-value").select();
  });
}
async function newBoard() {
  const name = await promptText("Новая доска", "", "Создать");
  if (!name) return;
  try {
    await api("/api/notes/boards/save", { name });
    notesBoard = name;
    prefs.set("notes.board", name);
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
  loadNotes();
}
async function renameBoard(name) {
  const next = await promptText("Переименовать доску", name);
  if (!next || next === name) return;
  try {
    await api("/api/notes/boards/save", { name: next, previous: name });
    if (notesBoard === name) {
      notesBoard = next;
      prefs.set("notes.board", next);
    }
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
  loadNotes();
}
async function reorderBoard(name, delta) {
  const names = notesState.boards.map((b) => b.name);
  const i = names.indexOf(name),
    j = i + delta;
  if (i < 0 || j < 0 || j >= names.length) return;
  [names[i], names[j]] = [names[j], names[i]];
  try {
    await api("/api/notes/boards/order", { names });
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
  loadNotes();
}
async function deleteBoard(name) {
  const board = notesState.boards.find((b) => b.name === name);
  const target =
    name !== INBOX && notesState.boards.some((b) => b.name === INBOX)
      ? INBOX
      : notesState.boards.find((b) => b.name !== name)?.name;
  const text = board?.count
    ? plural(board.count, ["заметка переедет", "заметки переедут", "заметок переедут"]) +
      " на доску «" + target + "». Сами заметки не удалятся."
    : "На доске нет заметок.";
  if (!(await ask("Удалить доску «" + name + "»?", text, "Удалить доску", true))) return;
  try {
    const r = await api("/api/notes/boards/delete", { name, confirmed: true });
    if (notesBoard === name) {
      notesBoard = "";
      prefs.set("notes.board", "");
    }
    toast(r.moved ? "Доска удалена, заметки — на «" + r.target + "»" : "Доска удалена");
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
  loadNotes();
}
$("notes-search-form").onsubmit = (e) => {
  e.preventDefault();
  clearTimeout(notesSearchTimer);
  $("notes-search").blur();
  loadNotes();
};
$("notes-search").oninput = () => {
  $("notes-search-clear").hidden = !$("notes-search").value;
  clearTimeout(notesSearchTimer);
  notesSearchTimer = setTimeout(loadNotes, 250);
};
$("notes-search-clear").onclick = () => {
  $("notes-search").value = "";
  $("notes-search-clear").hidden = true;
  clearTimeout(notesSearchTimer);
  loadNotes();
  $("notes-search").focus();
};
$("note-add").onclick = () => openNote(null, { board: notesBoard || undefined });
$("home-note-add").onclick = () => openNote(null);
$("home-notes-all").onclick = () => setView("notes");
$("top-brand").onclick = () => setView(isMini ? "planner" : "assistant");

// Editor
async function openNote(id, opts = {}) {
  if (!privateReady) return;
  await flushNote();
  if (noteDirty || noteConflict) return;
  const version = identityVersion;
  let note;
  try {
    if (!notesState.boards.length) notesState = await api("/api/notes");
    if (id) note = (await api("/api/notes/get?id=" + id)).note;
  } catch (e) {
    if (!e.stale) toast(e.message);
    return;
  }
  if (version !== identityVersion) return;
  if (!note)
    note = {
      id: null,
      title: opts.title || "",
      body: "",
      board: opts.board || notesState.boards[0]?.name || INBOX,
      pinned: false,
      color: null,
      events: [],
      outgoing: [],
      backlinks: [],
      revision: null,
    };
  currentNote = note;
  rememberNote(note);
  noteDraftEvent = id ? null : opts.event ? compactEvent(opts.event) : null;
  noteDirty = false;
  fillNote();
  setNoteMode(id && note.body.trim() ? "preview" : "edit");
  const d = $("note-dialog");
  if (!d.open) d.showModal();
  d.querySelector(".note-main").scrollTop = 0;
  if (!id && !isMini) $(opts.title ? "note-body" : "note-title").focus();
  else $("note-close").focus();
}
function fillNote() {
  const n = currentNote;
  const select = $("note-board");
  select.replaceChildren();
  const boards = notesState.boards.map((b) => b.name);
  if (!boards.includes(n.board)) boards.push(n.board);
  for (const b of boards) select.append(new Option(b, b));
  select.value = n.board;
  $("note-title").value = n.title;
  $("note-body").value = n.body;
  $("note-delete").hidden = !n.id;
  $("note-status").textContent = n.id ? "" : "Черновик";
  renderColors();
  renderPinned();
  renderNoteSide();
  renderExplorer();
  renderNoteTabs();
  $("note-conflict").hidden = true;
  closeSuggest();
}
function renderColors() {
  const box = $("note-colors");
  box.replaceChildren();
  for (const [c, label] of noteColors) {
    const b = node("button", "note-color");
    b.type = "button";
    b.dataset.color = c || "none";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.setAttribute("aria-pressed", String((currentNote.color || null) === c));
    b.onclick = () => {
      currentNote.color = c;
      renderColors();
      queueNoteSave(0);
    };
    box.append(b);
  }
  $("note-dialog").dataset.color = currentNote.color || "none";
}
function renderPinned() {
  const on = Boolean(currentNote.pinned);
  $("note-pin").setAttribute("aria-pressed", String(on));
  $("note-pin").setAttribute("aria-label", on ? "Открепить" : "Закрепить");
  $("note-pin").title = on ? "Открепить" : "Закрепить наверху";
}
function chip(label, meta, run, extra = "") {
  const b = node("button", "note-chip " + extra, label);
  b.type = "button";
  if (meta) b.append(node("small", "", meta));
  b.onclick = run;
  return b;
}
function renderNoteSide() {
  const n = currentNote;
  const events = n.id ? n.events : noteDraftEvent ? [noteDraftEvent] : [];
  const box = $("note-events");
  box.replaceChildren();
  for (const e of events) {
    const row = node("div", "note-chip-row" + (e.status === "done" ? " done" : ""));
    row.append(
      chip(e.title, eventWhen(e), () => openLinkedEvent(e.id)),
      iconButton("close", "Открепить от плана «" + e.title + "»", () => detachEvent(e.id)),
    );
    box.append(row);
  }
  if (!events.length) box.append(node("p", "note-empty", "Не прикреплена"));
  const out = $("note-outgoing");
  out.replaceChildren();
  for (const l of n.outgoing || [])
    out.append(chip(l.title, l.id ? "" : "создать", () => followLink(l.title), l.id ? "" : "missing"));
  if (!out.children.length) out.append(node("p", "note-empty", "Нет ссылок"));
  const back = $("note-backlinks");
  back.replaceChildren();
  for (const l of n.backlinks || []) back.append(chip(l.title, l.board, () => openNote(l.id)));
  if (!back.children.length) back.append(node("p", "note-empty", "Пока никто не ссылается"));
  $("note-dates").textContent = n.created_at
    ? "Создана " + fullDate(n.created_at) + (n.updated_at !== n.created_at ? " · изменена " + ago(n.updated_at) : "")
    : "";
}
function noteDraft() {
  return {
    title: $("note-title").value,
    body: $("note-body").value,
    board: $("note-board").value,
    pinned: Boolean(currentNote.pinned),
    color: currentNote.color || null,
  };
}
function queueNoteSave(delay = 700) {
  if (!currentNote) return;
  noteDirty = true;
  $("note-status").textContent = "Изменено";
  clearTimeout(noteTimer);
  noteTimer = setTimeout(saveNote, delay);
}
function saveNote() {
  clearTimeout(noteTimer);
  const note = currentNote;
  if (!note || !noteDirty || noteConflict) return noteQueue;
  const draft = noteDraft();
  noteDirty = false;
  if (!note.id && !draft.title.trim() && !draft.body.trim()) return noteQueue;
  const version = identityVersion;
  noteQueue = noteQueue.then(async () => {
    if (version !== identityVersion) return;
    if (note === currentNote) $("note-status").textContent = "Сохраняю…";
    const payload = { ...draft };
    if (note.id) Object.assign(payload, { id: note.id, revision: note.revision });
    else if (noteDraftEvent && note === currentNote) payload.event_id = noteDraftEvent.id;
    try {
      const r = await api("/api/notes/save", payload);
      if (version !== identityVersion) return;
      const created = !note.id;
      Object.assign(note, r.note);
      rememberNote(note);
      notesChanged = true;
      if (created && !notesState.titles.some((t) => t.id === note.id))
        notesState.titles.push({ id: note.id, title: note.title });
      const titleEntry = notesState.titles.find((t) => t.id === note.id);
      if (titleEntry) titleEntry.title = note.title;
      const summary = notesState.notes.find((t) => t.id === note.id);
      if (summary) Object.assign(summary, { title: note.title, board: note.board, pinned: note.pinned, color: note.color });
      if (note !== currentNote) return;
      if (created) noteDraftEvent = null;
      $("note-status").textContent = noteDirty ? "Изменено" : "Сохранено";
      $("note-delete").hidden = false;
      renderNoteSide();
      renderNoteTabs();
      renderExplorer();
    } catch (e) {
      if (e.stale || version !== identityVersion) return;
      if (e.status === 409 && e.details?.note) {
        if (note !== currentNote) return;
        noteConflict = e.details.note;
        noteDirty = true;
        $("note-conflict").hidden = false;
        $("note-status").textContent = "Есть новая версия";
      } else {
        if (note === currentNote) {
          noteDirty = true;
          $("note-status").textContent = "Не сохранено";
        }
        toast(e.message);
      }
    }
  });
  return noteQueue;
}
function flushNote() {
  if (noteDirty) saveNote();
  return noteQueue;
}
function afterNotesChange() {
  if (!privateReady) return;
  if (view === "notes") loadNotes();
  else if (view === "assistant" && docked()) loadHomeNotes();
  else if (view === "planner") loadPlanner();
  if ($("event-dialog").open && currentEvent) renderEventNotes(currentEvent);
}
async function closeNote() {
  await flushNote();
  if (noteDirty || noteConflict) {
    $("note-status").textContent = "Сначала сохрани изменения";
    return;
  }
  $("note-dialog").close();
}
$("note-close").onclick = closeNote;
$("note-dialog").addEventListener("cancel", (e) => {
  e.preventDefault();
  closeNote();
});
$("note-dialog").addEventListener("close", async () => {
  closeSuggest();
  await flushNote();
  if (!$("note-dialog").open) currentNote = noteDraftEvent = null;
  if (notesChanged) {
    notesChanged = false;
    afterNotesChange();
  }
});
$("note-dialog").addEventListener("pointerdown", (e) => {
  // A tap on the dimmed backdrop closes the drawer.
  if (e.target === $("note-dialog")) closeNote();
});
$("note-title").oninput = () => queueNoteSave();
$("note-title").onkeydown = (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    setNoteMode("edit");
    $("note-body").focus();
  }
};
$("note-board").onchange = () => queueNoteSave(0);
$("note-pin").onclick = () => {
  currentNote.pinned = !currentNote.pinned;
  renderPinned();
  queueNoteSave(0);
};
$("note-delete").onclick = async () => {
  const n = currentNote;
  if (!n?.id) return;
  if (!(await ask("Удалить заметку?", (n.title || "Без названия") + "\n\nУдаление можно отменить в течение 10 минут.", "Удалить", true)))
    return;
  await flushNote();
  if (noteDirty || noteConflict) return;
  try {
    await api("/api/notes/delete", { id: n.id, revision: n.revision, confirmed: true });
    noteDirty = false;
    notesChanged = true;
    $("note-dialog").close();
    haptic();
    toast("Заметка удалена", { label: "Отменить", run: () => restoreNote(n.id) });
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
};
async function restoreNote(id) {
  try {
    await api("/api/notes/restore", { id });
    toast("Заметка восстановлена");
    afterNotesChange();
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
}
function setNoteMode(mode) {
  noteMode = mode;
  document
    .querySelectorAll("[data-note-mode]")
    .forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.noteMode === mode)));
  $("note-body").hidden = mode !== "edit";
  $("note-tools").hidden = mode !== "edit";
  $("note-preview").hidden = mode !== "preview";
  if (mode === "preview") renderPreview();
  else closeSuggest();
}
document.querySelectorAll("[data-note-mode]").forEach(
  (b) =>
    (b.onclick = () => {
      setNoteMode(b.dataset.noteMode);
      if (b.dataset.noteMode === "edit") $("note-body").focus();
    }),
);
$("note-preview").ondblclick = (e) => {
  if (e.target.closest("button,a,input,label")) return;
  setNoteMode("edit");
  $("note-body").focus();
};
const inserts = {
  heading: ["## ", "", true],
  check: ["- [ ] ", "", true],
  bold: ["**", "**"],
  link: ["[[", "]]"],
  tag: ["#", ""],
};
function insertMarkup(kind) {
  const ta = $("note-body");
  const [before, after, line] = inserts[kind];
  const s = ta.selectionStart,
    e = ta.selectionEnd;
  if (line) {
    const start = ta.value.lastIndexOf("\n", s - 1) + 1;
    ta.setRangeText(before, start, start, "preserve");
    ta.setSelectionRange(s + before.length, e + before.length);
  } else {
    const selected = ta.value.slice(s, e);
    ta.setRangeText(before + selected + after, s, e, "preserve");
    ta.setSelectionRange(s + before.length, s + before.length + selected.length);
  }
  ta.focus();
  queueNoteSave();
  updateSuggest();
}
document.querySelectorAll("[data-insert]").forEach((b) => {
  b.onmousedown = (e) => e.preventDefault();
  b.onclick = () => insertMarkup(b.dataset.insert);
});
function findNoteTitle(title) {
  const key = (text) => text.trim().replace(/\s+/g, " ").toLowerCase();
  const t = key(title);
  return notesState.titles.filter((x) => key(x.title) === t).sort((a, b) => a.id - b.id)[0];
}
async function followLink(title) {
  if (currentNote && title.toLowerCase() === currentNote.title.toLowerCase()) return;
  const found = findNoteTitle(title);
  if (found) return openNote(found.id);
  const board = currentNote?.board;
  await openNote(null, { title, board });
  noteDirty = true;
  await saveNote();
  toast("Создана заметка «" + title + "»");
}
function updateSuggest() {
  const ta = $("note-body");
  if (ta.hidden || ta.selectionStart !== ta.selectionEnd) return closeSuggest();
  const m = /\[\[([^\[\]\n|]{0,60})$/.exec(ta.value.slice(0, ta.selectionStart));
  if (!m) return closeSuggest();
  const q = m[1].trim().toLowerCase();
  const own = currentNote?.title.toLowerCase();
  const items = notesState.titles
    .filter((t) => t.title.toLowerCase() !== own && t.title.toLowerCase().includes(q))
    .slice(0, 6);
  const box = $("note-suggest");
  box.replaceChildren();
  const option = (label, title, cls = "") => {
    const b = node("button", cls, label);
    b.type = "button";
    b.setAttribute("role", "option");
    b.onmousedown = (e) => e.preventDefault();
    b.onclick = () => pickSuggest(title, m[1].length);
    box.append(b);
  };
  for (const t of items) option(t.title, t.title);
  if (q && !items.some((t) => t.title.toLowerCase() === q))
    option("Новая: «" + m[1].trim() + "»", m[1].trim(), "new");
  box.hidden = !box.children.length;
  box.firstElementChild?.classList.add("active");
}
function pickSuggest(title, typed) {
  const ta = $("note-body");
  const s = ta.selectionStart;
  const closed = ta.value.slice(s).startsWith("]]");
  ta.setRangeText(title + (closed ? "" : "]]"), s - typed, s, "end");
  if (closed) ta.setSelectionRange(s - typed + title.length + 2, s - typed + title.length + 2);
  closeSuggest();
  ta.focus();
  queueNoteSave();
}
function closeSuggest() {
  $("note-suggest").hidden = true;
  $("note-suggest").replaceChildren();
}
$("note-body").oninput = () => {
  queueNoteSave();
  updateSuggest();
};
$("note-body").onclick = updateSuggest;
$("note-body").onkeyup = (e) => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) updateSuggest();
};
$("note-body").onblur = () => setTimeout(closeSuggest, 150);
$("note-body").onkeydown = (e) => {
  const box = $("note-suggest");
  if (!box.hidden) {
    const options = [...box.children];
    const active = options.findIndex((o) => o.classList.contains("active"));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = (active + (e.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options.forEach((o, i) => o.classList.toggle("active", i === next));
      return;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      options[Math.max(0, active)]?.click();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeSuggest();
      return;
    }
  }
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    // Lists continue on Enter; an empty item ends the list.
    const ta = e.target,
      s = ta.selectionStart;
    if (s !== ta.selectionEnd) return;
    const start = ta.value.lastIndexOf("\n", s - 1) + 1;
    const m = /^(\s*)([-*] \[[ xX]\] |[-*] |(\d+)\. )(.*)$/.exec(ta.value.slice(start, s));
    if (!m) return;
    e.preventDefault();
    if (!m[4].trim()) ta.setRangeText("", start, s, "end");
    else {
      const marker = m[3] ? Number(m[3]) + 1 + ". " : m[2].replace(/\[[xX]\]/, "[ ]");
      ta.setRangeText("\n" + m[1] + marker, s, s, "end");
    }
    queueNoteSave();
  }
};
const inlinePattern =
  /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(~~[^~\n]+~~)|(\*[^*\s][^*\n]*\*)|(\[\[[^\[\]\n]+\]\])|(https?:\/\/[^\s<>()]+[^\s<>().,!?;:])|(^|[^\p{L}\p{N}_#/&])(#\p{L}[\p{L}\p{N}_-]*)/gu;
function inline(el, text) {
  let last = 0;
  for (const m of text.matchAll(inlinePattern)) {
    let start = m.index,
      t = m[0];
    if (m[8]) {
      start += m[7].length;
      t = m[8];
    }
    if (start > last) el.append(document.createTextNode(text.slice(last, start)));
    if (m[1]) el.append(node("code", "", t.slice(1, -1)));
    else if (m[2]) {
      const b = node("strong");
      inline(b, t.slice(2, -2));
      el.append(b);
    } else if (m[3]) el.append(node("s", "", t.slice(2, -2)));
    else if (m[4]) {
      const i = node("em");
      inline(i, t.slice(1, -1));
      el.append(i);
    } else if (m[5]) {
      const [target, alias] = t.slice(2, -2).split("|");
      const name = target.trim();
      const a = node("button", "wikilink" + (findNoteTitle(name) ? "" : " missing"), (alias || target).trim());
      a.type = "button";
      a.title = findNoteTitle(name) ? "Открыть «" + name + "»" : "Создать заметку «" + name + "»";
      a.onclick = () => followLink(name);
      el.append(a);
    } else if (m[6]) {
      const a = node("a", "", t);
      a.href = t;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      el.append(a);
    } else if (m[8]) {
      const tag = t.slice(1).toLowerCase();
      const b = node("button", "note-tag", t);
      b.type = "button";
      b.onclick = async () => {
        await flushNote();
        if (noteDirty || noteConflict) return;
        $("note-dialog").close();
        filterTag(tag);
      };
      el.append(b);
    }
    last = start + t.length;
  }
  if (last < text.length) el.append(document.createTextNode(text.slice(last)));
}
function renderPreview() {
  const box = $("note-preview");
  box.replaceChildren();
  const body = $("note-body").value;
  if (!body.trim()) {
    box.append(node("p", "note-empty", "Пусто. Нажми «Текст», чтобы написать."));
    return;
  }
  let list = null,
    kind = null,
    para = null,
    code = null;
  const close = () => {
    list = kind = para = null;
  };
  body.split("\n").forEach((line, index) => {
    if (code) {
      if (/^\s*```/.test(line)) code = null;
      else code.textContent += (code.textContent ? "\n" : "") + line;
      return;
    }
    let m;
    if (/^\s*```/.test(line)) {
      close();
      const pre = node("pre");
      code = node("code");
      pre.append(code);
      box.append(pre);
    } else if (!line.trim()) close();
    else if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) {
      close();
      const h = node("h" + (m[1].length + 1));
      inline(h, m[2]);
      box.append(h);
    } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      close();
      box.append(node("hr"));
    } else if ((m = /^\s*>\s?(.*)$/.exec(line))) {
      close();
      const q = node("blockquote");
      inline(q, m[1]);
      box.append(q);
    } else if ((m = /^\s*[-*] \[( |x|X)\]\s?(.*)$/.exec(line))) {
      if (kind !== "task") {
        close();
        list = node("ul", "task-list");
        kind = "task";
        box.append(list);
      }
      const li = node("li" , m[1] !== " " ? "done" : ""),
        label = node("label"),
        box2 = node("input"),
        span = node("span");
      box2.type = "checkbox";
      box2.checked = m[1] !== " ";
      box2.onchange = () => toggleTask(index, box2.checked);
      inline(span, m[2]);
      label.append(box2, span);
      li.append(label);
      list.append(li);
    } else if ((m = /^\s*(?:[-*]|(\d+)\.)\s+(.*)$/.exec(line))) {
      const want = m[1] ? "ol" : "ul";
      if (kind !== want) {
        close();
        list = node(want);
        kind = want;
        box.append(list);
      }
      const li = node("li");
      inline(li, m[2]);
      list.append(li);
    } else {
      if (!para) {
        close();
        para = node("p");
        box.append(para);
      } else para.append(node("br"));
      inline(para, line);
    }
  });
}
function toggleTask(index, checked) {
  const ta = $("note-body");
  const lines = ta.value.split("\n");
  lines[index] = lines[index].replace(/\[( |x|X)\]/, checked ? "[x]" : "[ ]");
  ta.value = lines.join("\n");
  queueNoteSave(300);
  renderPreview();
}

// Plans ↔ notes
function picker(input, results, search, pick) {
  let timer = 0,
    generation = 0;
  const run = async () => {
    const g = ++generation;
    let items = [];
    try {
      items = await search(input.value.trim());
    } catch (e) {
      if (!e.stale) toast(e.message);
      return;
    }
    if (g !== generation || document.activeElement !== input) return;
    results.replaceChildren();
    for (const it of items) {
      const b = node("button", "", it.label);
      b.type = "button";
      if (it.meta) b.append(node("small", "", it.meta));
      b.onmousedown = (e) => e.preventDefault();
      b.onclick = async () => {
        results.hidden = true;
        input.value = "";
        await pick(it);
      };
      results.append(b);
    }
    if (!items.length) results.append(node("p", "note-empty", input.value.trim() ? "Ничего не нашлось" : "Нечего прикрепить"));
    results.hidden = false;
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(run, 200);
  });
  input.addEventListener("focus", run);
  input.addEventListener("blur", () => setTimeout(() => (results.hidden = true), 150));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !results.hidden) {
      e.preventDefault();
      e.stopPropagation();
      results.hidden = true;
    } else if (e.key === "Enter") {
      e.preventDefault();
      results.querySelector("button")?.click();
    }
  });
}
picker(
  $("note-attach"),
  $("note-attach-results"),
  async (q) => {
    const params = q
      ? new URLSearchParams({ q, limit: "8" })
      : new URLSearchParams({ start: today(), status: "active", limit: "8" });
    const r = await api("/api/events?" + params);
    const have = new Set(
      (currentNote?.id ? currentNote.events : noteDraftEvent ? [noteDraftEvent] : []).map((e) => e.id),
    );
    return r.events
      .filter((e) => !have.has(e.id))
      .map((e) => ({ id: e.id, label: e.title, meta: eventWhen(e), event: e }));
  },
  (it) => attachEvent(it.event),
);
async function attachEvent(e) {
  if (!currentNote) return;
  if (!currentNote.id) {
    noteDraftEvent = compactEvent(e);
    renderNoteSide();
    if ($("note-title").value.trim() || $("note-body").value.trim()) queueNoteSave(0);
    return;
  }
  const note = currentNote;
  await flushNote();
  try {
    const r = await api("/api/notes/attach", { note_id: note.id, event_id: e.id });
    Object.assign(note, { events: r.note.events, revision: r.note.revision });
    notesChanged = true;
    if (note === currentNote) renderNoteSide();
    toast("Прикреплено к плану «" + e.title + "»");
  } catch (err) {
    if (!err.stale) toast(err.message);
  }
}
async function detachEvent(id) {
  if (!currentNote) return;
  if (!currentNote.id) {
    noteDraftEvent = null;
    return renderNoteSide();
  }
  const note = currentNote;
  try {
    const r = await api("/api/notes/attach", { note_id: note.id, event_id: id, attached: false });
    note.events = r.note.events;
    notesChanged = true;
    if (note === currentNote) renderNoteSide();
  } catch (err) {
    if (!err.stale) toast(err.message);
  }
}
async function openLinkedEvent(id) {
  await flushNote();
  if (noteDirty || noteConflict) return;
  if ($("event-dialog").open && currentEvent?.id === id) return $("note-dialog").close();
  try {
    const r = await api("/api/events/get?id=" + id);
    $("note-dialog").close();
    if ($("event-dialog").open) $("event-dialog").close();
    openEvent(r.event);
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
}
async function renderEventNotes(e) {
  $("event-notes").hidden = !e;
  $("event-notes-list").replaceChildren();
  $("event-note-pick").value = "";
  $("event-note-results").hidden = true;
  if (!e) return;
  const version = identityVersion;
  try {
    const r = await api("/api/notes?event_id=" + e.id);
    if (version !== identityVersion || currentEvent?.id !== e.id) return;
    notesState.titles = r.titles;
    if (!notesState.boards.length) notesState.boards = r.boards;
    const list = $("event-notes-list");
    list.replaceChildren();
    for (const n of r.notes) {
      const row = node("div", "note-chip-row");
      row.append(
        chip(n.title, n.excerpt ? n.excerpt.slice(0, 90) : n.board, () => openNote(n.id)),
        iconButton("close", "Открепить заметку «" + n.title + "»", async () => {
          try {
            await api("/api/notes/attach", { note_id: n.id, event_id: e.id, attached: false });
            renderEventNotes(e);
          } catch (err) {
            if (!err.stale) toast(err.message);
          }
        }),
      );
      list.append(row);
    }
    if (!r.notes.length) list.append(node("p", "note-empty", "Заметок пока нет"));
  } catch (err) {
    if (!err.stale) toast(err.message);
  }
}
$("event-note-new").onclick = () => {
  if (currentEvent) openNote(null, { event: currentEvent });
};
picker(
  $("event-note-pick"),
  $("event-note-results"),
  async (q) => {
    const r = await api("/api/notes?" + new URLSearchParams(q ? { q } : {}));
    const id = currentEvent?.id;
    return r.notes
      .filter((n) => !n.events.some((x) => x.id === id))
      .slice(0, 8)
      .map((n) => ({ id: n.id, label: n.title, meta: n.board }));
  },
  async (it) => {
    if (!currentEvent) return;
    try {
      await api("/api/notes/attach", { note_id: it.id, event_id: currentEvent.id });
      toast("Заметка прикреплена");
      renderEventNotes(currentEvent);
      if (view === "planner") loadPlanner();
    } catch (e) {
      if (!e.stale) toast(e.message);
    }
  },
);
$("event-dialog").addEventListener("close", () => {
  if (view === "planner" && privateReady) loadPlanner();
});
async function openPlanNotes(e) {
  try {
    const r = await api("/api/notes?event_id=" + e.id);
    if (r.notes.length === 1) return openNote(r.notes[0].id);
    notesEvent = { id: e.id, title: e.title };
    notesTag = "";
    $("notes-search").value = "";
    setView("notes");
  } catch (err) {
    if (!err.stale) toast(err.message);
  }
}
async function loadHomeNotes() {
  const version = identityVersion;
  try {
    const r = await api("/api/notes");
    if (version !== identityVersion) return;
    notesState = r;
    const list = $("home-notes-list");
    list.replaceChildren();
    for (const n of r.notes.slice(0, 4)) {
      const b = node("button", "home-note");
      b.type = "button";
      if (n.color) b.dataset.color = n.color;
      b.append(node("strong", "", n.title));
      if (n.excerpt) b.append(node("span", "", n.excerpt));
      b.append(node("small", "", (n.pinned ? "Закреплена · " : "") + n.board + " · " + ago(n.updated_at)));
      b.onclick = () => openNote(n.id);
      list.append(b);
    }
    if (!r.notes.length) {
      const box = node("div", "today-empty"),
        mark = node("div", "empty-symbol");
      mark.append(icon("note"));
      const add = node("button", "subtle", "Создать заметку");
      add.onclick = () => openNote(null);
      box.append(mark, node("strong", "", "Заметок пока нет"), node("p", "", "Идеи, списки и конспекты — рядом с планами."), add);
      list.append(box);
    }
  } catch (e) {
    if (!e.stale) toast(e.message);
  }
}

// Obsidian-inspired navigation: file explorer, open tabs and a local links graph.
let notesLayout = prefs.get("notes.layout", "boards"),
  noteTabs = [],
  noteConflict = null;
if (!["boards", "files", "graph"].includes(notesLayout)) notesLayout = "boards";
document.querySelectorAll("[data-notes-layout]").forEach((b) => {
  b.onclick = () => {
    notesLayout = b.dataset.notesLayout;
    prefs.set("notes.layout", notesLayout);
    renderNotes();
  };
});
function rememberNote(n) {
  if (!n.id) return;
  noteTabs = noteTabs.filter((t) => t.id !== n.id);
  noteTabs.push({ id: n.id, title: n.title });
  noteTabs = noteTabs.slice(-8);
}
function renderNoteTabs() {
  const box = $("note-tabs");
  box.replaceChildren();
  for (const t of noteTabs) {
    const b = node("button", "note-tab" + (t.id === currentNote?.id ? " active" : ""));
    b.type = "button";
    b.append(icon("note"), node("span", "", t.title));
    b.setAttribute("aria-current", t.id === currentNote?.id ? "page" : "false");
    b.onclick = () => openNote(t.id);
    box.append(b);
  }
  if (!currentNote?.id) box.append(node("span", "note-tab active", "Новая заметка"));
  const add = iconButton("plus", "Новая заметка", () => openNote(null));
  box.append(add);
}
function renderExplorer() {
  const root = $("note-files");
  root.replaceChildren();
  const all = [...notesState.notes];
  if (currentNote?.id && !all.some((n) => n.id === currentNote.id)) all.push(currentNote);
  for (const b of notesState.boards) {
    const folder = node("details", "note-folder");
    folder.open = true;
    folder.append(node("summary", "", b.name));
    const files = all.filter((n) => n.board === b.name).sort((a, z) => a.title.localeCompare(z.title, "ru"));
    for (const n of files) {
      const button = node("button", "note-file" + (n.id === currentNote?.id ? " active" : ""));
      button.type = "button";
      button.append(icon("note"), node("span", "", n.title));
      button.onclick = () => openNote(n.id);
      folder.append(button);
    }
    if (!files.length) folder.append(node("small", "note-empty", "Пусто"));
    root.append(folder);
  }
}
$("explorer-new").onclick = () => openNote(null, { board: currentNote?.board });
$("note-server-version").onclick = () => {
  if (!noteConflict) return;
  Object.assign(currentNote, noteConflict);
  noteConflict = null;
  noteDirty = false;
  fillNote();
  if (noteMode === "preview") renderPreview();
};
$("note-my-version").onclick = async () => {
  if (!noteConflict) return;
  currentNote.revision = noteConflict.revision;
  noteConflict = null;
  $("note-conflict").hidden = true;
  noteDirty = true;
  await saveNote();
};
function renderVault(root, list) {
  root.className = "notes-board vault-list";
  if (!list.length) return emptyNotes(root, "Хранилище пустое", "Здесь появятся тексты, идеи и списки.", notesBoard);
  const head = node("div", "vault-head");
  head.append(node("span", "", "Название"), node("span", "", "Доска"), node("span", "", "Изменено"));
  root.append(head);
  for (const n of list) {
    const row = node("button", "vault-row");
    row.type = "button";
    const name = node("span", "vault-name");
    name.append(icon(n.pinned ? "pin" : "note"), node("strong", "", n.title));
    if (n.excerpt) name.append(node("small", "", n.excerpt));
    row.append(name, node("span", "vault-board", n.board), node("small", "vault-time", ago(n.updated_at)));
    row.onclick = () => openNote(n.id);
    root.append(row);
  }
}
function renderNotesGraph(root, list) {
  root.className = "notes-board graph-workspace";
  const selected = new Set(list.map((n) => n.id));
  const graph = notesState.graph || { nodes: notesState.titles, edges: [] };
  const all = graph.nodes.filter((n) => selected.has(n.id));
  if (!all.length) return emptyNotes(root, "Нет заметок для графа", "Добавь заметку и свяжи её с другой через [[Название]].", notesBoard);
  const nodes = all.slice(0, 160);
  const ids = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const tip = node("p", "graph-caption", "Свяжи заметки через [[Название]]. Нажми на точку, чтобы открыть текст." + (all.length > 160 ? " Показаны первые 160 — уточни доску или поиск." : ""));
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 900 560");
  svg.setAttribute("class", "notes-graph");
  svg.setAttribute("aria-label", "Связи между заметками");
  const coordinates = new Map();
  const degree = new Map(nodes.map((n) => [n.id, 0]));
  for (const e of edges) {
    degree.set(e.source, degree.get(e.source) + 1);
    degree.set(e.target, degree.get(e.target) + 1);
  }
  const sorted = [...nodes].sort((a, b) => degree.get(b.id) - degree.get(a.id));
  sorted.forEach((n, i) => {
    const angle = i * 2.399963;
    const radius = sorted.length === 1 ? 0 : 215 * Math.sqrt(i / Math.max(1, sorted.length - 1));
    coordinates.set(n.id, { x: 450 + Math.cos(angle) * radius * 1.45, y: 270 + Math.sin(angle) * radius });
  });
  for (const e of edges) {
    const line = document.createElementNS(ns, "line");
    const a = coordinates.get(e.source), b = coordinates.get(e.target);
    for (const [key, value] of Object.entries({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "graph-edge" })) line.setAttribute(key, value);
    line.dataset.source = e.source;
    line.dataset.target = e.target;
    svg.append(line);
  }
  const groups = new Map();
  for (const n of nodes) {
    const p = coordinates.get(n.id);
    const group = document.createElementNS(ns, "g");
    group.setAttribute("class", "graph-node");
    group.setAttribute("transform", "translate(" + p.x + " " + p.y + ")");
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", "Открыть заметку " + n.title);
    const circle = document.createElementNS(ns, "circle");
    circle.setAttribute("r", String(6 + Math.min(7, degree.get(n.id) * 1.5)));
    const label = document.createElementNS(ns, "text");
    label.setAttribute("y", "27");
    label.setAttribute("text-anchor", "middle");
    label.textContent = n.title.length > 24 ? n.title.slice(0, 23) + "…" : n.title;
    const title = document.createElementNS(ns, "title");
    title.textContent = n.title + " · " + (n.board || "");
    group.append(circle, label, title);
    const highlight = (on) => {
      const adjacent = new Set([n.id]);
      for (const e of edges) {
        if (e.source === n.id) adjacent.add(e.target);
        if (e.target === n.id) adjacent.add(e.source);
      }
      for (const [id, g] of groups) g.classList.toggle("dim", on && !adjacent.has(id));
      for (const line of svg.querySelectorAll(".graph-edge")) line.classList.toggle("highlight", on && (Number(line.dataset.source) === n.id || Number(line.dataset.target) === n.id));
    };
    group.onpointerenter = () => highlight(true);
    group.onpointerleave = () => highlight(false);
    group.onfocus = () => highlight(true);
    group.onblur = () => highlight(false);
    group.onclick = () => openNote(n.id);
    group.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openNote(n.id); }
    };
    groups.set(n.id, group);
    svg.append(group);
  }
  root.append(tip, svg, node("p", "graph-caption", plural(nodes.length, ["заметка", "заметки", "заметок"]) + " · " + plural(edges.length, ["связь", "связи", "связей"])));
}
window.addEventListener("beforeunload", (e) => {
  if (noteDirty) { e.preventDefault(); e.returnValue = ""; }
});
// All workspace state must exist before a synchronous Mini App denial can
// clear private UI. The authenticated path usually yields before reaching it.
init();
