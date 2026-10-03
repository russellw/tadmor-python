"use strict";
// Tadmor's only script: the line-item editor on document and order forms
// (domain §13 D2). Totals are previewed with exact decimal arithmetic on
// scaled BigInts, rounded half away from zero exactly as the server does
// (domain §2), so the preview never disagrees with what is saved.

// A decimal is {n: BigInt, s: scale}: the value n / 10^s.
function parseDecimal(text) {
  text = (text || "").trim();
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(text)) return null;
  const neg = text.startsWith("-");
  const [whole, frac = ""] = text.replace(/^[+-]/, "").split(".");
  const n = BigInt((whole || "0") + frac);
  return { n: neg ? -n : n, s: frac.length };
}

// Round to `scale` places, half away from zero.
function roundTo(d, scale) {
  if (d.s <= scale) return { n: d.n * 10n ** BigInt(scale - d.s), s: scale };
  const div = 10n ** BigInt(d.s - scale);
  const neg = d.n < 0n;
  const abs = neg ? -d.n : d.n;
  let q = abs / div;
  if ((abs % div) * 2n >= div) q += 1n;
  return { n: neg ? -q : q, s: scale };
}

const mul = (a, b) => ({ n: a.n * b.n, s: a.s + b.s });
const add = (a, b) => {
  const s = Math.max(a.s, b.s);
  return { n: roundTo(a, s).n + roundTo(b, s).n, s };
};
const ZERO = { n: 0n, s: 4 };

// "1,234.50" or "10.0011": exact, grouped, at least two places.
function formatAmount(d) {
  const neg = d.n < 0n;
  let digits = (neg ? -d.n : d.n).toString().padStart(d.s + 1, "0");
  const whole = digits.slice(0, digits.length - d.s);
  let frac = digits.slice(digits.length - d.s).replace(/0+$/, "");
  while (frac.length < 2) frac += "0";
  return (neg ? "-" : "") + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + frac;
}

function lineMoney(row) {
  const get = (cls) => parseDecimal(row.querySelector(cls).value);
  const q = get(".qty"), p = get(".price") || ZERO, r = get(".rate") || ZERO;
  if (!q) return null;
  const qty = roundTo(q, 4), price = roundTo(p, 4), rate = roundTo(r, 4);
  const subtotal = roundTo(mul(qty, price), 4);
  const tax = roundTo(mul(mul(mul(qty, price), rate), { n: 1n, s: 2 }), 4); // × rate / 100
  return { subtotal, tax, total: add(subtotal, tax) };
}

function setupLineEditor(form) {
  const data = JSON.parse(document.getElementById("client-data").textContent);
  const tbody = form.querySelector("#lines tbody");
  const template = form.querySelector("#line-template");

  function recompute() {
    let sub = ZERO, tax = ZERO, tot = ZERO;
    for (const row of tbody.querySelectorAll("tr.line")) {
      const m = lineMoney(row);
      for (const [cls, key] of [[".out-subtotal", "subtotal"], [".out-tax", "tax"], [".out-total", "total"]]) {
        row.querySelector(cls).textContent = m ? formatAmount(m[key]) : "";
      }
      if (m) {
        sub = add(sub, m.subtotal); tax = add(tax, m.tax); tot = add(tot, m.total);
      }
    }
    form.querySelector("#sum-subtotal").textContent = formatAmount(sub);
    form.querySelector("#sum-tax").textContent = formatAmount(tax);
    form.querySelector("#sum-total").textContent = formatAmount(tot);
  }

  function setTaxRate(row) {
    const code = row.querySelector(".taxcode").value;
    if (code && data.taxes[code] !== undefined) row.querySelector(".rate").value = data.taxes[code];
  }

  tbody.addEventListener("change", (e) => {
    const row = e.target.closest("tr.line");
    if (!row) return;
    if (e.target.classList.contains("product")) {
      const p = data.products[e.target.value];
      if (p) {
        row.querySelector(".desc").value = p.description;
        if (p.tax_code) {
          row.querySelector(".taxcode").value = p.tax_code;
          setTaxRate(row);
        }
        if (p.price !== null) row.querySelector(".price").value = p.price;
        if (p.account !== null) row.querySelector(".account").value = p.account;
      }
    }
    if (e.target.classList.contains("taxcode")) setTaxRate(row);
    recompute();
  });
  tbody.addEventListener("input", recompute);
  tbody.addEventListener("click", (e) => {
    if (!e.target.classList.contains("remove")) return;
    const row = e.target.closest("tr.line");
    if (tbody.querySelectorAll("tr.line").length > 1) {
      row.remove();
    } else {
      row.querySelectorAll("input").forEach((i) => (i.value = i.classList.contains("qty") ? "1" : i.classList.contains("rate") ? "0" : ""));
      row.querySelectorAll("select").forEach((s) => (s.value = ""));
    }
    recompute();
  });
  form.querySelector("#add-line").addEventListener("click", () => {
    tbody.appendChild(template.content.cloneNode(true));
    recompute();
  });

  // A new document takes the party's currency, unless the user chose one.
  const party = form.querySelector('select[name="customer_id"], select[name="supplier_id"]');
  const currency = form.querySelector('select[name="currency_code"]');
  let currencyTouched = false;
  if (currency) currency.addEventListener("change", () => (currencyTouched = true));
  if (party && currency) {
    party.addEventListener("change", () => {
      const c = data.partyCurrency[party.value];
      if (c && !currencyTouched) currency.value = c;
    });
  }
  recompute();
}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("docform");
  if (form) setupLineEditor(form);
});
