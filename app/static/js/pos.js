// POS screen (Alpine.js component). The browser only keeps product/unit/qty/
// discount text; every price and total comes from the server (/pos/quote).
"use strict";

const STORAGE_KEYS = { sale: "pos-carts-v1", quote: "pos-quote-carts-v1" };

function toMilli(text) {
  const n = Number(String(text).replace(/,/g, "").trim());
  return Number.isFinite(n) ? Math.round(n * 1000) : NaN;
}
function fromMilli(milli) {
  return String(milli / 1000);
}
function toSatang(text) {
  const n = Number(String(text).replace(/,/g, "").trim());
  return Number.isFinite(n) ? Math.round(n * 100) : NaN;
}
function moneyText(satang) {
  const sign = satang < 0 ? "-" : "";
  const v = Math.abs(satang);
  return sign + Math.floor(v / 100).toLocaleString("en-US") + "." + String(v % 100).padStart(2, "0");
}
function nowLabel() {
  const d = new Date();
  return "ลูกค้า " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "HX-Request": "true" },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { window.location = "/login"; throw new Error("login"); }
  return res.json();
}

function posApp(config) {
  return {
    config,
    carts: [],
    active: 0,
    quote: null,
    quoteSeq: 0,
    search: "",
    results: null,
    resultsFor: "",
    highlight: -1,
    remember: true,
    toast: "",
    modal: null, // "paste" | "pay" | "done" | "savequote"
    custSearch: "",
    custResults: [],
    qsave: { customer_name: "", valid_days: 7, note: "", pin: "", error: "", busy: false },
    paste: { text: "", rows: [], loading: false, remember: true, error: "" },
    pay: { type: "cash", cash: "", buyer: { name: "", address: "", tax_id: "", branch: "" }, wantBuyer: false, pin: "", error: "", busy: false },
    done: null,

    // ---- lifecycle ------------------------------------------------------------
    get isQuote() { return this.config.mode === "quote"; },
    get storageKey() { return STORAGE_KEYS[this.isQuote ? "quote" : "sale"]; },
    init() {
      try {
        const saved = JSON.parse(localStorage.getItem(this.storageKey) || "null");
        if (saved && Array.isArray(saved.carts) && saved.carts.length) {
          this.carts = saved.carts;
          this.active = Math.min(saved.active || 0, saved.carts.length - 1);
        }
      } catch (e) { /* storage unavailable: start fresh */ }
      if (!this.carts.length) this.newCart();
      if (this.config.reissue) this.loadReissue(this.config.reissue);
      if (this.config.fromQuote) this.loadQuotation(this.config.fromQuote, "sell");
      if (this.config.editQuote) this.loadQuotation(this.config.editQuote, "edit");
      if (this.config.customer) { this.newCart(); this.cart.customer = this.config.customer; history.replaceState(null, "", location.pathname + (this.isQuote ? "?mode=quote" : "")); }
      this.refresh();
      this.$nextTick(() => this.$refs.search && this.$refs.search.focus());
    },
    save() {
      try { localStorage.setItem(this.storageKey, JSON.stringify({ carts: this.carts, active: this.active })); } catch (e) {}
    },
    get cart() { return this.carts[this.active]; },
    get lines() { return this.cart ? this.cart.lines : []; },
    get totals() { return this.quote && this.quote.totals; },
    get canPay() { return this.lines.length > 0 && this.quote && this.quote.ok; },
    get locked() { return !!(this.cart && this.cart.quotationId); },

    // ---- carts (held bills, kept on this computer only) ------------------------
    newCart() {
      this.carts.push({ label: nowLabel(), lines: [], bill_discount: "", replaces: null, replacesDoc: "",
                        customer: null, quotationId: null, quoteDoc: "", editQuoteId: null, quoteMeta: null });
      this.active = this.carts.length - 1;
      this.refresh();
    },
    switchCart(i) { this.active = i; this.refresh(); },
    closeCart(i) {
      const c = this.carts[i];
      if (c.lines.length && !confirm("ทิ้งบิล \"" + c.label + "\" ?")) return;
      this.carts.splice(i, 1);
      if (!this.carts.length) this.newCart();
      this.active = Math.min(this.active, this.carts.length - 1);
      this.refresh();
    },
    clearCart() {
      if (!this.lines.length) return;
      if (!confirm("ล้างรายการในบิลนี้ทั้งหมด?")) return;
      Object.assign(this.cart, { lines: [], bill_discount: "", replaces: null, replacesDoc: "", quotationId: null, quoteDoc: "" });
      this.refresh();
    },

    // ---- cart lines --------------------------------------------------------------
    add(productId, unitId, qty = "1") {
      if (this.locked) { this.flash("บิลนี้ขายตามใบเสนอราคา แก้รายการไม่ได้"); return; }
      const existing = this.lines.find((l) => l.product_id === productId && l.unit_id === unitId && !l.discount);
      if (existing) {
        const sum = toMilli(existing.qty) + toMilli(qty);
        existing.qty = Number.isFinite(sum) ? fromMilli(sum) : existing.qty;
      } else {
        this.lines.push({ product_id: productId, unit_id: unitId, qty: String(qty), discount: "" });
      }
      this.refresh();
    },
    step(i, delta) {
      const q = toMilli(this.lines[i].qty) + delta * 1000;
      if (Number.isFinite(q) && q > 0) { this.lines[i].qty = fromMilli(q); this.refresh(); }
    },
    removeLine(i) { this.lines.splice(i, 1); this.refresh(); },
    refresh() {
      this.save();
      clearTimeout(this._quoteTimer);
      this._quoteTimer = setTimeout(() => this.fetchQuote(), 120);
    },
    async fetchQuote() {
      const seq = ++this.quoteSeq;
      if (!this.cart || !this.lines.length) { this.quote = null; return; }
      const data = await postJSON("/pos/quote", {
        lines: this.lines, bill_discount: this.cart.bill_discount,
        customer_id: this.cart.customer ? this.cart.customer.id : null, quotation_id: this.cart.quotationId,
      });
      if (seq === this.quoteSeq) this.quote = data;
    },
    q(i) { return this.quote && this.quote.lines[i]; },

    // ---- search / barcode (F2) ---------------------------------------------------
    onSearchInput() {
      clearTimeout(this._searchTimer);
      this.highlight = -1;
      if (!this.search.trim()) { this.results = null; return; }
      this._searchTimer = setTimeout(() => this.runSearch(), 200);
    },
    async runSearch() {
      const term = this.search;
      const res = await fetch("/pos/search?q=" + encodeURIComponent(term));
      if (res.status === 401) { window.location = "/login"; return; }
      const data = await res.json();
      if (term === this.search) { this.results = data; this.resultsFor = term; }
      return data;
    },
    async searchEnter() {
      if (!this.search.trim()) return;
      if (this.resultsFor !== this.search) { clearTimeout(this._searchTimer); await this.runSearch(); }
      const r = this.results;
      if (!r || !r.candidates.length) { this.flash("ไม่พบสินค้า"); return; }
      if (this.highlight >= 0) return this.pick(r.candidates[this.highlight]);
      if (r.status === "auto") return this.pick(r.candidates[0]);
      this.highlight = 0; // needs a choice: show list, arrows + Enter
    },
    moveHighlight(d) {
      if (!this.results || !this.results.candidates.length) return;
      const n = this.results.candidates.length;
      this.highlight = (this.highlight + d + n) % n;
    },
    pick(c) {
      const r = this.results;
      if (!c.unit_id) { this.flash("สินค้านี้ยังไม่มีหน่วยขาย"); return; }
      this.add(c.product_id, c.unit_id);
      if (r && r.status !== "auto" && this.remember) {
        postJSON("/pos/learn", { text: r.query, product_id: c.product_id }).then((d) => d.message && this.flash(d.message));
      }
      this.flash("เพิ่ม " + c.name);
      this.search = ""; this.results = null; this.highlight = -1;
      this.$refs.search.focus();
    },
    flash(text) {
      this.toast = text;
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => (this.toast = ""), 2200);
    },

    // ---- customer (phase 4) -------------------------------------------------------
    async searchCustomers() {
      const res = await fetch("/pos/customers?q=" + encodeURIComponent(this.custSearch));
      this.custResults = res.ok ? await res.json() : [];
    },
    pickCustomer(c) {
      this.cart.customer = c;
      this.custSearch = ""; this.custResults = [];
      this.refresh();
    },
    clearCustomer() {
      if (this.locked) return;
      this.cart.customer = null;
      if (this.pay.type === "credit") this.pay.type = "cash";
      this.refresh();
    },
    get creditOk() { return !!(this.cart && this.cart.customer); },

    // ---- quotations (phase 4) ------------------------------------------------------
    async loadQuotation(id, how) {
      const res = await fetch("/pos/quotation/" + id);
      if (!res.ok) { this.flash("ไม่พบใบเสนอราคา"); return; }
      const qt = await res.json();
      if (how === "sell" && qt.state !== "open") {
        this.flash(qt.state === "expired" ? "ใบเสนอราคาหมดอายุ — ต่ออายุก่อน" : "ใบเสนอราคานี้แปลงเป็นบิลแล้ว");
        return;
      }
      this.carts.push({
        label: (how === "sell" ? "ขายตาม " : "แก้ ") + qt.doc_no, lines: qt.lines, bill_discount: qt.bill_discount,
        replaces: null, replacesDoc: "", customer: qt.customer,
        quotationId: how === "sell" ? qt.id : null, quoteDoc: qt.doc_no,
        editQuoteId: how === "edit" ? qt.id : null,
        quoteMeta: { customer_name: qt.customer_name, valid_days: qt.valid_days, note: qt.note },
      });
      this.active = this.carts.length - 1;
      history.replaceState(null, "", location.pathname + (this.isQuote ? "?mode=quote" : ""));
      this.refresh();
    },
    openSaveQuote() {
      if (!this.canPay) { this.flash(this.lines.length ? "แก้รายการที่มีข้อผิดพลาดก่อน" : "ยังไม่มีสินค้า"); return; }
      const m = this.cart.quoteMeta || {};
      this.qsave = { customer_name: m.customer_name || "", valid_days: m.valid_days || 7, note: m.note || "",
                     pin: "", error: "", busy: false };
      this.modal = "savequote";
    },
    async submitQuote() {
      if (this.qsave.busy) return;
      this.qsave.busy = true; this.qsave.error = "";
      try {
        const body = {
          lines: this.lines, bill_discount: this.cart.bill_discount,
          customer_id: this.cart.customer ? this.cart.customer.id : null,
          customer_name: this.qsave.customer_name, valid_days: Number(this.qsave.valid_days) || 7,
          note: this.qsave.note, owner_pin: this.qsave.pin,
        };
        const url = this.cart.editQuoteId ? "/quotations/" + this.cart.editQuoteId + "/edit" : "/quotations";
        const data = await postJSON(url, body);
        if (!data.ok) { this.qsave.error = data.error; if (data.pin_failed) this.qsave.pin = ""; return; }
        this.carts.splice(this.active, 1);
        if (!this.carts.length) this.newCart(); else this.active = Math.min(this.active, this.carts.length - 1);
        this.save();
        window.location = "/quotations/" + data.id + "?done=saved";
      } finally {
        this.qsave.busy = false;
      }
    },
    checkout() { if (this.isQuote) this.openSaveQuote(); else this.openPay(); },
    get pinNeeded() {
      if (!this.quote) return false;
      return this.quote.discount_pin || (this.pay.type === "credit" && this.quote.credit_pin);
    },
    showBill() {
      const el = document.querySelector(".pos-right");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    },

    // ---- paste from LINE (F4) ----------------------------------------------------
    openPaste() {
      this.modal = "paste";
      this.paste.error = "";
      this.$nextTick(() => this.$refs.pasteText.focus());
    },
    async parsePaste() {
      if (!this.paste.text.trim()) return;
      this.paste.loading = true;
      try {
        const data = await postJSON("/pos/parse", { text: this.paste.text });
        this.paste.rows = data.lines.map((r) => ({
          ...r,
          skip: r.status === "skipped",
          chosen_product: r.product_id,
          chosen_unit: r.unit_id,
        }));
      } finally {
        this.paste.loading = false;
      }
    },
    options(row) { return row.chosen_product ? row.options[String(row.chosen_product)] : null; },
    chooseProduct(row) {
      const opt = this.options(row);
      row.chosen_unit = opt ? (row.unit_text ? opt.unit_match_id : opt.default_unit_id) : null;
    },
    rowReady(row) { return row.skip || (row.chosen_product && row.chosen_unit && toMilli(row.qty) > 0); },
    get pasteReady() {
      const rows = this.paste.rows;
      return rows.length > 0 && rows.every((r) => this.rowReady(r)) && rows.some((r) => !r.skip);
    },
    get pasteCount() { return this.paste.rows.filter((r) => !r.skip).length; },
    addPasted() {
      if (!this.pasteReady) return;
      for (const r of this.paste.rows) {
        if (r.skip) continue;
        this.add(Number(r.chosen_product), Number(r.chosen_unit), r.qty);
        const chosenByStaff = r.status !== "ok" && Number(r.chosen_product) !== r.product_id;
        if (chosenByStaff && this.paste.remember && r.product_text) {
          postJSON("/pos/learn", { text: r.product_text, product_id: Number(r.chosen_product) });
        }
      }
      this.flash("เพิ่ม " + this.pasteCount + " รายการลงบิล");
      this.paste = { text: "", rows: [], loading: false, remember: true, error: "" };
      this.modal = null;
    },

    // ---- payment (F9) ------------------------------------------------------------
    openPay() {
      if (!this.canPay) { this.flash(this.lines.length ? "แก้รายการที่มีข้อผิดพลาดก่อน" : "ยังไม่มีสินค้าในบิล"); return; }
      this.pay.error = "";
      this.pay.cash = "";
      this.pay.pin = "";
      this.pay.wantBuyer = !this.cart.customer && (this.config.buyerRequired || !!this.pay.buyer.name);
      if (this.pay.type === "credit" && !this.cart.customer) this.pay.type = "cash";
      this.modal = "pay";
      this.$nextTick(() => this.$refs.cash && this.$refs.cash.focus());
    },
    get changeText() {
      if (!this.totals) return "";
      const cash = this.pay.cash.trim() ? toSatang(this.pay.cash) : this.totals.total;
      if (!Number.isFinite(cash)) return "";
      return cash >= this.totals.total ? moneyText(cash - this.totals.total) : "รับเงินไม่พอ";
    },
    quickCash(amount) { this.pay.cash = amount === "exact" ? String(this.totals.total / 100) : String(amount); },
    async submitPay() {
      if (this.pay.busy) return;
      this.pay.busy = true;
      this.pay.error = "";
      try {
        const body = {
          lines: this.lines,
          bill_discount: this.cart.bill_discount,
          payment_type: this.pay.type,
          cash_received: this.pay.type === "cash" ? this.pay.cash : "",
          buyer: this.pay.wantBuyer && !this.cart.customer ? this.pay.buyer : null,
          owner_pin: this.pay.pin,
          replaces_sale_id: this.cart.replaces,
          customer_id: this.cart.customer ? this.cart.customer.id : null,
          quotation_id: this.cart.quotationId,
        };
        const data = await postJSON("/pos/checkout", body);
        if (!data.ok) { this.pay.error = data.error; if (data.pin_failed) this.pay.pin = ""; return; }
        this.done = data;
        this.modal = "done";
        this.pay.buyer = { name: "", address: "", tax_id: "", branch: "" };
        this.carts.splice(this.active, 1);
        if (!this.carts.length) this.newCart(); else this.active = Math.min(this.active, this.carts.length - 1);
        this.refresh();
        this.reloadGrid();
      } finally {
        this.pay.busy = false;
      }
    },
    reloadGrid() {
      // Refresh stock figures on the product cards for the selected category.
      const chip = document.querySelector(".chip.active");
      const url = (chip && chip.getAttribute("hx-get")) || "/pos/products";
      if (window.htmx) htmx.ajax("GET", url, "#grid");
    },
    printSale(fmt) {
      if (this.done) window.open("/sales/" + this.done.sale_id + "/print?format=" + fmt, "_blank");
    },
    finishDone() {
      this.modal = null;
      this.done = null;
      this.$nextTick(() => this.$refs.search.focus());
    },

    // ---- reissue after void ------------------------------------------------------
    async loadReissue(saleId) {
      const res = await fetch("/pos/reissue/" + saleId);
      const data = await res.json();
      if (!data.ok) { this.flash(data.error); return; }
      this.carts.push({ label: "ออกใหม่แทน " + data.doc_no, lines: data.lines, bill_discount: data.bill_discount,
                        replaces: saleId, replacesDoc: data.doc_no });
      this.active = this.carts.length - 1;
      history.replaceState(null, "", "/pos");
      this.refresh();
    },

    // ---- keyboard ----------------------------------------------------------------
    onKey(e) {
      if (e.key === "F2") { e.preventDefault(); this.modal = null; this.$refs.search.focus(); }
      else if (e.key === "F4") { e.preventDefault(); this.openPaste(); }
      else if (e.key === "F9") {
        e.preventDefault();
        if (this.modal === "pay") this.submitPay();
        else if (this.modal === "savequote") this.submitQuote();
        else if (!this.modal) this.checkout();
      }
      else if (e.key === "Escape") {
        if (this.modal === "done") this.finishDone();
        else if (this.modal) this.modal = null;
        else if (this.results) { this.search = ""; this.results = null; }
        else this.clearCart();
      } else if (e.key === "Enter" && this.modal === "done") { e.preventDefault(); this.finishDone(); }
    },
  };
}
