# Dashboard design and future data contracts

The Phase 02 dashboard uses Unfold's native sidebar, topbar, account controls and theme handling.
A small `admin/base_site.html` extension adds the branch selector through Unfold's documented
template block. No package template is copied wholesale. The restrained palette is configured in
`config/branding.py`; `static/css/erp-admin.css` holds responsive component styles and a dark
theme. The sidebar is dark teal. A compact welcome strip, four KPI cards and a two-column panel
grid replace the Phase 01 hero. Layout breakpoints reduce cards to two columns and then one.

The user prompt mentioned an approved reference image, but only the text attachment was available
at implementation. Exact comparison with that image cannot be claimed. Browser rendering at
1440/768/390 pixels, light/dark mode, selector and admin form review should be performed when a
browser connection is available. HTML/static checks alone do not establish visual acceptance.

## Component data contracts

`admin/components/kpi_card.html` accepts `title`, `value`, `icon`, `status`, `change`, and
`permission`. `value=None` displays an em dash; `change=None` displays no trend. No business
count, financial amount or percentage is invented. A future data provider should supply a
timezone-aware calculation window, branch scope, permission check, currency and freshness time.

`admin/components/empty_state.html` accepts `icon` and `message`. Membership, attendance,
revenue, classes and expiry panels currently render empty states. Branch Performance displays
only actual active branch names/counts in the viewer's scope, followed by a statement that
performance metrics are pending. It does not infer financial or attendance performance.
`admin/components/loading_state.html` accepts a message for later asynchronous providers; it is
not shown during today's synchronous empty-state rendering. Its animation respects reduced-motion
preferences.

For a future chart provider (planned with the reporting work around Phase 07), each chart should
receive a series of `(period_start, value)` points, an explicit unit (members, check-ins or ETB),
the branch scope used, the timezone, a retrieval timestamp, and a permission decision. An empty
series must show the existing empty state. Aggregate data before rendering and avoid exposing
other branches in labels, hover text, downloadable data or global totals. No charting dependency
was added for an empty series.
