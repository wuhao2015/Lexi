import "./style.css";
import { api, clearToken, getToken, setToken, ApiError } from "./api";

type User = { id: number; username: string };
type LookupResult = {
  term: string;
  display_term: string;
  primary_translation: string;
  alt_translations: string[] | null;
  translation_source: string;
  vocabulary_id: number;
};
type ReviewItem = {
  id: number;
  term: string;
  display_term: string | null;
  source_lang: string;
  target_lang: string;
};
type Language = { code: string; name: string };

const app = document.querySelector<HTMLDivElement>("#app")!;

let user: User | null = null;
let view: "login" | "register" | "search" | "review" = "login";
let languages: Language[] = [];
let sourceLang = "en";
let targetLang = "zh";
const PAIR_SOURCE_KEY = "lexi_source_lang";
const PAIR_TARGET_KEY = "lexi_target_lang";

function initializeLanguagePair(): void {
  const savedSource = localStorage.getItem(PAIR_SOURCE_KEY);
  const savedTarget = localStorage.getItem(PAIR_TARGET_KEY);
  if (savedSource) sourceLang = savedSource;
  if (savedTarget) targetLang = savedTarget;
}

function persistLanguagePair(): void {
  localStorage.setItem(PAIR_SOURCE_KEY, sourceLang);
  localStorage.setItem(PAIR_TARGET_KEY, targetLang);
}

async function ensureLanguages(): Promise<void> {
  if (languages.length) return;
  try {
    const res = await api<{ items: Language[] }>("/languages");
    languages = res.items;
  } catch {
    languages = [
      { code: "en", name: "English" },
      { code: "zh", name: "Chinese (Mandarin)" },
      { code: "hi", name: "Hindi" },
      { code: "es", name: "Spanish" },
      { code: "fr", name: "French" },
      { code: "ar", name: "Arabic" },
      { code: "bn", name: "Bengali" },
      { code: "pt", name: "Portuguese" },
      { code: "ru", name: "Russian" },
      { code: "ur", name: "Urdu" },
    ];
  }
  if (!languages.some((l) => l.code === sourceLang)) sourceLang = "en";
  if (!languages.some((l) => l.code === targetLang)) targetLang = "zh";
  if (sourceLang === targetLang && languages.length > 1) {
    targetLang = languages.find((l) => l.code !== sourceLang)?.code || targetLang;
  }
  persistLanguagePair();
}

async function refreshUser(): Promise<void> {
  if (!getToken()) {
    user = null;
    return;
  }
  try {
    user = await api<User>("/me");
  } catch {
    user = null;
    clearToken();
  }
}

function navLink(href: string, label: string, active: boolean): string {
  const cls = active ? "active" : "";
  return `<a href="#${href}" class="${cls}">${label}</a>`;
}

function layout(content: string): string {
  const authed = !!user;
  const nav = authed
    ? `
    <nav>
      ${navLink("search", "Search", view === "search")}
      ${navLink("review", "Review", view === "review")}
      <button type="button" class="link" id="btn-logout">Log out</button>
    </nav>`
    : "";
  return `
    <header>
      <h1>Lexi</h1>
      <p class="subtitle">Multilingual dictionary & spaced-style review</p>
      ${nav}
    </header>
    ${content}
  `;
}

function renderLogin(msg = ""): void {
  view = "login";
  app.innerHTML = layout(`
    <div class="card">
      <form id="form-login">
        <div class="field">
          <label for="u">Username</label>
          <input id="u" name="username" type="text" autocomplete="username" required />
        </div>
        <div class="field">
          <label for="p">Password</label>
          <input id="p" name="password" type="password" autocomplete="current-password" required />
        </div>
        ${msg ? `<p class="error">${escapeHtml(msg)}</p>` : ""}
        <button type="submit" class="primary">Log in</button>
        <p style="margin-top:1rem;color:var(--muted);font-size:0.9rem;">
          No account? <a href="#register" style="color:var(--accent)">Register</a>
        </p>
      </form>
    </div>
  `);
  bindLogout();
  document.getElementById("form-login")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target as HTMLFormElement);
    const username = String(fd.get("username") || "").trim();
    const password = String(fd.get("password") || "");
    try {
      const res = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        json: { username, password },
      });
      setToken(res.access_token);
      await refreshUser();
      window.location.hash = "#search";
      render();
    } catch (err) {
      renderLogin(err instanceof ApiError ? err.detail : "Login failed");
    }
  });
  app.querySelector('a[href="#register"]')?.addEventListener("click", (e) => {
    e.preventDefault();
    window.location.hash = "#register";
    render();
  });
}

