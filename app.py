# -*- coding: utf-8 -*-
"""
Created on Fri Aug 22 07:50:31 2025

@author: koehn
"""

# app.py
import streamlit as st
import pandas as pd
import requests
import datetime
import re
import os
import json
from collections import defaultdict
import toml # Added for local secrets.toml loading

# --- Page and App Configuration ---

st.set_page_config(
    page_title="CFB Weekly Picks",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="auto"
)

# --- Constants and User Data ---
USERS = {
    "Paul": "pass123", "Weston": "pass123", "Jared": "pass123",
    "Cole": "pass123", "Andy": "pass123", "Krystal": "pass123",
    "Rian": "pass123", "Tucker": "pass123", "Aaron": "pass123",
    "Brayson": "pass123"
}
SCOREBOARD_DB = "scoreboard.json"
PICKS_DB = "all_picks.json"

# --- Helper Functions (with Caching) ---

@st.cache_resource
def load_secrets():
    """
    Loads secrets for the app.
    - First, it tries st.secrets, which is the default for Streamlit Community Cloud.
    - As a fallback for local development, it looks for a 'secrets.toml' file in the root directory.
    """
    if hasattr(st, 'secrets') and st.secrets:
        return st.secrets
    secrets_file_path = "secrets.toml"
    if os.path.exists(secrets_file_path):
        try:
            return toml.load(secrets_file_path)
        except Exception as e:
            st.error(f"Error loading local secrets.toml file: {e}")
            return {}
    return {}

@st.cache_data
def parse_draft_summary(file_path="draft_summary.txt"):
    """Parses the draft summary text file into a dictionary."""
    if not os.path.exists(file_path):
        return {}
    all_picks = {}
    current_user = None
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith("---") and line.endswith("---"):
                user_name = line.replace("---", "").replace("'s Picks", "").strip()
                current_user = user_name
                all_picks[current_user] = []
            elif current_user and line and (line and line[0].isdigit()):
                team_name = ''.join([i for i in line if not i.isdigit()]).lstrip('. ')
                all_picks[current_user].append(team_name)
    return all_picks

