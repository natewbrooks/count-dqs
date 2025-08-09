import os
import re
import json
import requests
import streamlit as st
from urllib.parse import urlparse
from dotenv import load_dotenv

API_URL = "https://api.start.gg/gql/alpha"

QUERY_EVENT_ENTRANTS = """
query EventEntrants($eventId: ID!, $page: Int!, $perPage: Int!) {
  event(id: $eventId) {
    id
    name
    slug
    entrants(query: {page: $page, perPage: $perPage}) {
      pageInfo { total totalPages }
      nodes { id name isDisqualified participants { id gamerTag prefix } }
    }
  }
}
"""

RESOLVE_EVENT_ID = """
query GetEventId($slug: String!) { event(slug: $slug) { id name slug } }
"""

def parse_event_input(s: str):
    s = s.strip()
    if re.fullmatch(r"\d+", s):
        return int(s), None
    # Expect .../tournament/<tournament-slug>/event/<event-slug>
    path = urlparse(s).path.strip("/")
    if "/event/" in f"/{path}/" and "tournament/" in f"/{path}/":
        # start.gg accepts the full path starting at "tournament/..."
        slug = path[path.index("tournament"):]
        return ("slug", slug), None
    return None, "Enter a numeric Event ID or a valid Event URL."

def gql(session, token, query, variables):
    r = session.post(
        API_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data["data"]

def resolve_event_id(session, token, parsed):
    if isinstance(parsed, tuple) and parsed[0] == "slug":
        data = gql(session, token, RESOLVE_EVENT_ID, {"slug": parsed[1]})
        ev = data.get("event")
        if not ev:
            raise RuntimeError("Event not found for that URL.")
        return int(ev["id"]), ev["name"], ev["slug"]
    return int(parsed), None, None

def fetch_counts(session, token, event_id, per_page=500):
    page = 1
    total_pages = 1
    total_players = 0
    dq_count = 0
    dq_rows = []
    event_name = None
    event_slug = None

    while page <= total_pages:
        data = gql(session, token, QUERY_EVENT_ENTRANTS, {
            "eventId": event_id, "page": page, "perPage": per_page
        })
        ev = data["event"]
        if event_name is None:
            event_name, event_slug = ev.get("name"), ev.get("slug")

        entrants = ev["entrants"]
        if page == 1:
            total_players = entrants["pageInfo"]["total"] or 0
        total_pages = entrants["pageInfo"]["totalPages"] or 1

        for n in entrants["nodes"]:
            if n.get("isDisqualified"):
                tags = []
                for p in (n.get("participants") or []):
                    tag = p.get("gamerTag") or ""
                    pref = p.get("prefix")
                    tags.append(f"{pref + ' | ' if pref else ''}{tag}".strip())
                dq_rows.append({
                    "Entrant Name": n.get("name"),
                    "Participants": ", ".join([t for t in tags if t]),
                    "Entrant ID": n.get("id"),
                })
                dq_count += 1
        page += 1

    return {
        "event_id": event_id,
        "event_name": event_name,
        "event_slug": event_slug,
        "total_players": total_players,
        "dq_count": dq_count,
        "dq_rows": dq_rows
    }

# ------------------------- UI -------------------------

st.set_page_config(page_title="start.gg Event DQ Counter", page_icon="✅", layout="centered")

st.title("start.gg Event DQ Counter")
st.caption("Enter an Event URL or Event ID. Uses GraphQL `event -> entrants` and `Entrant.isDisqualified`.")

with st.sidebar:
    load_dotenv()
    default_token = os.getenv("START_GG_TOKEN", "")
    token = st.text_input("API Token (Bearer)", type="password", value=default_token, help="From start.gg developer settings.")
    st.markdown("---")
    st.markdown("**Tips**")
    st.markdown("- Example URL: `https://www.start.gg/tournament/<slug>/event/<slug>`\n- You can set `START_GG_TOKEN` in a `.env` file to prefill.")

event_input = st.text_input("Event URL or numeric Event ID", placeholder="https://www.start.gg/tournament/.../event/...")
run_btn = st.button("Count DQs", type="primary")

if run_btn:
    if not token:
        st.error("Provide an API token.")
        st.stop()

    parsed, err = parse_event_input(event_input)
    if err:
        st.error(err)
        st.stop()

    try:
        with requests.Session() as s:
            event_id, ev_name, ev_slug = resolve_event_id(s, token, parsed)
            with st.spinner("Fetching entrants…"):
                result = fetch_counts(s, token, event_id)

        # KPIs
        left, right = st.columns(2)
        left.metric("Total Players (summary)", result["total_players"])
        right.metric("Disqualifications", result["dq_count"])

        # Meta
        st.subheader(result["event_name"] or ev_name or "Event")
        st.caption(f"Event ID: {result['event_id']} • Slug: {result['event_slug'] or ev_slug}")

        # Table
        if result["dq_rows"]:
            st.markdown("### DQ Entrants")
            st.dataframe(result["dq_rows"], use_container_width=True)

            # CSV export
            import pandas as pd
            df = pd.DataFrame(result["dq_rows"])
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV", csv, file_name="dq_entrants.csv", mime="text/csv")
        else:
            st.info("No disqualifications found for this event.")

    except requests.HTTPError as e:
        st.error(f"HTTP error: {e}")
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")
