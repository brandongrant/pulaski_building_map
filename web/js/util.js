/* Pulaski County Building Map — shared helpers */

export const $ = (id) => document.getElementById(id);
export const fmt = new Intl.NumberFormat("en-US");
export const fmtUSD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function normAddrJS(s) {
  return String(s || "").toUpperCase().replace(/[^A-Z0-9 ]+/g, " ")
    .replace(/\s+/g, " ").trim();
}

export function titleCase(s) {
  return String(s).toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());
}