def get_current_week():
    """Calculates the current week of the season."""
    season_start_date = datetime.date(2025, 8, 18)
    today = datetime.date.today()
    days_since_start = (today - season_start_date).days
    current_week = (days_since_start // 7) if days_since_start >= 0 else 0
    return min(current_week, 15)

# --- API & Data Fetching Functions ---

def fetch_api_data(endpoint, params):
    """Generic function to fetch data from the collegefootballdata API."""
    app_secrets = load_secrets()
    if "api_key" not in app_secrets:
        st.error("API key not found. Please add it to your secrets.toml file or deployment settings.")
        return None, "API key not configured."
    headers = {'accept': 'application/json', 'Authorization': app_secrets["api_key"]}
    try:
        response = requests.get(f"https://api.collegefootballdata.com/{endpoint}", headers=headers, params=params)
        response.raise_for_status()
        return response.json(), None
    except requests.exceptions.HTTPError as e:
        return None, f"API request failed: {e.response.status_code} - {e.response.text}"
    except requests.exceptions.RequestException as e:
        return None, f"Connection Error: Could not connect to the API. {e}"

@st.cache_data(ttl=3600)
def fetch_game_results(year, week):
    """Fetches game results for a given week and returns a set of winning teams."""
    games_data, error = fetch_api_data("games", {'year': year, 'week': week, 'seasonType': 'regular'})
    if error:
        st.error(error)
        return set()
    if not games_data:
        st.warning(f"No game data found for Week {week}.")
        return set()
    winning_teams = set()
    for game in games_data:
        if game.get('home_points') is not None and game.get('away_points') is not None:
            if game['home_points'] > game['away_points']:
                winning_teams.add(game['home_team'])
            elif game['away_points'] > game['home_points']:
                winning_teams.add(game['away_team'])
    return winning_teams

def fetch_betting_lines(year, week):
    """Fetch betting lines for a specific year and week."""
    lines_data, error = fetch_api_data("lines", {'year': year, 'week': week})
    if error:
        st.error(error)
        return
    if not lines_data:
        st.info(f"No betting lines found for Week {week}.")
        return
    processed_lines = defaultdict(list)
    for game in lines_data:
        home_team_std = str(game['homeTeam']).lower().strip()
        away_team_std = str(game['awayTeam']).lower().strip()
        game_key = frozenset([home_team_std, away_team_std])
        processed_lines[game_key].extend(game['lines'])
    st.session_state.weekly_lines_cache[week] = processed_lines
    st.success(f"Fetched betting lines for {len(lines_data)} games.")

# --- Picks & Scoreboard Logic ---

def save_picks_to_db(username, week, selected_teams):
    """Saves a user's picks for a given week to the JSON database."""
    try:
        with open(PICKS_DB, 'r', encoding='utf-8') as f:
            all_picks = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        all_picks = {}

    if username not in all_picks:
        all_picks[username] = {}

    all_picks[username][str(week)] = selected_teams

    try:
        with open(PICKS_DB, 'w', encoding='utf-8') as f:
            json.dump(all_picks, f, indent=4)
        st.success(f"✅ Successfully saved your {len(selected_teams)} picks for Week {week}!")
    except IOError as e:
        st.error(f"Failed to save picks: {e}")

def load_my_picks(username, week):
    """Loads a specific user's picks for a given week from the JSON DB."""
    try:
        with open(PICKS_DB, 'r', encoding='utf-8') as f:
            all_picks = json.load(f)
        my_weekly_picks = all_picks.get(username, {}).get(str(week), [])
        return my_weekly_picks
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def parse_weekly_picks(week):
    """Parses all user picks for a given week from the JSON database."""
    try:
        with open(PICKS_DB, 'r', encoding='utf-8') as f:
            all_picks = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    weekly_picks = {}
    for user, picks_by_week in all_picks.items():
        if str(week) in picks_by_week:
            weekly_picks[user] = picks_by_week[str(week)]
    return weekly_picks

def update_scoreboard(week, year):
    """Calculates scores for a week and updates the scoreboard JSON file."""
    with st.spinner(f"Fetching winners and calculating scores for Week {week}..."):
        winning_teams = fetch_game_results(year, week)
        if not winning_teams:
            st.warning(f"Could not update scores for Week {week} as no game results were found.")
            return

        user_picks = parse_weekly_picks(week)
        if not user_picks:
            st.warning(f"No user picks have been submitted for Week {week}.")
            return

        try:
            with open(SCOREBOARD_DB, 'r') as f:
                scoreboard = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            scoreboard = {}

        for user, picks in user_picks.items():
            wins = sum(1 for team in picks if team in winning_teams)
            if user not in scoreboard:
                scoreboard[user] = {}
            scoreboard[user][str(week)] = wins
            st.toast(f"Logged {wins} wins for {user} in Week {week}.", icon="✅")

        try:
            with open(SCOREBOARD_DB, 'w') as f:
                json.dump(scoreboard, f, indent=4)
            st.success(f"Scoreboard successfully updated for Week {week}!")
        except IOError as e:
            st.error(f"Failed to save scoreboard: {e}")

# --- UI Component Functions ---

def display_login_form():
    """Displays the login form in the center of the page."""
    st.header("🏈 College Football Weekly Picks")
    with st.form("login_form"):
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Login")
        if submitted:
            if username in USERS and USERS[username] == password:
                st.session_state.logged_in = True
                st.session_state.username = username
                st.rerun()
            else:
                st.error("Invalid username or password.")

def display_scoreboard():
    """Loads scoreboard data and displays it in a table."""
    st.header("🏆 Overall Standings")
    try:
        with open(SCOREBOARD_DB, 'r') as f:
            scoreboard_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st.info("Scoreboard is empty. Update scores for a past week to begin.")
        return

    if not scoreboard_data:
        st.info("Scoreboard contains no data. Please update a week's scores.")
        return

    df = pd.DataFrame.from_dict(scoreboard_data, orient='index').fillna(0)
    week_cols = sorted([col for col in df.columns if col.isdigit()], key=int)

    if not week_cols:
        st.warning("No weekly win data found in the scoreboard.")
        return

    df['Total Wins'] = df[week_cols].sum(axis=1)
    df = df.sort_values(by='Total Wins', ascending=False)
    display_cols = week_cols + ['Total Wins']
    df = df[display_cols]
    df[week_cols] = df[week_cols].astype(int)
    df['Total Wins'] = df['Total Wins'].astype(int)

    st.dataframe(df, use_container_width=True)

def display_weekly_results(week):
    """Shows all user picks and their results for a given scored week."""
    st.subheader(f"Picks & Results for Week {week}")
    year = datetime.datetime.now().year
    winning_teams = fetch_game_results(year, week)
    all_user_picks = parse_weekly_picks(week)

    if not all_user_picks:
        st.info("No picks were submitted for this week.")
        return

    users = sorted(all_user_picks.keys())
    cols = st.columns(len(users))

    for idx, user in enumerate(users):
        with cols[idx]:
            st.markdown(f"**{user}**")
            picks = all_user_picks.get(user, [])
            if not picks:
                st.write("No picks.")
                continue

            results_data = []
            for pick in sorted(picks):
                is_winner = pick in winning_teams
                results_data.append({'Pick': pick, 'Result': '✅ Win' if is_winner else '❌ Loss'})
            
            st.dataframe(pd.DataFrame(results_data), hide_index=True, use_container_width=True)

@st.dialog("Returning Player Analytics")
def display_analytics():
    # ... (function content unchanged)
    st.subheader(f"Returning Production for {st.session_state.username}'s Teams")
    try:
        df = pd.read_csv("returning_players_2025.csv")
        my_teams = st.session_state.my_teams
        drafted_teams_std = [str(team).lower().strip() for team in my_teams]
        df['team_standardized'] = df['team'].astype(str).str.lower().str.strip()
        user_teams_df = df[df['team_standardized'].isin(drafted_teams_std)].drop(columns=['team_standardized'])

        if user_teams_df.empty:
            st.warning("No analytics data found for your drafted teams.")
            return

        for col in user_teams_df.columns:
            if 'percent' in col.lower() or 'usage' in col.lower():
                 if pd.api.types.is_numeric_dtype(user_teams_df[col]):
                     user_teams_df[col] = user_teams_df[col].apply(lambda x: f"{x*100:.2f}%" if pd.notna(x) else "N/A")

        st.dataframe(user_teams_df, use_container_width=True, hide_index=True)
    except FileNotFoundError:
        st.error("Error: 'returning_players_2025.csv' not found.")
    except Exception as e:
        st.error(f"An error occurred: {e}")

# --- Main Application Logic ---

def main_app():
    """The main application interface shown after a successful login."""
    with st.sidebar:
        st.header(f"🏈 Welcome, {st.session_state.username}!")
        st.write("Your Drafted Teams:")
        st.dataframe(st.session_state.my_teams, hide_index=True, use_container_width=True, column_config={0:"Team"})
        if st.button("📊 View Team Analytics", use_container_width=True):
            display_analytics()
        st.divider()
        st.button("Logout", on_click=lambda: st.session_state.clear(), use_container_width=True)

    tab1, tab2 = st.tabs(["Weekly Picks", "🏆 Scoreboard"])

    with tab1:
        st.title("Weekly Picks Selection")
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.session_state.week_selection = st.selectbox(
                "Select Week",
                options=[f"Week {i}" for i in range(16)],
                index=get_current_week()
            )
        current_week = int(st.session_state.week_selection.split(" ")[1])
        current_year = datetime.datetime.now().year
        with col2:
            if st.button("📡 Fetch Betting Lines", use_container_width=True):
                with st.spinner(f"Fetching lines for Week {current_week}..."):
                    fetch_betting_lines(current_year, current_week)

        # Load any picks previously saved by the current user for this week
        my_saved_picks = load_my_picks(st.session_state.username, current_week)

        try:
            schedule_df = pd.read_csv(f"2025_week_{current_week}.csv")
            matchups = {row['homeTeam']: row['awayTeam'] for _, row in schedule_df.iterrows()}
            matchups.update({row['awayTeam']: row['homeTeam'] for _, row in schedule_df.iterrows()})
        except FileNotFoundError:
            st.warning(f"Schedule file '2025_week_{current_week}.csv' not found.")
            schedule_df = pd.DataFrame()
            matchups = {}

        picks_data = []
        for team in st.session_state.my_teams:
            opponent = matchups.get(team, "BYE WEEK")
            # Pre-select checkboxes if the team is in the user's saved picks
            picks_data.append({
                "Select": team in my_saved_picks,
                "My Team": team,
                "Opponent": opponent
            })
        picks_df = pd.DataFrame(picks_data)

        st.subheader(f"Your Matchups for Week {current_week}")

        # ... (Betting Lines display logic is unchanged)
        lines_cache = st.session_state.weekly_lines_cache.get(current_week)
        if lines_cache and not schedule_df.empty:
            lines_to_display = []
            user_games_df = schedule_df[schedule_df['homeTeam'].isin(st.session_state.my_teams) | schedule_df['awayTeam'].isin(st.session_state.my_teams)]
            for _, game in user_games_df.iterrows():
                home_team, away_team = game['homeTeam'], game['awayTeam']
                game_key = frozenset([str(home_team).lower().strip(), str(away_team).lower().strip()])
                game_lines = lines_cache.get(game_key)
                if game_lines:
                    line = game_lines[0]
                    lines_to_display.append({"Home Team": home_team, "Away Team": away_team, "Spread": line.get('formattedSpread', 'N/A'), "Over/Under": line.get('overUnder', 'N/A')})
            if lines_to_display:
                st.markdown("##### Betting Lines Overview")
                st.dataframe(pd.DataFrame(lines_to_display), hide_index=True, use_container_width=True)
                st.divider()

        if not picks_df.empty:
            edited_df = st.data_editor(picks_df, column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)}, disabled=["My Team", "Opponent"], hide_index=True, use_container_width=True, key=f"picks_editor_{current_week}")
            selected_teams = edited_df[edited_df["Select"]]["My Team"].tolist()
            
            if st.button(f"Submit Picks for Week {current_week}", use_container_width=True, type="primary"):
                num_picks = len(selected_teams)
                if current_week > 0 and num_picks != 6 and num_picks > 0:
                     st.warning(f"⚠️ The standard is 6 picks, but you have submitted **{num_picks}**. Please confirm this is correct.")
                save_picks_to_db(st.session_state.username, current_week, selected_teams)

    with tab2:
        st.title("League Scoreboard")
        st.subheader("Update Weekly Scores")
        st.markdown("Select a completed week and click the button to fetch game results and update the standings.")
        max_week = get_current_week()
        week_to_update = st.selectbox("Select week to update scores", options=range(max_week + 1), index=max_week, disabled=(max_week < 0))
        if st.button(f"Calculate & Update Scores for Week {week_to_update}", type="primary", disabled=(max_week < 0)):
            current_year = datetime.datetime.now().year
            update_scoreboard(week_to_update, current_year)
            st.rerun()
        st.divider()
        display_scoreboard()

        # NEW: Section to review all picks for a scored week
        st.divider()
        st.header("🔍 Review Weekly Picks")
        try:
            with open(SCOREBOARD_DB, 'r') as f:
                scoreboard_data = json.load(f)
            scored_weeks = set()
            for user_scores in scoreboard_data.values():
                scored_weeks.update(user_scores.keys())
            
            if scored_weeks:
                sorted_weeks = sorted([int(w) for w in scored_weeks], reverse=True)
                week_to_review = st.selectbox("Select a scored week to see all picks", options=sorted_weeks)
                display_weekly_results(week_to_review)
            else:
                st.info("No weeks have been scored yet. Update a week's scores to review picks.")
        except (FileNotFoundError, json.JSONDecodeError):
            st.info("Scoreboard is empty. No picks to review.")

# --- App Initialization and State Management ---

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'weekly_lines_cache' not in st.session_state:
    st.session_state.weekly_lines_cache = {}

# --- Main Render Logic ---

if st.session_state.logged_in:
    if 'my_teams' not in st.session_state:
        all_picks = parse_draft_summary()
        st.session_state.my_teams = all_picks.get(st.session_state.username, [])
    main_app()
else:
    display_login_form()
