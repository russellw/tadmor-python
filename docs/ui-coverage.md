# UI coverage

**Status:** written 2026-10-06, from the tests and a browser walk-through
of the finished UI.

This records how each item of the UI checklist in `spec/domain.md` §13 is
checked. `docs/counterpart-metrics.md` in tadmor asks for a walk-through,
item by item, before a counterpart's figures count; this is the record to
walk through.

**How it was checked.**

- **Test** means `tadmor/tests/test_ui.py` drives it with Django's test
  client: a real session, forms posted, and the pages read back (the test
  client skips the CSRF check).
  It runs with `make test`. Each test names the items it covers.
- **Browser** means a scripted walk-through in headless Chromium on
  2026-10-06, at `221447a`, against a server holding the conformance
  suite's data. It used tadmor's Playwright install from outside this repo,
  so it is not repeatable from here. It signed in, then loaded every screen
  reachable by links (163 pages over 118 routes) with no script error,
  console error, failed request, or Content Security Policy violation. On
  a new sales invoice and a new purchase bill it chose a party, which set
  the party's currency, and a product, which filled the description,
  price, account, tax code, and rate. It added a hand-written line with
  awkward decimals and a tax code, which set the rate, added a third line
  and removed it, saved, and found the previewed total equal to the saved
  one to the ten-thousandth. Then it deleted the draft, cancelling the
  confirmation once (kept) and confirming it once (deleted). 26 checks, all
  passing.
- **Crawl** means the screen loaded cleanly in that walk-through, with the
  conformance data behind it, but nothing asserts its content.

## General

| Item | Checked |
| ---- | ------- |
| G1 | Test (`test_login_is_the_only_page_without_a_session`: redirect to sign-in, the refusal message, the case-insensitive email, the return to the page asked for), Browser |
| G2 | Test (`test_login_is_the_only_page_without_a_session`: the name shown, sign-out ends the session) |
| G3 | Test (`test_every_navigation_link_works`: every sidebar link loads), Browser (every screen reachable by links) |
| G4 | Test (`test_admin_actions_are_hidden_from_users`: no Users link, 403, read-only settings, no Unpost) |
| G5 | Test (`test_refusals_show_the_server_message`: validation, an unknown country, posting a zero total) |
| G6 | Test (`test_deletes_ask_first`: a confirmation page, nothing deleted until confirmed), Browser (Cancel keeps the draft, confirming deletes it) |
| G7 | Test (`test_amounts_are_exact`: 1.00005 × 10.0001 shown as 10.0011, with the currency), Browser (the editor's preview equals the saved total) |
| G8 | Test (`test_unknown_address_is_not_found`: an unknown path and an unknown id) |

## Home

| Item | Checked |
| ---- | ------- |
| H1 | Test (`test_home_shows_outstanding_and_overdue`: the outstanding amount with its currency) |
| H2, H4 | Crawl |
| H3 | Test (`test_home_shows_outstanding_and_overdue`: the overdue invoice links to it) |
| H5 | Test (`test_home_shows_outstanding_and_overdue`: all six one-step starts) |

## Master data

| Item | Checked |
| ---- | ------- |
| M1, M2 | Test (`test_master_data_forms`: an organization and a customer created; the organization read-only on edit), Crawl (lists) |
| M3, M5 | Test (`test_master_data_forms`: the tax code, payment term, and warehouse forms load), Crawl (lists and the product form) |
| M4 | Test (`test_master_data_forms`: an account is not offered as its own parent) |
| M6 | Test (`test_master_data_forms`: deactivation through the form, Inactive in the list) |
| M7 | Test (`test_users_and_settings`: the password rule, no self-deactivation, a password reset) |
| M8 | Test (`test_users_and_settings`: the FX account shown), Test (`test_admin_actions_are_hidden_from_users`: read-only for others) |

## Documents and payments

| Item | Checked |
| ---- | ------- |
| D1 | Test (`test_invoice_lifecycle`: the new invoice listed), Crawl (all four lists) |
| D2 | Test (`test_invoice_lifecycle`: the form carries the editor's data), Browser (fills from party and product, add and remove lines, exact preview equal to the saved total, on an invoice and a bill) |
| D3 | Test (`test_invoice_lifecycle`: the journal entry linked once posted) |
| D4 | Test (`test_invoice_lifecycle`: Post, Edit, Delete offered on a draft; post, unpost) |
| D5 | Crawl |
| D6 | Test (`test_invoice_lifecycle`: the PDF link, and the PDF served to the session) |
| D7 | Test (`test_invoice_lifecycle`: the email form shows the 501 when sending is off) |
| P1 | Test (`test_payment_and_apply`: the method in the list) |
| P2 | Test (`test_payment_and_apply`: a payment created through the form) |
| P3 | Test (`test_payment_and_apply`: post), Crawl |
| P4 | Test (`test_payment_and_apply`: apply, then the invoice and amount listed) |

## Orders and inventory

| Item | Checked |
| ---- | ------- |
| O1, O3 | Test (`test_order_fulfilment`: the fulfilment status), Crawl |
| O2 | Test (`test_order_fulfilment`: an order created through the form) |
| O4 | Test (`test_order_fulfilment`: confirm; no Cancel once fulfilment has started) |
| O5 | Test (`test_order_fulfilment`: the quantity lowered to 2 of 5, on to the invoice, which cannot be edited) |
| O6 | Test (`test_order_fulfilment`: ship, the movement created) |
| O7 | Crawl |
| S1 | Crawl |
| S2 | Test (`test_stock_movements`: an issue of 3 saved as −3) |
| S3 | Test (`test_stock_movements`: posted, then Unpost offered) |

## Reports and accounting

| Item | Checked |
| ---- | ------- |
| R1 to R4 | Test (`test_reports`: each report loads with its key line; the trial balance links to the ledger; a malformed date shows its error) |
| R5 | Test (`test_reports`: the ledger's amount) |
| R6 | Test (`test_reports`: the journal entry's accounts) |
| R7 | Test (`test_reports`: the customer in the AR aging; AP aging loads) |
| R8 | Test (`test_stock_movements`: the product in the valuation) |
| A1 | Test (`test_periods_and_year_end`: a year and a period created; the next period proposed) |
| A2 | Test (`test_periods_and_year_end`: Retained Earnings proposed, the year closed, 403 for others) |
| A3 | Test (`test_exchange_rates`: create, change, delete) |
| A4 | Test (`test_bank_reconciliation`: only cash accounts offered) |
| A5 | Test (`test_bank_reconciliation`: CSV import and its refusal, Match offered, auto-match, reconcile, Reopen offered), Crawl (manual match, unmatch, line deletion) |