function renderRegister(msg = ""): void {
  view = "register";
  app.innerHTML = layout(`
    <div class="card">
      <form id="form-reg">
        <div class="field">
          <label for="ru">Username</label>
          <input id="ru" name="username" type="text" autocomplete="username" required minlength="2" />
        </div>
        <div class="field">
          <label for="rp">Password</label>
          <input id="rp" name="password" type="password" autocomplete="new-password" required minlength="6" />
        </div>
        ${msg ? `<p class="error">${escapeHtml(msg)}</p>` : ""}
        <button type="submit" class="primary">Create account</button>
        <p style="margin-top:1rem;color:var(--muted);font-size:0.9rem;">
          <a href="#login" style="color:var(--accent)">Back to login</a>
        </p>
      </form>
    </div>
  `);
  bindLogout();
  document.getElementById("form-reg")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target as HTMLFormElement);
    const username = String(fd.get("username") || "").trim();
    const password = String(fd.get("password") || "");
    try {
      const res = await api<{ access_token: string }>("/auth/register", {
        method: "POST",
        json: { username, password },
      });
      setToken(res.access_token);
      await refreshUser();
      window.location.hash = "#search";
      render();
    } catch (err) {
      renderRegister(err instanceof ApiError ? err.detail : "Registration failed");
    }
  });
  app.querySelector('a[href="#login"]')?.addEventListener("click", (e) => {
    e.preventDefault();
    window.location.hash = "#login";
    render();
  });
}

let lastLookup: LookupResult | null = null;

function renderSearch(msg = "", err = ""): void {
  view = "search";
  const badge =
    lastLookup?.translation_source === "gemini"
      ? '<span class="badge gemini">New (Gemini)</span>'
      : lastLookup?.translation_source === "global_cache"
        ? '<span class="badge cache">Global cache</span>'
        : "";
  const result = lastLookup
    ? `
    <div class="card">
      <div class="result-term">${escapeHtml(lastLookup.display_term)} ${badge}</div>
      <div class="result-zh">${escapeHtml(lastLookup.primary_translation)}</div>
      ${
        lastLookup.alt_translations?.length
          ? `<p style="color:var(--muted);font-size:0.9rem;">Also: ${escapeHtml(lastLookup.alt_translations.join(" · "))}</p>`
          : ""
      }
      <p style="color:var(--muted);font-size:0.85rem;">Saved to your review list.</p>
    </div>`
    : "";

  app.innerHTML = layout(`
    <div class="card">
      <form id="form-lookup">
        <div class="field">
          <label for="source-lang">Source language</label>
          <select id="source-lang" name="source_lang">
            ${languages
              .map(
                (l) =>
                  `<option value="${l.code}" ${l.code === sourceLang ? "selected" : ""}>${escapeHtml(l.name)}</option>`
              )
              .join("")}
          </select>
        </div>
        <div class="field">
          <label for="target-lang">Target language</label>
          <select id="target-lang" name="target_lang">
            ${languages
              .map(
                (l) =>
                  `<option value="${l.code}" ${l.code === targetLang ? "selected" : ""}>${escapeHtml(l.name)}</option>`
              )
              .join("")}
          </select>
        </div>
        <div class="actions" style="margin:0.5rem 0 0.25rem;">
          <button type="button" class="secondary" id="btn-swap-langs">Swap languages</button>
        </div>
        <div class="field">
          <label for="term">Word or phrase</label>
          <input id="term" name="term" type="text" placeholder="e.g. ephemeral" required />
        </div>
        ${err ? `<p class="error">${escapeHtml(err)}</p>` : ""}
        ${msg ? `<p style="color:var(--ok);font-size:0.9rem;">${escapeHtml(msg)}</p>` : ""}
        <button type="submit" class="primary" id="btn-lookup">Look up</button>
      </form>
    </div>
    ${result}
  `);
  bindLogout();
  const sourceSelect = document.getElementById("source-lang") as HTMLSelectElement | null;
  const targetSelect = document.getElementById("target-lang") as HTMLSelectElement | null;
  sourceSelect?.addEventListener("change", () => {
    sourceLang = sourceSelect.value;
    if (sourceLang === targetLang && languages.length > 1) {
      targetLang = languages.find((l) => l.code !== sourceLang)?.code || targetLang;
    }
    persistLanguagePair();
    renderSearch(msg, err);
  });
  targetSelect?.addEventListener("change", () => {
    targetLang = targetSelect.value;
    if (targetLang === sourceLang && languages.length > 1) {
      sourceLang = languages.find((l) => l.code !== targetLang)?.code || sourceLang;
    }
    persistLanguagePair();
    renderSearch(msg, err);
  });
  document.getElementById("btn-swap-langs")?.addEventListener("click", () => {
    const prevSource = sourceLang;
    sourceLang = targetLang;
    targetLang = prevSource;
    persistLanguagePair();
    renderSearch(msg, err);
  });
  document.getElementById("form-lookup")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target as HTMLFormElement);
    const term = String(fd.get("term") || "").trim();
    const selectedSource = String(fd.get("source_lang") || sourceLang);
    const selectedTarget = String(fd.get("target_lang") || targetLang);
    sourceLang = selectedSource;
    targetLang = selectedTarget;
    persistLanguagePair();
    const btn = document.getElementById("btn-lookup") as HTMLButtonElement;
    btn.disabled = true;
    try {
      lastLookup = await api<LookupResult>("/lookup", {
        method: "POST",
        json: { term, source_lang: selectedSource, target_lang: selectedTarget },
      });
      renderSearch("", "");
    } catch (err) {
      lastLookup = null;
      renderSearch("", err instanceof ApiError ? err.detail : "Lookup failed");
    } finally {
      btn.disabled = false;
    }
  });
}

