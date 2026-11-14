/* hotdeal-bot — 목록 렌더링 · 필터 · 테마 (외부 라이브러리 없음) */
(() => {
  "use strict";

  const SITE_URL = "https://dgnslabs.github.io/hotdeal-bot/";
  const ALLOWED_HOSTS = ["link.coupang.com", "coupa.ng", "www.coupang.com", "coupang.com"];
  const ALLOWED_IMAGE_HOSTS = ["coupang.com", "coupangcdn.com"];
  const THEME_KEY = "hotdeal-theme";

  const CAT_LABEL = {
    refurb: "반품·리퍼",
    it: "가전·IT",
    living: "생활·식품",
    beauty: "뷰티·패션",
    etc: "기타",
  };

  const state = {
    deals: [],
    source: "demo",
    generatedAt: null,
    category: "all",
    query: "",
    sort: "discount",
  };

  const el = {
    grid: document.getElementById("grid"),
    empty: document.getElementById("empty"),
    chips: document.getElementById("chips"),
    search: document.getElementById("search"),
    sort: document.getElementById("sort"),
    updated: document.getElementById("updated"),
    statsTotal: document.getElementById("stat-total"),
    statsRefurb: document.getElementById("stat-refurb"),
    statsMax: document.getElementById("stat-max"),
    demoBanner: document.getElementById("demo-banner"),
    notice: document.getElementById("notice"),
    themeToggle: document.getElementById("theme-toggle"),
    themeIcon: document.getElementById("theme-icon"),
    reset: document.getElementById("reset-filters"),
    year: document.getElementById("year"),
  };

  /* ---------------------------------------------------------------- 테마 */
  function applyTheme(theme) {
    const resolved = theme === "auto"
      ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : theme;
    document.documentElement.dataset.theme = resolved;
    if (el.themeIcon) el.themeIcon.textContent = resolved === "light" ? "☀️" : "🌙";
    if (el.themeToggle) {
      el.themeToggle.setAttribute("aria-label",
        resolved === "light" ? "다크 모드로 전환" : "라이트 모드로 전환");
    }
  }

  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch { /* 사생활 보호 모드 등 */ }
    applyTheme(saved || "auto");
    el.themeToggle?.addEventListener("click", () => {
      const current = document.documentElement.dataset.theme;
      const next = current === "light" ? "dark" : "light";
      applyTheme(next);
      try { localStorage.setItem(THEME_KEY, next); } catch { /* 무시 */ }
    });
  }

  /* ---------------------------------------------------------------- 유틸 */
  const numFmt = new Intl.NumberFormat("ko-KR");

  function won(value) {
    return Number.isFinite(value) && value > 0 ? `${numFmt.format(value)}원` : "가격 미확인";
  }

  function safeUrl(value, hosts = ALLOWED_HOSTS) {
    try {
      const url = new URL(value, window.location.href);
      if (url.protocol !== "https:") return null;
      if (!hosts.some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))) {
        return null;
      }
      return url.href;
    } catch {
      return null;
    }
  }

  function formatKst(iso) {
    if (!iso) return null;
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return null;
    const parts = new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul", month: "numeric", day: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(date);
    const get = (type) => parts.find((p) => p.type === type)?.value || "";
    return `${get("month")}/${get("day")} ${get("hour")}:${get("minute")}`;
  }

  function firstSeenValue(deal) {
    const time = Date.parse(deal.first_seen || deal.last_seen || "");
    return Number.isNaN(time) ? 0 : time;
  }

  /* ---------------------------------------------------------------- 카드 */
  function buildCard(deal) {
    const card = document.createElement("article");
    card.className = "card" + (deal.demo ? " is-demo" : "");
    card.dataset.category = deal.category_key || "etc";

    const media = document.createElement("div");
    media.className = "card__media";

    const image = safeUrl(deal.image, ALLOWED_IMAGE_HOSTS);
    if (image) {
      const img = document.createElement("img");
      img.src = image;
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.addEventListener("error", () => {
        img.remove();
        const ph = document.createElement("span");
        ph.className = "ph";
        ph.setAttribute("aria-hidden", "true");
        ph.textContent = "🛍️";
        media.prepend(ph);
      });
      media.appendChild(img);
    } else {
      const ph = document.createElement("span");
      ph.className = "ph";
      ph.setAttribute("aria-hidden", "true");
      ph.textContent = deal.demo ? "🧪" : "🛍️";
      media.appendChild(ph);
    }

    const flags = document.createElement("div");
    flags.className = "card__flags";
    const addFlag = (text, cls) => {
      const span = document.createElement("span");
      span.className = "flag" + (cls ? ` ${cls}` : "");
      span.textContent = text;
      flags.appendChild(span);
    };
    if (deal.demo) addFlag("샘플", "flag--demo");
    if (deal.category_key === "refurb" && !deal.demo) addFlag("반품·리퍼", "flag--refurb");
    if (!deal.demo && (deal.tags || []).includes("골드박스")) addFlag("골드박스");
    if (flags.children.length) media.appendChild(flags);

    if (Number(deal.discount_rate) > 0) {
      const save = document.createElement("span");
      save.className = "card__save";
      save.textContent = `${Math.round(deal.discount_rate)}% 할인`;
      media.appendChild(save);
    }

    const body = document.createElement("div");
    body.className = "card__body";

    const cat = document.createElement("span");
    cat.className = "card__cat";
    cat.textContent = deal.demo
      ? deal.category
      : (deal.category_name || deal.category || CAT_LABEL[deal.category_key] || "기타");
    body.appendChild(cat);

    const title = document.createElement("h3");
    title.className = "card__title";
    title.textContent = deal.name || "상품명 없음";
    title.title = deal.name || "";
    body.appendChild(title);

    const priceRow = document.createElement("div");
    priceRow.className = "card__price";
    const price = document.createElement("strong");
    price.className = "price";
    price.textContent = deal.demo ? "가격 정보 없음" : won(deal.price);
    priceRow.appendChild(price);
    if (!deal.demo && Number(deal.original_price) > Number(deal.price)) {
      const orig = document.createElement("span");
      orig.className = "price__orig";
      orig.textContent = won(deal.original_price);
      priceRow.appendChild(orig);
    }
    body.appendChild(priceRow);

    if (deal.discount_source === "history" && !deal.demo) {
      const note = document.createElement("span");
      note.className = "price__note";
      note.textContent = "관측 최고가 대비";
      priceRow.appendChild(note);
    }

    const pills = [];
    if ((deal.tags || []).includes("로켓배송")) pills.push(["로켓배송", "pill--rocket"]);
    if ((deal.tags || []).includes("무료배송")) pills.push(["무료배송", ""]);
    if (pills.length) {
      const meta = document.createElement("div");
      meta.className = "card__meta";
      pills.forEach(([text, cls]) => {
        const pill = document.createElement("span");
        pill.className = "pill" + (cls ? ` ${cls}` : "");
        pill.textContent = text;
        meta.appendChild(pill);
      });
      body.appendChild(meta);
    }

    const href = deal.demo ? null : safeUrl(deal.url);
    if (href) {
      const cta = document.createElement("a");
      cta.className = "card__cta";
      cta.href = href;
      cta.target = "_blank";
      cta.rel = "nofollow sponsored noopener noreferrer";
      cta.textContent = "쿠팡에서 보기 ↗";
      body.appendChild(cta);
    } else {
      const cta = document.createElement("span");
      cta.className = "card__cta";
      cta.setAttribute("aria-disabled", "true");
      cta.textContent = deal.demo ? "준비 중" : "링크 확인 필요";
      body.appendChild(cta);
    }

    card.append(media, body);
    return card;
  }

  function demoDeals() {
    return [
      {
        id: "demo-1", demo: true, category_key: "refurb", category: "반품·리퍼",
        name: "예시 카드 — 리퍼·반품 특가 상품이 이 자리에 표시됩니다",
        tags: ["로켓배송"],
      },
      {
        id: "demo-2", demo: true, category_key: "it", category: "가전·IT",
        name: "예시 카드 — 가전·IT 특가 상품과 할인율 뱃지가 표시됩니다",
        tags: ["골드박스"],
      },
      {
        id: "demo-3", demo: true, category_key: "living", category: "생활·식품",
        name: "예시 카드 — 생활·식품 특가는 정가와 할인가를 함께 보여 줍니다",
        tags: ["무료배송"],
      },
    ];
  }

  /* ---------------------------------------------------------------- 렌더 */
  function visibleDeals() {
    let list = state.deals.slice();
    if (state.category !== "all") {
      list = list.filter((deal) => (deal.category_key || "etc") === state.category);
    }
    if (state.query) {
      const query = state.query.toLowerCase();
      list = list.filter((deal) => String(deal.name || "").toLowerCase().includes(query));
    }
    const rate = (deal) => (Number.isFinite(Number(deal.discount_rate)) ? Number(deal.discount_rate) : -1);
    const price = (deal) => (Number.isFinite(Number(deal.price)) ? Number(deal.price) : Infinity);
    if (state.sort === "price") {
      list.sort((a, b) => price(a) - price(b));
    } else if (state.sort === "new") {
      list.sort((a, b) => firstSeenValue(b) - firstSeenValue(a));
    } else {
      list.sort((a, b) => (rate(b) - rate(a)) || (price(a) - price(b)));
    }
    return list;
  }

  function render() {
    const list = visibleDeals();
    el.grid.replaceChildren(...list.map(buildCard));
    el.grid.setAttribute("aria-busy", "false");
    const isEmpty = list.length === 0;
    el.empty.hidden = !isEmpty;
    el.grid.hidden = isEmpty;

    const live = state.deals.filter((deal) => !deal.demo);
    el.statsTotal.textContent = live.length ? `${live.length}건` : "준비 중";
    const refurb = live.filter((deal) => deal.category_key === "refurb").length;
    el.statsRefurb.textContent = live.length ? `${refurb}건` : "–";
    const maxRate = live.reduce((acc, deal) => Math.max(acc, Number(deal.discount_rate) || 0), 0);
    el.statsMax.textContent = maxRate > 0 ? `${Math.round(maxRate)}%` : "–";
  }

  function renderMeta() {
    const stamp = formatKst(state.generatedAt);
    el.updated.textContent = stamp ? `${stamp} 업데이트` : "첫 수집 대기 중";
    el.updated.title = state.generatedAt ? `마지막 수집: ${state.generatedAt}` : "아직 수집된 데이터가 없습니다";
    const isDemo = state.source !== "live" || state.deals.length === 0;
    el.demoBanner.hidden = !isDemo;
    if (isDemo) el.grid.dataset.demo = "true";
  }

  /* ---------------------------------------------------------------- 이벤트 */
  function initControls() {
    el.chips?.addEventListener("click", (event) => {
      const button = event.target.closest(".chip");
      if (!button) return;
      state.category = button.dataset.category || "all";
      el.chips.querySelectorAll(".chip").forEach((chip) => {
        const active = chip === button;
        chip.classList.toggle("is-active", active);
        chip.setAttribute("aria-pressed", active ? "true" : "false");
      });
      render();
    });

    let timer = null;
    el.search?.addEventListener("input", (event) => {
      const value = event.target.value || "";
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        state.query = value.trim();
        render();
      }, 140);
    });

    el.sort?.addEventListener("change", (event) => {
      state.sort = event.target.value || "discount";
      render();
    });

    el.reset?.addEventListener("click", () => {
      state.category = "all";
      state.query = "";
      state.sort = "discount";
      if (el.search) el.search.value = "";
      if (el.sort) el.sort.value = "discount";
      el.chips?.querySelectorAll(".chip").forEach((chip) => {
        const active = chip.dataset.category === "all";
        chip.classList.toggle("is-active", active);
        chip.setAttribute("aria-pressed", active ? "true" : "false");
      });
      render();
    });
  }

  /* ---------------------------------------------------------------- 시작 */
  async function load() {
    try {
      const response = await fetch(`data/deals.json?v=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const deals = Array.isArray(payload.deals) ? payload.deals.filter((deal) => deal && deal.id) : [];
      state.source = payload.source || (deals.length ? "live" : "demo");
      state.generatedAt = payload.generated_at || null;
      state.deals = state.source === "live" && deals.length ? deals : [];
      if (payload.price_notice) el.notice.textContent = payload.price_notice;
    } catch (error) {
      console.warn("딜 데이터를 읽지 못했습니다:", error);
      state.source = "demo";
      state.deals = [];
    }

    if (!state.deals.length) {
      state.deals = demoDeals();
      state.source = "demo";
    }

    renderMeta();
    render();
  }

  function boot() {
    initTheme();
    initControls();
    if (el.year) el.year.textContent = String(new Date().getFullYear());
    if (el.notice && !el.notice.textContent.trim()) {
      el.notice.textContent = "표시 가격·재고는 수집 시점 기준이며 실시간으로 달라질 수 있습니다.";
    }
    load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  // 참고용으로 사이트 주소를 노출(디버깅·공유 편의)
  window.HOTDEAL_SITE = SITE_URL;
})();
