/**
 * ClaimCoach Interactive Conversion Tools
 *
 * Four self-contained widgets that mount into placeholder divs.
 * Each supports "mini" (article embed) and "full" (standalone page) modes.
 *
 * Usage:
 *   <div id="cc-tool-sales-tax" data-mode="mini" data-state="Florida"></div>
 *   <script src="/static/tools/tools.js"></script>
 *   <script>ClaimCoachTools.mountAll();</script>
 */

/* global ClaimCoachTools */
(function () {
  "use strict";

  // ── Tool data is injected by the server into a global ──
  // Falls back to fetching /api/tools/data if not present.
  let TOOL_DATA = window.__CC_TOOL_DATA || null;

  const CTA_URL = "https://claimcoach.app";

  // ── Helpers ──

  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); }
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(function (kv) {
      if (kv[0] === "className") e.className = kv[1];
      else if (kv[0] === "innerHTML") e.innerHTML = kv[1];
      else if (kv[0].startsWith("on")) e.addEventListener(kv[0].slice(2).toLowerCase(), kv[1]);
      else e.setAttribute(kv[0], kv[1]);
    });
    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(function (c) {
        if (typeof c === "string") e.appendChild(document.createTextNode(c));
        else if (c) e.appendChild(c);
      });
    }
    return e;
  }

  function fmt(n) {
    return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }
  function fmtD(n) {
    return "$" + fmt(n);
  }

  function stateSelect(selected, id) {
    if (!TOOL_DATA) return el("select", { id: id });
    var s = el("select", { id: id });
    s.appendChild(el("option", { value: "" }, "Select state..."));
    TOOL_DATA.states.forEach(function (st) {
      var o = el("option", { value: st }, st);
      if (st === selected) o.selected = true;
      s.appendChild(o);
    });
    return s;
  }

  // ── TOOL 1: Sales Tax Calculator ──

  function SalesTaxCalculator(container, opts) {
    var mode = opts.mode || "mini";
    var preState = opts.state || "";

    function calcTax(amount, state, keeping, salvage) {
      var data = TOOL_DATA.state_sales_tax[state];
      if (!data) return null;
      var rate = data.avg_combined / 100;
      var taxable = keeping && salvage > 0 ? amount - salvage : amount;
      return {
        state: state,
        rate_pct: data.avg_combined,
        state_rate_pct: data.state_rate,
        taxable: taxable,
        tax_owed: Math.round(taxable * rate * 100) / 100,
        note: data.note || null,
      };
    }

    function renderMini() {
      container.innerHTML = "";
      var wrap = el("div", { className: "cc-tool cc-tool-mini cc-tax-calc" });

      wrap.appendChild(el("div", { className: "cc-tool-title" }, "\ud83d\udcb0 How much sales tax are you owed?"));

      var form = el("div", { className: "cc-tool-form" });
      form.appendChild(el("label", { for: "cc-tax-amount" }, "Your settlement offer"));
      var amtInput = el("input", { id: "cc-tax-amount", type: "number", placeholder: "14500", min: "0", step: "100" });
      form.appendChild(amtInput);

      form.appendChild(el("label", { for: "cc-tax-state" }, "Your state"));
      form.appendChild(stateSelect(preState, "cc-tax-state"));

      wrap.appendChild(form);

      var resultBox = el("div", { className: "cc-tool-result", style: "display:none" });
      wrap.appendChild(resultBox);

      var btn = el("button", { className: "cc-tool-btn", onClick: function () {
        var amt = parseFloat(amtInput.value);
        var st = $("#cc-tax-state", wrap).value;
        if (!amt || !st) return;
        var r = calcTax(amt, st, false, 0);
        if (!r) return;
        resultBox.style.display = "block";
        resultBox.innerHTML = "<div class='cc-result-label'>" + r.state + " sales tax (" + r.rate_pct + "% avg):</div>" +
          "<div class='cc-result-amount'>You're owed: " + fmtD(r.tax_owed) + "</div>" +
          (r.note ? "<div class='cc-result-note'>" + r.note + "</div>" : "") +
          "<div class='cc-result-subtext'>If this wasn't in your offer, your insurer owes you this on top of the settlement amount.</div>";
      } }, "Calculate");
      wrap.appendChild(btn);

      wrap.appendChild(el("div", { className: "cc-tool-footer" }, [
        el("span", {}, "Sales tax is just one of 6-8 line items most offers miss."),
        el("a", { href: CTA_URL, className: "cc-tool-cta", target: "_blank" }, "See all missing line items \u2192"),
      ]));

      container.appendChild(wrap);
    }

    function renderFull() {
      container.innerHTML = "";
      var wrap = el("div", { className: "cc-tool cc-tool-full cc-tax-calc" });

      wrap.appendChild(el("h1", { className: "cc-tool-page-title" }, "Sales Tax Recovery Calculator"));
      wrap.appendChild(el("p", { className: "cc-tool-subtitle" }, "See what your insurer owes you on a replacement vehicle."));

      var form = el("div", { className: "cc-tool-form" });

      form.appendChild(el("label", { for: "cc-tax-amount" }, "Your settlement offer"));
      var amtInput = el("input", { id: "cc-tax-amount", type: "number", placeholder: "14500", min: "0", step: "100" });
      form.appendChild(amtInput);

      form.appendChild(el("label", { for: "cc-tax-state" }, "Your state"));
      form.appendChild(stateSelect(preState, "cc-tax-state"));

      form.appendChild(el("label", {}, "Keeping the vehicle?"));
      var keepRow = el("div", { className: "cc-radio-row" });
      var keepNo = el("input", { type: "radio", name: "cc-tax-keep", id: "cc-keep-no", value: "no", checked: "checked" });
      keepRow.appendChild(keepNo);
      keepRow.appendChild(el("label", { for: "cc-keep-no" }, "No"));
      var keepYes = el("input", { type: "radio", name: "cc-tax-keep", id: "cc-keep-yes", value: "yes" });
      keepRow.appendChild(keepYes);
      keepRow.appendChild(el("label", { for: "cc-keep-yes" }, "Yes"));
      form.appendChild(keepRow);

      var salvageRow = el("div", { id: "cc-salvage-row", style: "display:none" });
      salvageRow.appendChild(el("label", { for: "cc-salvage" }, "Salvage value"));
      var salvInput = el("input", { id: "cc-salvage", type: "number", placeholder: "2000", min: "0", step: "100" });
      salvageRow.appendChild(salvInput);
      form.appendChild(salvageRow);

      keepYes.addEventListener("change", function () { salvageRow.style.display = "block"; });
      keepNo.addEventListener("change", function () { salvageRow.style.display = "none"; });

      wrap.appendChild(form);

      var resultBox = el("div", { className: "cc-tool-result cc-full-result", style: "display:none" });
      wrap.appendChild(resultBox);

      var btn = el("button", { className: "cc-tool-btn", onClick: function () {
        var amt = parseFloat(amtInput.value);
        var st = $("#cc-tax-state", wrap).value;
        var keeping = keepYes.checked;
        var salv = parseFloat(salvInput.value) || 0;
        if (!amt || !st) return;
        var r = calcTax(amt, st, keeping, salv);
        if (!r) return;

        var stateItems = TOOL_DATA.checklist_state_specific[st] || [];

        resultBox.style.display = "block";
        resultBox.innerHTML =
          "<h2>Your Results</h2>" +
          "<table class='cc-result-table'>" +
          "<tr><td>State sales tax rate:</td><td>" + r.state_rate_pct + "%</td></tr>" +
          "<tr><td>Average combined rate:</td><td>" + r.rate_pct + "%</td></tr>" +
          (keeping ? "<tr><td>Taxable amount (after salvage):</td><td>" + fmtD(r.taxable) + "</td></tr>" : "") +
          "<tr class='cc-result-total'><td>Sales tax owed:</td><td>" + fmtD(r.tax_owed) + "</td></tr>" +
          "</table>" +
          (r.note ? "<div class='cc-callout cc-callout-info'>" + r.note + "</div>" : "") +
          (r.tax_owed > 0 ? "<div class='cc-callout cc-callout-warn'>If your settlement offer does NOT include this amount, your insurer is shortchanging you by " + fmtD(r.tax_owed) + ".</div>" : "") +
          "<div class='cc-more-items'>" +
          "<p>But sales tax is just ONE of the line items insurers miss. Most offers are also missing:</p>" +
          "<ul>" +
          "<li>Title &amp; registration fees ($200\u2013$500)</li>" +
          "<li>Comparable vehicle adjustments ($500\u2013$2,000)</li>" +
          "<li>Dealer documentation fees ($300\u2013$800)</li>" +
          "<li>Loss of use compensation ($200\u2013$1,500)</li>" +
          "</ul>" +
          "</div>" +
          "<div class='cc-cta-box'>" +
          "<p>Get your complete analysis. See every line item your offer is missing.</p>" +
          "<a href='" + CTA_URL + "' class='cc-tool-cta-btn' target='_blank'>Analyze my full offer \u2014 free, 5 minutes \u2192</a>" +
          "</div>";
      } }, "Calculate \u2192");
      wrap.appendChild(btn);

      container.appendChild(wrap);
    }

    if (mode === "full") renderFull(); else renderMini();
  }

  // ── TOOL 2: Settlement Checklist ──

  function SettlementChecklist(container, opts) {
    var mode = opts.mode || "mini";
    var preState = opts.state || "";

    function renderMini() {
      container.innerHTML = "";
      var wrap = el("div", { className: "cc-tool cc-tool-mini cc-checklist" });
      wrap.appendChild(el("div", { className: "cc-tool-title" }, "\ud83d\udccb Does your offer include all of these?"));

      var list = el("div", { className: "cc-check-list" });
      TOOL_DATA.checklist_standard.forEach(function (item) {
        if (item.is_right) return;
        var row = el("label", { className: "cc-check-row" });
        var cb = el("input", { type: "checkbox", "data-id": item.id, "data-low": String(item.range_low || 0), "data-high": String(item.range_high || 0) });
        row.appendChild(cb);
        row.appendChild(el("span", { className: "cc-check-label" }, item.label));
        row.appendChild(el("span", { className: "cc-check-range" }, fmtD(item.range_low || 0) + "\u2013" + fmtD(item.range_high || 0)));
        list.appendChild(row);
      });
      wrap.appendChild(list);

      var resultBox = el("div", { className: "cc-tool-result cc-check-result" });
      wrap.appendChild(resultBox);

      function update() {
        var unchecked = 0, lo = 0, hi = 0;
        $$("input[type=checkbox]", list).forEach(function (cb) {
          if (!cb.checked) {
            unchecked++;
            lo += parseInt(cb.dataset.low) || 0;
            hi += parseInt(cb.dataset.high) || 0;
          }
        });
        if (unchecked > 0) {
          resultBox.innerHTML = "<strong>" + unchecked + " unchecked item" + (unchecked > 1 ? "s" : "") + "</strong><br>" +
            "You could be owed: <strong>" + fmtD(lo) + " \u2013 " + fmtD(hi) + "</strong>";
          resultBox.style.display = "block";
        } else {
          resultBox.innerHTML = "<strong>All items checked.</strong> Your offer looks more complete than most.";
        }
      }
      update();
      list.addEventListener("change", update);

      wrap.appendChild(el("a", { href: CTA_URL, className: "cc-tool-cta", target: "_blank" }, "Get your exact breakdown \u2192"));
      container.appendChild(wrap);
    }

    function renderFull() {
      container.innerHTML = "";
      var wrap = el("div", { className: "cc-tool cc-tool-full cc-checklist" });

      wrap.appendChild(el("h1", { className: "cc-tool-page-title" }, "Total Loss Settlement Checklist"));
      wrap.appendChild(el("p", { className: "cc-tool-subtitle" }, "Check every line item that's included in your offer."));

      var topRow = el("div", { className: "cc-tool-form cc-form-inline" });
      topRow.appendChild(el("label", { for: "cc-cl-state" }, "Your state:"));
      topRow.appendChild(stateSelect(preState, "cc-cl-state"));
      topRow.appendChild(el("label", { for: "cc-cl-amount" }, "Offer amount:"));
      var amtInput = el("input", { id: "cc-cl-amount", type: "number", placeholder: "12000", min: "0", step: "100" });
      topRow.appendChild(amtInput);
      wrap.appendChild(topRow);

      var listWrap = el("div", { className: "cc-check-list-full" });
      wrap.appendChild(listWrap);

      var resultBox = el("div", { className: "cc-tool-result cc-full-result", style: "display:none" });
      wrap.appendChild(resultBox);

      function rebuild() {
        listWrap.innerHTML = "";
        var st = $("#cc-cl-state", wrap).value;
        var amt = parseFloat(amtInput.value) || 0;

        // Standard items
        listWrap.appendChild(el("h3", {}, "Required Line Items"));
        TOOL_DATA.checklist_standard.forEach(function (item) {
          var row = el("label", { className: "cc-check-row-full" });
          var cb = el("input", { type: "checkbox", "data-id": item.id });
          row.appendChild(cb);
          var info = el("div", { className: "cc-check-info" });
          info.appendChild(el("strong", {}, item.label));
          info.appendChild(el("p", {}, item.description));
          if (!item.is_right && (item.range_low || item.range_high)) {
            if (item.id === "sales_tax" && item.calculate && st && amt) {
              var tax = TOOL_DATA.state_sales_tax[st];
              if (tax) {
                var computed = Math.round(amt * tax.avg_combined / 100);
                info.appendChild(el("span", { className: "cc-est" }, "Estimated: " + fmtD(computed)));
              }
            } else {
              info.appendChild(el("span", { className: "cc-est" }, "Estimated: " + fmtD(item.range_low) + "\u2013" + fmtD(item.range_high)));
            }
          }
          row.appendChild(info);
          listWrap.appendChild(row);
        });

        // State-specific items
        var stateItems = TOOL_DATA.checklist_state_specific[st] || [];
        if (stateItems.length) {
          listWrap.appendChild(el("h3", {}, st + "-Specific Items"));
          stateItems.forEach(function (item) {
            var row = el("label", { className: "cc-check-row-full" });
            if (!item.is_right) {
              row.appendChild(el("input", { type: "checkbox", "data-id": item.id }));
            } else {
              row.appendChild(el("span", { className: "cc-info-icon", innerHTML: "\u2139\ufe0f" }));
            }
            var info = el("div", { className: "cc-check-info" });
            info.appendChild(el("strong", {}, item.label));
            info.appendChild(el("p", {}, item.description));
            if (item.statute) info.appendChild(el("cite", {}, item.statute));
            if (item.range_low || item.range_high) {
              info.appendChild(el("span", { className: "cc-est" }, "Estimated: " + fmtD(item.range_low || 0) + "\u2013" + fmtD(item.range_high || 0)));
            }
            row.appendChild(info);
            listWrap.appendChild(row);
          });
        }

        updateResult();
      }

      function updateResult() {
        var checked = [];
        $$("input[type=checkbox]", listWrap).forEach(function (cb) {
          if (cb.checked) checked.push(cb.dataset.id);
        });
        var st = $("#cc-cl-state", wrap).value || "";
        var amt = parseFloat(amtInput.value) || 0;
        if (!st) return;

        var allItems = TOOL_DATA.checklist_standard.concat(TOOL_DATA.checklist_state_specific[st] || []);
        var missing = 0, lo = 0, hi = 0;
        allItems.forEach(function (item) {
          if (item.is_right || checked.indexOf(item.id) >= 0) return;
          missing++;
          if (item.id === "sales_tax" && item.calculate && amt) {
            var tax = TOOL_DATA.state_sales_tax[st];
            if (tax) { var t = Math.round(amt * tax.avg_combined / 100); lo += t; hi += t; }
          } else {
            lo += item.range_low || 0;
            hi += item.range_high || 0;
          }
        });

        resultBox.style.display = "block";
        resultBox.innerHTML =
          "<h2>Your Results</h2>" +
          "<div class='cc-result-grid'>" +
          "<div class='cc-rg-item'><span class='cc-rg-val'>" + checked.length + "</span> Included</div>" +
          "<div class='cc-rg-item cc-rg-warn'><span class='cc-rg-val'>" + missing + "</span> Missing</div>" +
          "</div>" +
          (missing > 0 && amt ? "<div class='cc-callout cc-callout-warn'>Estimated gap: <strong>" + fmtD(lo) + " \u2013 " + fmtD(hi) + "</strong><br>Your " + fmtD(amt) + " offer should be " + fmtD(amt + lo) + " \u2013 " + fmtD(amt + hi) + ".</div>" : "") +
          "<div class='cc-cta-box'><p>Get exact dollar amounts for each missing item. ClaimCoach analyzes your specific offer against " + (st || "your state's") + " regulations and market data.</p>" +
          "<a href='" + CTA_URL + "' class='cc-tool-cta-btn' target='_blank'>Analyze my offer \u2014 free, 5 minutes \u2192</a></div>";
      }

      $("#cc-cl-state", wrap).addEventListener("change", rebuild);
      amtInput.addEventListener("input", rebuild);
      listWrap.addEventListener("change", updateResult);

      rebuild();
      container.appendChild(wrap);
    }

    if (mode === "full") renderFull(); else renderMini();
  }

  // ── TOOL 3: Fairness Quiz ──

  function FairnessQuiz(container, opts) {
    var mode = opts.mode || "mini";
    var preState = opts.state || "";

    function renderMini() {
      container.innerHTML = "";
      var wrap = el("div", { className: "cc-tool cc-tool-mini cc-quiz" });
      wrap.appendChild(el("div", { className: "cc-tool-title" }, "\ud83e\udd14 Is your insurance offer fair?"));
      wrap.appendChild(el("p", { className: "cc-tool-desc" }, "Answer 3 quick questions to find out."));

      var form = el("div", { className: "cc-tool-form" });
      form.appendChild(el("label", { for: "cc-q-state" }, "Your state"));
      form.appendChild(stateSelect(preState, "cc-q-state"));
      form.appendChild(el("label", { for: "cc-q-offer" }, "Settlement offer"));
      var offerInput = el("input", { id: "cc-q-offer", type: "number", placeholder: "12000", min: "0", step: "100" });
      form.appendChild(offerInput);
      form.appendChild(el("label", {}, "Does it include sales tax?"));
      var taxRow = el("div", { className: "cc-radio-row" });
      var taxYes = el("input", { type: "radio", name: "cc-q-tax", id: "cc-qt-yes", value: "yes" });
      taxRow.appendChild(taxYes); taxRow.appendChild(el("label", { for: "cc-qt-yes" }, "Yes"));
      var taxNo = el("input", { type: "radio", name: "cc-q-tax", id: "cc-qt-no", value: "no", checked: "checked" });
      taxRow.appendChild(taxNo); taxRow.appendChild(el("label", { for: "cc-qt-no" }, "No"));
      var taxUnsure = el("input", { type: "radio", name: "cc-q-tax", id: "cc-qt-unsure", value: "unsure" });
      taxRow.appendChild(taxUnsure); taxRow.appendChild(el("label", { for: "cc-qt-unsure" }, "Not sure"));
      form.appendChild(taxRow);
      wrap.appendChild(form);

      var resultBox = el("div", { className: "cc-tool-result", style: "display:none" });
      wrap.appendChild(resultBox);

      var btn = el("button", { className: "cc-tool-btn", onClick: function () {
        var st = $("#cc-q-state", wrap).value;
        var amt = parseFloat(offerInput.value);
        if (!st || !amt) return;
        var hasTax = taxYes.checked;
        var r = scoreFairness(st, amt, hasTax, []);
        resultBox.style.display = "block";
        resultBox.innerHTML =
          "<div class='cc-score-bar'><div class='cc-score-fill' style='width:" + r.score + "%'></div></div>" +
          "<div class='cc-score-label'>Your offer score: <strong>" + r.score + " / 100</strong></div>" +
          "<div class='cc-result-subtext'>" + r.severity + "</div>" +
          (r.gap_high > 0 ? "<div class='cc-result-amount'>Estimated gap: " + fmtD(r.gap_low) + " \u2013 " + fmtD(r.gap_high) + "</div>" : "");
      } }, "Check my offer");
      wrap.appendChild(btn);

      wrap.appendChild(el("a", { href: CTA_URL, className: "cc-tool-cta", target: "_blank" }, "Get your detailed analysis with exact amounts \u2192"));
      container.appendChild(wrap);
    }

    function renderFull() {
      container.innerHTML = "";
      var wrap = el("div", { className: "cc-tool cc-tool-full cc-quiz" });
      wrap.appendChild(el("h1", { className: "cc-tool-page-title" }, "Is My Total Loss Offer Fair?"));
      wrap.appendChild(el("p", { className: "cc-tool-subtitle" }, "5 questions. 60 seconds. Find out if you're leaving money on the table."));

      var step = 0;
      var answers = {};

      var formArea = el("div", { className: "cc-quiz-steps" });
      var resultBox = el("div", { className: "cc-tool-result cc-full-result", style: "display:none" });

      function showStep() {
        formArea.innerHTML = "";
        var stepEl = el("div", { className: "cc-quiz-step" });
        var progress = el("div", { className: "cc-quiz-progress" }, "Question " + (step + 1) + " of 5");
        stepEl.appendChild(progress);

        if (step === 0) {
          stepEl.appendChild(el("label", { for: "cc-qf-state" }, "What state are you in?"));
          stepEl.appendChild(stateSelect(preState, "cc-qf-state"));
          stepEl.appendChild(el("button", { className: "cc-tool-btn", onClick: function () {
            answers.state = $("#cc-qf-state", stepEl).value;
            if (answers.state) { step++; showStep(); }
          } }, "Next \u2192"));
        } else if (step === 1) {
          stepEl.appendChild(el("label", { for: "cc-qf-year" }, "Vehicle year"));
          stepEl.appendChild(el("input", { id: "cc-qf-year", type: "number", placeholder: "2020", min: "1990", max: "2026" }));
          stepEl.appendChild(el("label", { for: "cc-qf-make" }, "Make"));
          stepEl.appendChild(el("input", { id: "cc-qf-make", type: "text", placeholder: "Honda" }));
          stepEl.appendChild(el("label", { for: "cc-qf-model" }, "Model"));
          stepEl.appendChild(el("input", { id: "cc-qf-model", type: "text", placeholder: "Accord" }));
          stepEl.appendChild(el("button", { className: "cc-tool-btn", onClick: function () {
            answers.year = $("#cc-qf-year", stepEl).value;
            answers.make = $("#cc-qf-make", stepEl).value;
            answers.model = $("#cc-qf-model", stepEl).value;
            step++; showStep();
          } }, "Next \u2192"));
        } else if (step === 2) {
          stepEl.appendChild(el("label", { for: "cc-qf-offer" }, "What did they offer you?"));
          stepEl.appendChild(el("input", { id: "cc-qf-offer", type: "number", placeholder: "12000", min: "0", step: "100" }));
          stepEl.appendChild(el("button", { className: "cc-tool-btn", onClick: function () {
            answers.offer = parseFloat($("#cc-qf-offer", stepEl).value);
            if (answers.offer) { step++; showStep(); }
          } }, "Next \u2192"));
        } else if (step === 3) {
          stepEl.appendChild(el("label", {}, "Does your offer include sales tax?"));
          var row = el("div", { className: "cc-radio-row" });
          ["Yes", "No", "Not sure"].forEach(function (opt) {
            var id = "cc-qf-tax-" + opt.toLowerCase().replace(/\s/g, "");
            row.appendChild(el("input", { type: "radio", name: "cc-qf-tax", id: id, value: opt.toLowerCase() }));
            row.appendChild(el("label", { for: id }, opt));
          });
          stepEl.appendChild(row);
          stepEl.appendChild(el("button", { className: "cc-tool-btn", onClick: function () {
            var sel = $("input[name=cc-qf-tax]:checked", stepEl);
            answers.has_tax = sel ? sel.value === "yes" : false;
            step++; showStep();
          } }, "Next \u2192"));
        } else if (step === 4) {
          stepEl.appendChild(el("label", {}, "Which of these are in your offer? (check all)"));
          var items = [
            { id: "title_registration", label: "Title and registration fees" },
            { id: "comparable_adjustments", label: "Comparable vehicle adjustments" },
            { id: "dealer_fees", label: "Dealer fees" },
            { id: "loss_of_use", label: "Loss of use / rental gap" },
            { id: "aftermarket", label: "Aftermarket upgrades" },
          ];
          items.forEach(function (it) {
            var row = el("label", { className: "cc-check-row" });
            row.appendChild(el("input", { type: "checkbox", "data-id": it.id }));
            row.appendChild(el("span", {}, it.label));
            stepEl.appendChild(row);
          });
          stepEl.appendChild(el("button", { className: "cc-tool-btn", onClick: function () {
            answers.included = [];
            $$("input[type=checkbox]:checked", stepEl).forEach(function (cb) {
              answers.included.push(cb.dataset.id);
            });
            showResult();
          } }, "See my score \u2192"));
        }

        formArea.appendChild(stepEl);
      }

      function showResult() {
        formArea.style.display = "none";
        var r = scoreFairness(answers.state, answers.offer, answers.has_tax, answers.included || []);
        var vehicle = [answers.year, answers.make, answers.model].filter(Boolean).join(" ");
        var color = r.score >= 70 ? "#22c55e" : r.score >= 40 ? "#f59e0b" : "#ef4444";

        resultBox.style.display = "block";
        resultBox.innerHTML =
          "<h2>Your Offer Score</h2>" +
          "<div class='cc-score-big'>" +
          "<div class='cc-score-bar-big'><div class='cc-score-fill' style='width:" + r.score + "%;background:" + color + "'></div></div>" +
          "<div class='cc-score-number' style='color:" + color + "'>" + r.score + " / 100</div>" +
          "</div>" +
          "<p>Your " + fmtD(answers.offer) + " offer" + (vehicle ? " for a " + vehicle : "") + (answers.state ? " in " + answers.state : "") +
          " is likely <strong>" + fmtD(r.gap_low) + " \u2013 " + fmtD(r.gap_high) + " below fair value</strong>.</p>" +
          "<p>" + r.severity + "</p>" +
          (r.missing.length ? "<h3>Missing items detected:</h3><ul>" + r.missing.map(function (m) {
            return "<li><strong>" + m.item + "</strong>: " + (m.amount ? "~" + fmtD(m.amount) : fmtD(m.low || 0) + "\u2013" + fmtD(m.high || 0)) + "</li>";
          }).join("") + "</ul>" : "") +
          "<div class='cc-cta-box'>" +
          "<p>Get your detailed analysis with exact amounts" + (answers.state ? " and " + answers.state + "-specific citations" : "") + " you can send to your adjuster.</p>" +
          "<a href='" + CTA_URL + "' class='cc-tool-cta-btn' target='_blank'>Get my full analysis \u2192</a>" +
          "</div>";
      }

      wrap.appendChild(formArea);
      wrap.appendChild(resultBox);
      showStep();
      container.appendChild(wrap);
    }

    function scoreFairness(state, offer, hasTax, included) {
      var score = 50;
      var missing = [];
      var gLow = 0, gHigh = 0;

      if (hasTax) {
        score += 15;
      } else {
        score -= 15;
        var taxData = TOOL_DATA.state_sales_tax[state];
        if (taxData && taxData.avg_combined > 0) {
          var t = Math.round(offer * taxData.avg_combined / 100);
          missing.push({ item: "Sales tax", amount: t });
          gLow += t; gHigh += t;
        }
      }

      var stdItems = [
        { id: "title_registration", label: "Title & registration fees", low: 200, high: 500 },
        { id: "comparable_adjustments", label: "Comparable vehicle adjustments", low: 500, high: 2000 },
        { id: "dealer_fees", label: "Dealer documentation fees", low: 300, high: 800 },
        { id: "loss_of_use", label: "Loss of use / rental gap", low: 200, high: 1500 },
        { id: "aftermarket", label: "Aftermarket upgrades", low: 0, high: 5000 },
      ];
      var incCount = 0;
      stdItems.forEach(function (it) {
        if (included.indexOf(it.id) >= 0) {
          score += 7; incCount++;
        } else {
          missing.push({ item: it.label, low: it.low, high: it.high });
          gLow += it.low; gHigh += it.high;
        }
      });
      if (stdItems.length - incCount >= 3) score -= 10;
      score = Math.max(0, Math.min(100, score));

      var severity;
      if (score >= 80) severity = "Your offer looks reasonable, but there may still be room to negotiate.";
      else if (score >= 60) severity = "Your offer is below average. Several common line items appear to be missing.";
      else if (score >= 40) severity = "Your offer is significantly below what you're likely owed.";
      else severity = "Your offer is very low. You're likely leaving thousands on the table.";

      return { score: score, severity: severity, missing: missing, gap_low: gLow, gap_high: gHigh };
    }

    if (mode === "full") renderFull(); else renderMini();
  }

  // ── TOOL 4: Car Worth Estimator ──

  function CarWorthEstimator(container, opts) {
    var mode = opts.mode || "mini";

    function render() {
      container.innerHTML = "";
      var isFull = mode === "full";
      var wrap = el("div", { className: "cc-tool " + (isFull ? "cc-tool-full" : "cc-tool-mini") + " cc-car-worth" });

      if (isFull) {
        wrap.appendChild(el("h1", { className: "cc-tool-page-title" }, "What's Your Totaled Car Worth?"));
        wrap.appendChild(el("p", { className: "cc-tool-subtitle" }, "Get an independent estimate to compare against your insurer's offer."));
      } else {
        wrap.appendChild(el("div", { className: "cc-tool-title" }, "\ud83d\ude97 What's your car actually worth?"));
      }

      var form = el("div", { className: "cc-tool-form" });
      form.appendChild(el("label", { for: "cc-cw-year" }, "Year"));
      var yearInput = el("input", { id: "cc-cw-year", type: "number", placeholder: "2020", min: "1990", max: "2026" });
      form.appendChild(yearInput);
      form.appendChild(el("label", { for: "cc-cw-make" }, "Make"));
      var makeInput = el("input", { id: "cc-cw-make", type: "text", placeholder: "Honda" });
      form.appendChild(makeInput);
      form.appendChild(el("label", { for: "cc-cw-model" }, "Model"));
      var modelInput = el("input", { id: "cc-cw-model", type: "text", placeholder: "Accord" });
      form.appendChild(modelInput);
      form.appendChild(el("label", { for: "cc-cw-miles" }, "Mileage"));
      var milesInput = el("input", { id: "cc-cw-miles", type: "number", placeholder: "45000", min: "0", step: "1000" });
      form.appendChild(milesInput);
      wrap.appendChild(form);

      var resultBox = el("div", { className: "cc-tool-result", style: "display:none" });
      wrap.appendChild(resultBox);

      var btn = el("button", { className: "cc-tool-btn", onClick: function () {
        var year = yearInput.value;
        var make = makeInput.value.trim();
        var model = modelInput.value.trim();
        var miles = milesInput.value;
        if (!year || !make || !model) return;

        var kbbUrl = "https://www.kbb.com/";
        var vehicle = year + " " + make + " " + model;

        resultBox.style.display = "block";
        resultBox.innerHTML =
          "<div class='cc-result-label'>Look up your " + vehicle + ":</div>" +
          "<div class='cc-value-links'>" +
          "<a href='" + kbbUrl + "' target='_blank' class='cc-ext-link'>Kelley Blue Book (KBB) \u2192</a>" +
          "<a href='https://www.nadaguides.com/' target='_blank' class='cc-ext-link'>NADA Guides \u2192</a>" +
          "<a href='https://www.edmunds.com/' target='_blank' class='cc-ext-link'>Edmunds \u2192</a>" +
          "</div>" +
          "<div class='cc-result-subtext'>Compare what you find to your insurer's offer. If there's a gap, you have leverage to negotiate.</div>" +
          (isFull ? "<div class='cc-callout cc-callout-info'><strong>Pro tip:</strong> Print screenshots from at least 2 of these sources. Adjusters take written evidence more seriously than verbal claims.</div>" : "") +
          "<div class='cc-cta-box'>" +
          "<p>Want ClaimCoach to run this comparison for you \u2014 including comps, condition adjustments, and state-specific rules?</p>" +
          "<a href='" + CTA_URL + "' class='cc-tool-cta-btn' target='_blank'>Get my full analysis \u2192</a>" +
          "</div>";
      } }, "Check value \u2192");
      wrap.appendChild(btn);

      container.appendChild(wrap);
    }

    render();
  }

  // ── Mount System ──

  var TOOLS = {
    "sales-tax": SalesTaxCalculator,
    "sales_tax_calculator": SalesTaxCalculator,
    "checklist": SettlementChecklist,
    "settlement_checklist": SettlementChecklist,
    "quiz": FairnessQuiz,
    "fairness_quiz": FairnessQuiz,
    "car-worth": CarWorthEstimator,
    "car_worth_estimator": CarWorthEstimator,
  };

  function mountAll() {
    var containers = $$("[data-cc-tool]");
    containers.forEach(function (c) {
      var toolName = c.getAttribute("data-cc-tool");
      var Tool = TOOLS[toolName];
      if (!Tool) return;
      Tool(c, {
        mode: c.getAttribute("data-mode") || "mini",
        state: c.getAttribute("data-state") || "",
      });
    });
  }

  function mount(selector, toolName, opts) {
    var c = $(selector);
    var Tool = TOOLS[toolName];
    if (c && Tool) Tool(c, opts || {});
  }

  // ── Async init: load data then mount ──

  function init() {
    if (TOOL_DATA) {
      mountAll();
      return;
    }
    // Try to load from inline script tag
    var dataEl = document.getElementById("cc-tool-data");
    if (dataEl) {
      try {
        TOOL_DATA = JSON.parse(dataEl.textContent);
        mountAll();
        return;
      } catch (e) { /* fall through */ }
    }
    // Fetch from API
    fetch("/api/tools/data")
      .then(function (r) { return r.json(); })
      .then(function (d) { TOOL_DATA = d; mountAll(); })
      .catch(function () { console.warn("ClaimCoach tools: failed to load data"); });
  }

  // ── Export ──
  window.ClaimCoachTools = {
    mountAll: mountAll,
    mount: mount,
    init: init,
    SalesTaxCalculator: SalesTaxCalculator,
    SettlementChecklist: SettlementChecklist,
    FairnessQuiz: FairnessQuiz,
    CarWorthEstimator: CarWorthEstimator,
  };

  // Auto-init on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