let reviewItem: ReviewItem | null = null;
let reviewFeedback: {
  correct: boolean;
  canonical_answer: string;
  grading_mode: string;
  new_priority: number;
} | null = null;

async function loadReview(): Promise<void> {
  reviewFeedback = null;
  try {
    const q = new URLSearchParams({ source_lang: sourceLang, target_lang: targetLang });
    reviewItem = await api<ReviewItem | null>(`/review/next?${q.toString()}`);
  } catch {
    reviewItem = null;
  }
}

function renderReview(): void {
  view = "review";
  if (!reviewItem) {
    app.innerHTML = layout(`
      <div class="card empty-state">
        <div class="field" style="text-align:left;">
          <label for="review-source-lang">Review source language</label>
          <select id="review-source-lang">
            ${languages
              .map(
                (l) =>
                  `<option value="${l.code}" ${l.code === sourceLang ? "selected" : ""}>${escapeHtml(l.name)}</option>`
              )
              .join("")}
          </select>
        </div>
        <div class="field" style="text-align:left;">
          <label for="review-target-lang">Review target language</label>
          <select id="review-target-lang">
            ${languages
              .map(
                (l) =>
                  `<option value="${l.code}" ${l.code === targetLang ? "selected" : ""}>${escapeHtml(l.name)}</option>`
              )
              .join("")}
          </select>
        </div>
        <div class="actions" style="justify-content:center;margin-top:0.25rem;">
          <button type="button" class="secondary" id="btn-review-swap">Swap languages</button>
        </div>
        <p>Nothing left to review — great work.</p>
        <p style="margin-top:0.5rem;">Look up new words on the Search page to grow your list.</p>
        <div class="actions" style="justify-content:center;margin-top:1.5rem;">
          <a href="#search" class="primary" style="text-decoration:none;display:inline-block;">Search</a>
          <button type="button" class="secondary" id="btn-retry-review">Check again</button>
        </div>
      </div>
    `);
    bindLogout();
    document.getElementById("btn-retry-review")?.addEventListener("click", async () => {
      await loadReview();
      renderReview();
    });
    app.querySelector('a[href="#search"]')?.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.hash = "#search";
      render();
    });
    return;
  }

  const prompt = reviewItem.display_term || reviewItem.term;
  const fb = reviewFeedback;

  app.innerHTML = layout(`
    <div class="card">
      <div class="field">
        <label for="review-source-lang">Review source language</label>
        <select id="review-source-lang">
          ${languages
            .map(
              (l) =>
                `<option value="${l.code}" ${l.code === sourceLang ? "selected" : ""}>${escapeHtml(l.name)}</option>`
            )
            .join("")}
        </select>
      </div>
      <div class="field">
        <label for="review-target-lang">Review target language</label>
        <select id="review-target-lang">
          ${languages
            .map(
              (l) =>
                `<option value="${l.code}" ${l.code === targetLang ? "selected" : ""}>${escapeHtml(l.name)}</option>`
            )
            .join("")}
        </select>
      </div>
      <div class="actions" style="margin:0.5rem 0 0.25rem;">
        <button type="button" class="secondary" id="btn-review-swap">Swap languages</button>
      </div>
      <p style="color:var(--muted);font-size:0.85rem;margin:0 0 0.5rem;">What does this mean?</p>
      <div class="review-prompt">${escapeHtml(prompt)}</div>
      <form id="form-review">
        <div class="field">
          <label for="expl">Your explanation (source or target language)</label>
          <textarea id="expl" name="explanation" required ${fb ? "readonly" : ""}></textarea>
        </div>
        ${
          fb
            ? `
        <div class="feedback ${fb.correct ? "ok" : "bad"}">
          <strong>${fb.correct ? "Correct" : "Not quite"}</strong>
          ${fb.grading_mode === "offline" ? ' <span class="badge">Graded offline</span>' : ""}
          <p style="margin:0.5rem 0 0;">Answer: ${escapeHtml(fb.canonical_answer)}</p>
          <p style="margin:0.35rem 0 0;color:var(--muted);font-size:0.85rem;">Priority now: ${fb.new_priority}</p>
        </div>
        <div class="actions">
          <button type="button" class="primary" id="btn-next">Next</button>
        </div>`
            : `<button type="submit" class="primary" id="btn-submit-review">Submit</button>`
        }
      </form>
    </div>
  `);
  bindLogout();
  const reviewSource = document.getElementById("review-source-lang") as HTMLSelectElement | null;
  const reviewTarget = document.getElementById("review-target-lang") as HTMLSelectElement | null;
  reviewSource?.addEventListener("change", async () => {
    sourceLang = reviewSource.value;
    if (sourceLang === targetLang && languages.length > 1) {
      targetLang = languages.find((l) => l.code !== sourceLang)?.code || targetLang;
    }
    persistLanguagePair();
    await loadReview();
    renderReview();
  });
  reviewTarget?.addEventListener("change", async () => {
    targetLang = reviewTarget.value;
    if (targetLang === sourceLang && languages.length > 1) {
      sourceLang = languages.find((l) => l.code !== targetLang)?.code || sourceLang;
    }
    persistLanguagePair();
    await loadReview();
    renderReview();
  });
  document.getElementById("btn-review-swap")?.addEventListener("click", async () => {
    const prevSource = sourceLang;
    sourceLang = targetLang;
    targetLang = prevSource;
    persistLanguagePair();
    await loadReview();
    renderReview();
  });

  if (!fb) {
    document.getElementById("form-review")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target as HTMLFormElement);
      const explanation = String(fd.get("explanation") || "");
      try {
        reviewFeedback = await api<{
          correct: boolean;
          canonical_answer: string;
          grading_mode: string;
          new_priority: number;
        }>("/review/answer", {
          method: "POST",
          json: { id: reviewItem!.id, explanation },
        });
        renderReview();
      } catch (err) {
        alert(err instanceof ApiError ? err.detail : "Failed to submit");
      }
    });
  } else {
    document.getElementById("btn-next")?.addEventListener("click", async () => {
      await loadReview();
      renderReview();
    });
  }
}

function bindLogout(): void {
  document.getElementById("btn-logout")?.addEventListener("click", () => {
    clearToken();
    user = null;
    lastLookup = null;
    window.location.hash = "#login";
    render();
  });
}

function escapeHtml(s: string): string {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function render(): Promise<void> {
  await ensureLanguages();
  await refreshUser();
  const hash = (window.location.hash || "#login").slice(1).split("?")[0];

  if (!user) {
    if (hash === "register") renderRegister();
    else renderLogin();
    return;
  }

  if (hash === "login" || hash === "register") {
    window.location.hash = "#search";
    await render();
    return;
  }

  if (hash === "review") {
    if (view !== "review" || !reviewItem) await loadReview();
    renderReview();
    return;
  }

  if (hash === "search" || hash === "") {
    renderSearch();
    return;
  }

  window.location.hash = "#search";
  renderSearch();
}

window.addEventListener("hashchange", () => {
  render();
});

initializeLanguagePair();
render();
