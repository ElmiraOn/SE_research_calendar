# SE Research Calendar

A Streamlit calendar for software-engineering conference submission deadlines.
Conference home URLs are listed in `conferences.txt`. When the app starts, it
automatically runs the Researchr extractor if a configured conference's cached
CSV is missing or at least 24 hours old. Successful results are stored in
`data/`; if a refresh fails, the previous cached CSV remains available.
Each row is normalized by `deadline_cleaner.py` and includes a `deadline_type`
column. The calendar initially shows only abstract and full-paper submissions;
the **Deadline types** filter can reveal other categories when needed.
Conference names are normalized from their stable URL slugs, and each source
track is mapped to a cross-conference `track_category`. The original website
wording is retained in `track_original` for auditing and future rule updates.

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Use **Refresh deadline data now** in the sidebar to refresh immediately without
waiting for the daily freshness check.

Click a deadline to inspect its source details and open the **Add to calendar**
form. Confirm the date, time, and IANA time zone, then choose Google Calendar or
Outlook. AoE deadlines are prefilled as 23:59 in UTC−12:00; missing source time
zones must be selected explicitly.

## Submit an event

Use **Add Your Event** in the app to choose between a structured single-deadline
form and a bulk CSV upload for multiple tracks or deadlines from one conference.
Every submission is reviewed manually and submission does not guarantee
approval. Accepted Researchr conferences are added to `conferences.txt`;
accepted one-off events are stored in the curated `data/manual_deadlines.csv`
file. See `docs/REVIEWING_EVENTS.md` for the maintainer workflow.
