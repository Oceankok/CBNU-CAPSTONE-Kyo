# W15: optional multi-zone backend contract (partial implementation)

Both `GET /api/events` and `GET /api/events/summary` now accept repeated `zones` query parameters, e.g. `?zones=A&zones=B`. This is an OR union of exact historical event-zone snapshot names. Other filters (date, PPE, status, confidence) intersect with that union. Pagination and summary counts use the same SQL filter.

- Existing single `zone=A` and requests without a zone filter retain their behavior.
- Send either `zone` or `zones`, not both; both produces HTTP400 `ambiguous_event_zone_filter`.
- At most50 supplied `zones` values, each nonempty and at most100 characters. Empty/oversized values produce422. Duplicate names are deduplicated; unknown names match no records. Names are exact, not comma-separated or whitespace-normalized.
- All values are SQL-bound. Current camera reassignment does not change historical snapshot filtering.
- Repository callers may pass `zones=[]` for no filter; HTTP callers must omit both parameters to clear (an empty `zones=` value is invalid). Clear the UI filter by omitting both parameters. Reset pagination offset when changing selected zones. Use the same selection for list and summary requests.
- This adds no schema migration and changes no frontend. Existing frontend needs a multi-select and repeated parameter encoding. PR/deployment status and real UI integration are not verified, so W15 is not complete.
