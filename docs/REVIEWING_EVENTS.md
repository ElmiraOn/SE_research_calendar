# Reviewing event submissions

Event suggestions arrive as GitHub issues created with the **Add your event**
form. GitHub provides the public audit trail and can send repository issue
notifications to the maintainer's email without putting mail credentials in the
Streamlit application.

Single deadlines arrive as structured issue fields. Bulk submissions attach a
completed copy of `templates/event_submission_template.csv`, with one deadline
per row. Reject or request a corrected upload if the header was changed, a date
is not `YYYY-MM-DD`, a time is not `HH:MM`, or a source URL is not official.

## Suggested labels

Create these repository labels once: `event submission`, `needs review`,
`needs information`, `approved`, and `declined`. The issue form automatically
requests the first two when those labels exist.

## Review checklist

1. Confirm that the source is an official conference or track page.
2. Verify the conference, track, deadline type, date, time, and timezone.
3. Check for an extension, grace period, duplicate event, or superseded date.
4. Confirm that the event is relevant to software-engineering research.
5. Add `needs information` and ask a question when evidence is incomplete.
6. Add `declined` and explain why when the event should not be included.

## Accepting a Researchr conference

1. Add its official Researchr home URL to `conferences.txt` if it is not present.
2. Run the extractor or use **Refresh deadline data now** in the app.
3. Compare the generated dates with the submitted authoritative source.
4. Check the normalized `deadline_type` and `track_category` values.
5. Add `approved`, commit the reviewed change, and close the issue from the
   commit or pull request.

## Accepting a manually maintained event

Use this path only when the conference is unsupported by the extractor or the
published deadline cannot be parsed reliably.

1. Append one reviewed row to `data/manual_deadlines.csv`; never edit a
   generated conference cache for a permanent correction.
2. Preserve the submitted source URL and use an unambiguous conference slug.
3. Set `confidence` to `high` only after checking the official source and set
   `needs_review` to `False`.
4. Run `python -m unittest` and `python -m py_compile app.py`.
5. Add `approved`, commit the reviewed change, and close the linked issue.

For higher traceability, make accepted changes through a small pull request that
references the submission issue. Submitters do not need to create the pull
request themselves.
