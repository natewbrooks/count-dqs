import os, re, sys, math, json, requests
from urllib.parse import urlparse
from dotenv import load_dotenv

API_URL = "https://api.start.gg/gql/alpha"

QUERY = """
query EventEntrants($eventId: ID!, $page: Int!, $perPage: Int!) {
  event(id: $eventId) {
    id
    name
    slug
    entrants(query: {page: $page, perPage: $perPage}) {
      pageInfo { total totalPages }
      nodes {
        id
        name
        isDisqualified
      }
    }
  }
}
"""

def parse_event_id(input_str: str):
    # numeric ID
    if re.fullmatch(r"\d+", input_str):
        return int(input_str)
    # URL -> slug -> we’ll resolve slug to id using the same field
    # start.gg supports querying by slug, but we’ll fetch id with a separate call
    # If you prefer slug directly, swap to event(slug: "tournament/.../event/...").
    path = urlparse(input_str).path.strip("/")
    # Expect ".../tournament/<slug>/event/<slug>" shape
    if "/event/" in f"/{path}/":
        return ("slug", path[path.index("tournament"):])  # pass slug string back
    raise ValueError("Provide an event ID or event URL, e.g. https://www.start.gg/tournament/.../event/...")

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

# tiny helper to resolve slug -> event id once
RESOLVE_EVENT_ID_QUERY = """
query GetEventId($slug: String!) {
  event(slug: $slug) { id name slug }
}
"""

def resolve_event_id(session, token, maybe):
    if isinstance(maybe, tuple) and maybe[0] == "slug":
        slug = maybe[1]
        data = gql(session, token, RESOLVE_EVENT_ID_QUERY, {"slug": slug})
        ev = data.get("event")
        if not ev: raise RuntimeError("Event not found for slug.")
        return int(ev["id"]), ev["name"], ev["slug"]
    return int(maybe), None, None

def main():
    if len(sys.argv) != 2:
        print("Usage: python event_summary.py <eventId | eventUrl>", file=sys.stderr)
        sys.exit(2)

    load_dotenv()
    token = os.getenv("START_GG_TOKEN")
    if not token:
        print("Missing START_GG_TOKEN in env/.env", file=sys.stderr)
        sys.exit(2)

    input_arg = sys.argv[1]
    maybe = parse_event_id(input_arg)

    with requests.Session() as s:
        event_id, event_name, event_slug = resolve_event_id(s, token, maybe)

        per_page = 500
        page = 1
        total_pages = 1

        total_players = 0
        dq_count = 0

        # First page also gives us pageInfo.total == total players
        while page <= total_pages:
            data = gql(s, token, QUERY, {"eventId": event_id, "page": page, "perPage": per_page})
            ev = data["event"]
            # capture friendly meta if not from resolver
            if event_name is None:
                event_name, event_slug = ev.get("name"), ev.get("slug")

            entrants = ev["entrants"]
            total_pages = entrants["pageInfo"]["totalPages"] or 1
            if page == 1:
                total_players = entrants["pageInfo"]["total"] or 0  # “summary” count

            for node in entrants["nodes"]:
                if node.get("isDisqualified") is True:
                    dq_count += 1
            page += 1

    print(f"Event: {event_name} ({event_slug})")
    print(f"Event ID: {event_id}")
    print(f"Total players (summary): {total_players}")
    print(f"Disqualifications: {dq_count}")

if __name__ == "__main__":
    main()
