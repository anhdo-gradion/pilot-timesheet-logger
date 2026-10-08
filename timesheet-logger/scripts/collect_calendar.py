#!/usr/bin/env python3
"""
[Script Step] collect_calendar.py
Deterministic Google Calendar event extractor using Google Calendar REST API v3 directly.
Fetches and compacts scheduled workday events via Google Calendar REST API v3.
"""

import os
import json
import re
import html
import urllib.request
import urllib.parse
import urllib.error
import argparse
from datetime import datetime, date, time, timezone, timedelta

def get_credentials_paths():
    """Returns candidate paths for credentials.json and token.json."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root_dir = os.path.dirname(base_dir)
    cred_candidates = [
        os.path.join(root_dir, "credentials.json"),
        os.path.join(base_dir, "credentials.json"),
        os.path.join(os.getcwd(), "credentials.json")
    ]
    token_candidates = [
        os.path.join(root_dir, "token.json"),
        os.path.join(base_dir, "token.json"),
        os.path.join(os.getcwd(), "token.json")
    ]

    cred_path = next((p for p in cred_candidates if os.path.exists(p) and os.path.getsize(p) > 0), cred_candidates[0])
    token_path = next((p for p in token_candidates if os.path.exists(p) and os.path.getsize(p) > 0), token_candidates[0])
    return cred_path, token_path

def get_access_token_via_rest():
    """Obtains a valid access token by refreshing via Google OAuth2 REST endpoint."""
    cred_path, token_path = get_credentials_paths()
    if not os.path.exists(token_path) or os.path.getsize(token_path) == 0:
        return None, f"Token file not found or empty at {token_path}"

    try:
        with open(token_path, "r", encoding="utf-8") as f:
            tok_data = json.load(f)
    except Exception as e:
        return None, f"Failed to parse token file: {e}"

    refresh_token = tok_data.get("refresh_token")
    client_id = tok_data.get("client_id")
    client_secret = tok_data.get("client_secret")

    # If client_id / client_secret are missing from token.json, pull them from credentials.json
    if (not client_id or not client_secret) and os.path.exists(cred_path):
        try:
            with open(cred_path, "r", encoding="utf-8") as cf:
                cd = json.load(cf)
            cinfo = cd.get("installed") or cd.get("web") or {}
            client_id = cinfo.get("client_id")
            client_secret = cinfo.get("client_secret")
            tok_data["client_id"] = client_id
            tok_data["client_secret"] = client_secret
        except Exception:
            pass

    if refresh_token and client_id and client_secret:
        try:
            # Direct REST API call to Google OAuth2 token endpoint
            refresh_url = "https://oauth2.googleapis.com/token"
            payload = urllib.parse.urlencode({
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }).encode("utf-8")
            req = urllib.request.Request(
                refresh_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                new_access_token = res_data.get("access_token")
                tok_data["access_token"] = new_access_token
                tok_data["token"] = new_access_token
                try:
                    with open(token_path, "w", encoding="utf-8") as tf:
                        json.dump(tok_data, tf, indent=2)
                except Exception:
                    pass
                return new_access_token, None
        except Exception as e:
            # Fall back to existing access token if refresh fails
            if tok_data.get("access_token") or tok_data.get("token"):
                return tok_data.get("access_token") or tok_data.get("token"), None
            return None, f"OAuth2 token refresh REST API failed: {e}"

    access_token = tok_data.get("access_token") or tok_data.get("token")
    if access_token:
        return access_token, None
    return None, "No valid access token or refresh token available."

def ensure_google_auth():
    """Open Google's installed-app OAuth flow in a browser when no usable token exists."""
    access_token, _ = get_access_token_via_rest()
    if access_token:
        request = urllib.request.Request(
            "https://www.googleapis.com/calendar/v3/calendars/primary",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise RuntimeError(f"Google Calendar validation failed (HTTP {exc.code}).") from exc

    cred_path, token_path = get_credentials_paths()
    if not os.path.exists(cred_path):
        raise RuntimeError(f"Google OAuth credentials file is required at {cred_path}.")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install google-auth-oauthlib to authenticate Google Calendar in a browser.") from exc

    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    flow = InstalledAppFlow.from_client_secrets_file(cred_path, scopes)
    credentials = flow.run_local_server(port=0, open_browser=True)
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as token_file:
        token_file.write(credentials.to_json())
    return True

def collect_today_events(target_date=None, tz_offset_hours=7, calendar_id="primary", max_results=250):
    """
    Connects to Google Calendar REST API v3 directly and fetches all events for the target day.
    Returns a list of dicts: [{"title": ..., "start": "HH:MM", "end": "HH:MM", "desc": ...}].
    """
    if isinstance(target_date, str) and target_date.strip():
        try:
            target_date = datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            target_date = None
    if not isinstance(target_date, date):
        target_date = date.today()

    access_token, err = get_access_token_via_rest()
    if not access_token:
        return {
            "available": False,
            "error": err or "Authentication failed.",
            "events": []
        }

    try:
        # Local start of day and end of day in ISO 8601
        local_tz = timezone(timedelta(hours=float(tz_offset_hours)))
        dt_start = datetime.combine(target_date, time.min, tzinfo=local_tz)
        dt_end = datetime.combine(target_date, time.max, tzinfo=local_tz)

        params = {
            "timeMin": dt_start.isoformat(),
            "timeMax": dt_end.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": int(max_results)
        }
        # Direct REST API HTTP request to Google Calendar API v3
        url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(calendar_id, safe='@')}/events?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        })

        with urllib.request.urlopen(req, timeout=12) as resp:
            events_result = json.loads(resp.read().decode("utf-8"))

        raw_items = events_result.get('items', [])
        events = []

        for item in raw_items:
            if item.get('status') == 'cancelled':
                continue

            summary = item.get('summary', '(Untitled event)')
            raw_desc = item.get('description', '')

            # Clean and normalize description to preserve token budget
            clean_desc = ""
            if raw_desc:
                text = re.sub(r'<[^>]+>', ' ', raw_desc)
                text = html.unescape(text)
                text = ' '.join(text.split()).strip()
                if len(text) > 120:
                    text = text[:117] + "..."
                clean_desc = text

            start_raw = item.get('start', {}).get('dateTime') or item.get('start', {}).get('date', '')
            end_raw = item.get('end', {}).get('dateTime') or item.get('end', {}).get('date', '')
            all_day = "dateTime" not in item.get("start", {})

            # Extract HH:MM
            start_time = "09:00"
            end_time = "10:00"
            if "T" in start_raw:
                try:
                    start_time = start_raw.split("T")[1][:5]
                except Exception:
                    pass
            if "T" in end_raw:
                try:
                    end_time = end_raw.split("T")[1][:5]
                except Exception:
                    pass

            event_entry = {
                "title": summary,
                "start": start_time,
                "end": end_time,
                "all_day": all_day
            }
            if clean_desc:
                event_entry["desc"] = clean_desc

            events.append(event_entry)

        return {
            "available": True,
            "events": events
        }

    except Exception as e:
        return {
            "available": False,
            "error": f"Calendar REST API request failed: {e}",
            "events": []
        }

def main():
    parser = argparse.ArgumentParser(description="Collect Google Calendar events for a date.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--timezone-offset", type=float, default=7, help="Timezone UTC offset in hours")
    parser.add_argument("--calendar-id", default="primary")
    parser.add_argument("--max-results", type=int, default=250)
    args = parser.parse_args()
    result = collect_today_events(args.date, args.timezone_offset, args.calendar_id, args.max_results)
    print(json.dumps(result, separators=(',', ':')))

if __name__ == "__main__":
    main()
